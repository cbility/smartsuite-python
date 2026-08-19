"""Example showing how to use the SmartSuite client with dataclasses."""

from smartsuite_python import (
    SmartSuiteClient,
    FilterElement,
    FilterDateValue,
    FilterComparison,
    FilterValue,
)

# Initialize the client
client = SmartSuiteClient(
    account_id="your-account-id",
    api_token="your-api-token",
)

# Simple filter with basic value
records = client.filter_records(
    table_id="projects",
    fields_to_filter=[
        FilterElement(field="status", comparison="is", value="Complete"),
    ],
)

# Multiple filters with AND
records = client.filter_records(
    table_id="projects",
    fields_to_filter=[
        FilterElement(field="status", comparison="is", value="Complete"),
        FilterElement(field="priority", comparison="is", value="High"),
    ],
    operator="and",
)

# Date filter with FilterDateValue
records = client.filter_records(
    table_id="projects",
    fields_to_filter=[
        FilterElement(
            field="due_date",
            comparison="is_before",
            value=FilterDateValue(date_mode="today", date_mode_value=""),
        ),
    ],
)

# OR filter
records = client.filter_records(
    table_id="projects",
    fields_to_filter=[
        FilterElement(field="status", comparison="is", value="Complete"),
        FilterElement(field="status", comparison="is", value="On Hold"),
    ],
    operator="or",
)


# Using type hints in your own functions
def create_status_filter(status: str) -> FilterElement:
    """Create a filter element for status field."""
    return FilterElement(field="status", comparison="is", value=status)


def create_priority_filter(priority: str) -> FilterElement:
    """Create a filter element for priority field."""
    return FilterElement(field="priority", comparison="is", value=priority)


# Use the helper functions
records = client.filter_records(
    table_id="projects",
    fields_to_filter=[
        create_status_filter("Complete"),
        create_priority_filter("High"),
    ],
)

# Access field types for your own type annotations
from dataclasses import fields

# Get all fields from FilterElement
for field in fields(FilterElement):
    print(f"{field.name}: {field.type}")
# Output:
# field: str
# comparison: FilterComparison
# value: FilterValue
