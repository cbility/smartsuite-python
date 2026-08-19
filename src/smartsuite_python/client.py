# region imports

import time
import logging
from collections import deque
from dataclasses import dataclass, asdict
from typing import Any, Literal, TypedDict, cast

import requests

# endregion imports

# region configuration

log = logging.getLogger("smartsuite_python")
log.addHandler(
    logging.NullHandler()
)  # avoid defaults being applied if logging is not configured in importing application

# endregion configuration

# region helpers


class _LimitedList:
    """Tracks the timestamps of the most recent N requests for rate limiting."""

    def __init__(self, max_length: int):
        self._max_length = max_length
        self._items: deque[float] = deque(maxlen=max_length)

    def add(self, item: float) -> None:
        self._items.appendleft(item)

    def get_oldest(self) -> float | None:
        """Return the oldest (last) item, or None if the list isn't full yet."""
        if len(self._items) < self._max_length:
            return None
        return self._items[-1]


def _split_into_batches(batch_size: int, items: list) -> list[list]:
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


# endregion helpers

# region types

FilterComparison = Literal[
    # string
    "is",
    "is_not",
    "is_empty",
    "is_not_empty",
    "contains",
    "not_contains",
    # number
    "is_equal_to",
    "is_not_equal_to",
    "is_greater_than",
    "is_less_than",
    "is_equal_or_greater_than",
    "is_equal_or_less_than",
    # select
    "is_any_of",
    "is_none_of",
    "has_any_of",
    "has_all_of",
    "is_exactly",
    "has_none_of",
    # date
    "is_before",
    "is_on_or_before",
    "is_on_or_after",
    # due date
    "is_overdue",
    "is_not_overdue",
    # files
    "file_name_contains",
    "file_type_is",
]


FilterDateMode = Literal[
    "today",
    "yesterday",
    "one_week_ago",
    "one_week_from_now",
    "one_month_ago",
    "one_month_from_now",
    "one_year_ago",
    "one_year_from_now",
    "next_number_of_days",
    "past_number_of_days",
    "date_range",
    "exact_date",
]


@dataclass
class FilterDateValue:
    """Value used when filtering date and due-date fields."""

    date_mode: FilterDateMode
    date_mode_value: str | int | list[str]


# Define FilterValue after FilterDateValue is defined
FilterValue = str | int | float | bool | list[str] | FilterDateValue | None


@dataclass
class FilterElement:
    """One SmartSuite record filter.

    ``field`` is the field slug. ``comparison`` must be supported by that
    field's type, and ``value`` is a scalar, a list of strings, ``None`` for
    empty checks, or a :class:`FilterDateValue` for date comparisons.
    """

    field: str
    comparison: FilterComparison
    value: FilterValue


@dataclass
class _FilterBody:
    operator: Literal["and", "or"]
    fields: list[FilterElement]


@dataclass
class FilterBody:
    filter: _FilterBody


@dataclass
class SortElement:
    field: str
    direction: Literal["asc", "desc"]


# Keep as TypedDict for response handling
class BulkRequestResponse(TypedDict):
    items: list[dict]


# endregion types

# region main class


class SmartSuiteClient:
    """A Python client for the SmartSuite API.

    Handles authentication, rate limiting, and provides convenient methods for common operations.

    Args:
        account_id: SmartSuite account ID (Account-Id header value).
        api_token:  SmartSuite API token.
        max_requests_per_second: maximum requests per second made by the client.
            Set by default to 2 to allow the client to avoid rate limiting up to 125% of the configured limit for your plan.
            Set this to 5 to allow more frequent requests, but note that rate limiting will occur earlier.
            See https://help.smartsuite.com/en/articles/4759983-smartsuite-limits and https://developers.smartsuite.com/docs/rate-limits.
        request_timeout: maximum number of seconds to wait for an HTTP response before
            raising a requests timeout exception. Defaults to 30 seconds.
    """

    # region initialisation

    base_url = "https://app.smartsuite.com/api/v1"
    max_bulk_request_size = 25

    def __init__(
        self,
        account_id: str,
        api_token: str,
        max_requests_per_second: int = 2,
        request_timeout: float = 30.0,
    ) -> None:
        if max_requests_per_second < 1:
            raise ValueError("max_requests_per_second must be at least 1")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be greater than 0")

        self._account_id = account_id
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {api_token}",
                "Account-Id": account_id,
                "Content-Type": "application/json;charset=utf-8",
            }
        )
        self.max_requests_per_second = max_requests_per_second
        self.request_timeout = request_timeout
        self._recent_request_timestamps = _LimitedList(self.max_requests_per_second)

    # endregion initialisation

    # region request method

    def request(
        self,
        endpoint: str,
        method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"] = "GET",
        body: Any | None = None,
        max_retries: int = 3,
        initial_retry_delay: float = 30.0,
    ) -> requests.Response:
        """Make an authenticated request with rate limiting and 429 retry logic.

        Args:
            endpoint: Full URL to request.
            method:   HTTP method (GET, POST, PATCH, PUT or DELETE).
            body:     JSON-serialisable body.
            retries:  Maximum number of retry attempts on 429.
            delay:    Initial retry delay in seconds (doubles on each retry).
        """
        attempts = 0
        current_delay = initial_retry_delay

        while True:
            # handle rate limiting
            oldest_ts = self._recent_request_timestamps.get_oldest()
            if oldest_ts is not None:
                elapsed = time.time() - oldest_ts
                if elapsed < 1.0:
                    wait = 1.001 - elapsed
                    log.debug("Avoiding rate limiting: waiting %.3f s", wait)
                    time.sleep(wait)
            try:
                # record request timestamp to avoid future rate limiting
                self._recent_request_timestamps.add(time.time())
                response = self._session.request(
                    method, endpoint, json=body, timeout=self.request_timeout
                )
                response.raise_for_status()
                return response
            except requests.exceptions.HTTPError as err:
                # backoff and retry for rate limit errors
                status_code = getattr(err.response, "status_code", None)
                error_text = getattr(err.response, "text", "")
                if status_code == 429:
                    attempts += 1
                    if attempts <= max_retries:
                        log.warning(
                            "Attempt %d failed with 429. Retrying in %.0f s…",
                            attempts,
                            current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= 2
                        continue
                    else:
                        log.error(
                            f"{max_retries} retries failed due to rate limiting - are you making too many requests in parallel? See https://developers.smartsuite.com/docs/rate-limits."
                        )
                        raise  # re-raise when retries exceeded
                log.error(
                    f"SmartSuite returned a {status_code} error with the following content: '{error_text}'"
                )
                raise  # re-raise for non rate limiting errors

    # endregion request method

    # region get records

    def get_all_records(self, table_id: str) -> list[dict]:
        """Get all records for a particular table"""
        url = f"{self.base_url}/applications/{table_id}/records/list/"
        response = self.request(url, method="POST", body={})
        return response.json()["items"]

    def filter_records(
        self,
        table_id: str,
        fields_to_filter: list[FilterElement],
        operator: Literal["and", "or"] = "and",
    ) -> list[dict]:
        """Get records matching one or more field filters.

        Each filter has a field slug, a comparison supported by that field's
        type, and a comparison value. For example::

            client.filter_records(
                table_id="table-id",
                fields_to_filter=[
                    FilterElement(field="status", comparison="is", value="Complete"),
                ],
            )

        Pass a list of filter objects to combine multiple conditions with
        ``operator``. Date filters use ``FilterDateValue`` for their value.
        """
        # Convert dataclasses to dicts for JSON serialization
        fields_as_dicts = [asdict(f) for f in fields_to_filter]
        body = {"filter": {"operator": operator, "fields": fields_as_dicts}}
        url = f"{self.base_url}/applications/{table_id}/records/list/"
        response = self.request(url, method="POST", body=body)
        return response.json()["items"]

    def get_records_by_field_values(
        self, table_id: str, field_slug: str, field_values: list
    ) -> list[dict]:
        fields = [
            FilterElement(field=field_slug, comparison="is", value=v)
            for v in field_values
        ]
        body = {"filter": {"operator": "or", "fields": [asdict(f) for f in fields]}}
        url = f"{self.base_url}/applications/{table_id}/records/list/"
        response = self.request(url, method="POST", body=body)
        return response.json()["items"]

    def get_records_by_title(self, table_id: str, titles: list[str]) -> list[dict]:
        fields = [
            FilterElement(field="title", comparison="is", value=t) for t in titles
        ]
        body = {"filter": {"operator": "or", "fields": [asdict(f) for f in fields]}}
        url = f"{self.base_url}/applications/{table_id}/records/list/"
        response = self.request(url, method="POST", body=body)
        return response.json()["items"]

    # endregion

    # region update records

    def _update_request(
        self,
        table_id: str,
        record_id: str,
        record: dict,
        method: Literal["PATCH", "PUT"],
    ):
        url = f"{self.base_url}/applications/{table_id}/records/{record_id}/"
        response = self.request(url, method=method, body=record)
        return response.json()

    def update_record(self, table_id: str, record_id: str, record: dict) -> dict:
        """Update a single record, keeping existing field values."""
        return self._update_request(table_id, record_id, record, "PATCH")

    def replace_record(self, table_id: str, record_id: str, record: dict) -> dict:
        """Destructively update a single record, removing existing field values."""
        return self._update_request(table_id, record_id, record, "PUT")

    def _bulk_update_request(
        self, table_id: str, records: list[dict], method: Literal["PATCH", "PUT"]
    ) -> BulkRequestResponse:
        """Bulk-update records in batches according to the maximum allowed updates per request.

        Args:
            table_id:               Target application/table ID.
            records:                Records to update (must include ``id``).
            method:                 Either "PATCH" for non-destructure updates or "PUT" for destructive updates.
        """
        if any(record.get("id") is None for record in records):
            raise ValueError("All bulk update records must have an 'id' field.")

        log.debug("Bulk updating %d records", len(records))
        if not records:
            return {"items": []}

        url = f"{self.base_url}/applications/{table_id}/records/bulk/"
        updated_records: list[dict] = []
        batches = _split_into_batches(self.max_bulk_request_size, records)
        log.debug("Split into %d batch(es)", len(batches))

        for i, batch in enumerate(batches, 1):
            log.debug("Processing batch %d / %d", i, len(batches))
            response = self.request(url, method=method, body={"items": batch})
            result = cast(BulkRequestResponse, response.json())
            updated_records.extend(result["items"])

        return {"items": updated_records}

    def bulk_update_records(
        self,
        table_id: str,
        records: list[dict],
    ) -> BulkRequestResponse:
        """Bulk-update records in batches according to the maximum allowed updates per request. Performs a non-destructive update.

        Args:
            table_id:               Target application/table ID.
            records:                Records to update (must include `id`).
        """
        return self._bulk_update_request(
            table_id=table_id, records=records, method="PATCH"
        )

    def bulk_replace_records(
        self,
        table_id: str,
        records: list[dict],
    ) -> BulkRequestResponse:
        """Bulk-update records in batches according to the maximum allowed updates per request. Performs a destructive update.

        Args:
            table_id:               Target application/table ID.
            records:                Records to update (must include `id`).
        """
        return self._bulk_update_request(
            table_id=table_id, records=records, method="PUT"
        )

    # endregion update records

    # region add records
    def add_new_record(self, table_id: str, record: dict) -> dict:
        url = f"{self.base_url}/applications/{table_id}/records/"
        response = self.request(url, method="POST", body=record)
        return response.json()

    def bulk_add_new_records(
        self, table_id: str, records: list[dict]
    ) -> BulkRequestResponse:
        url = f"{self.base_url}/applications/{table_id}/records/bulk/"
        new_records: list[dict] = []
        for batch in _split_into_batches(self.max_bulk_request_size, records):
            response = self.request(url, method="POST", body={"items": batch})
            result = cast(BulkRequestResponse, response.json())
            new_records.extend(result["items"])
        return {"items": new_records}

    # endregion add records

    # region workspace

    def get_table(self, table_id: str) -> dict:
        url = f"{self.base_url}/applications/{table_id}/"
        return self.request(url).json()

    def list_tables(self) -> dict:
        url = f"{self.base_url}/applications/"
        return self.request(url).json()

    def list_teams(self) -> list[dict]:
        url = f"{self.base_url}/teams/list/"
        body = {"sort": [], "filter": {}}
        response = self.request(url, method="POST", body=body)
        return response.json()["items"]

    # endregion
