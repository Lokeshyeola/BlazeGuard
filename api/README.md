# API client prototype

api_client.py is an outbound Python HTTP client. It centralizes the base URL, timeout, JSON requests, and optional bearer-token header. blazeguard_endpoints.py wraps the current local prototype routes /system-status and /decision; demo_working.py is a manual smoke script for those routes.

Run the root FastAPI app first, then run python api/demo_working.py from the repository root. Install dependencies from the root requirements.txt.

This is not the future SPPU-to-BlazeGuard API contract. The bearer token is optional in the client, and the FastAPI app does not enforce inbound authentication. Authentication, endpoint definitions, and upstream behavior remain to be designed.
