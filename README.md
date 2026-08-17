## SmartSuite Python

Python client for the SmartSuite API.

### Install from GitHub with uv

```powershell
uv add "smartsuite-python @ git+https://github.com/cbility/smartsuite-python.git"
```

### Import

```python
from smartsuite_python import SmartSuiteClient

client = SmartSuiteClient(
	account_id="your-account-id",
	api_token="your-api-token",
)
```
