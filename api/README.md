# API client prototype

api_client.py is an outbound Python HTTP client. It centralizes the base URL, timeout, JSON requests, and optional bearer-token header. blazeguard_endpoints.py wraps the current local prototype routes /system-status and /decision; demo_working.py is a manual smoke script for those routes.

Run the root FastAPI app first, then run python api/demo_working.py from the repository root. Install dependencies from the root requirements.txt.

This is a prototype outbound client for BlazeGuard's legacy `/system-status` and `/decision` endpoints. The current `/api/v1` admission and request endpoints require an active BlazeGuard API key; API-key administration uses the separate server-side admin token. This client module does not yet wrap those authenticated APIs or define an external SPPU integration contract.
