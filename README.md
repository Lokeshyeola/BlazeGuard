# BlazeGuard

BlazeGuard is a prototype backend for monitoring service load and making traffic decisions. The fictional student result portal is owned by the separate SPPU-result-demo repository; this repository does not provide that result website or result data.

## Repository components

- Core FastAPI prototype: backend/main.py with /, /decision, and /system-status.
- Decision engine: backend/decision_engine/decision_engine.py; retains existing NORMAL, WARNING, CRITICAL, and DELAY thresholds.
- Monitoring: backend/monitoring/ contains CPU, RAM, request-rate, response-time, and network metric prototypes. The root FastAPI app uses CPU/RAM sampling. backend/monitoring/main.py is an unfinished alternate application and is not the supported entry point.
- Queue management: queue_management/ contains SQLAlchemy queue operations, separate from and not currently wired into FastAPI.
- Database: database/ contains SQLite setup, Request and ApiKey models, table creation, and repository functions.
- API client: api/ contains an outbound Python client and smoke script. Its optional bearer token is not inbound API authentication.
- Prototype/synthetic monitoring simulator: frontend/blazeguard-monitor.js and frontend/config.js are legacy test-only browser code. It does not generate 100/500/1000 actual concurrent requests. Its displayed request count is synthetic, and its decision simulation sends fixed CPU/RAM metrics.
- Administrator console: frontend/api.html manages API keys through authenticated backend endpoints.

## Local development

Requires Python and pip. From the repository root:

1. Create and activate a virtual environment.
2. Install dependencies: pip install -r requirements.txt
3. Set a private admin token in PowerShell: $env:BLAZEGUARD_ADMIN_TOKEN = "use-a-long-random-private-value"
4. Run the API: python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
5. In another terminal serve the admin page: python -m http.server 5500 --directory frontend
6. Open http://localhost:5500/api.html. API URL: http://127.0.0.1:8000.

The admin token is kept in page memory only and sent to the local API in an Authorization header. Never put it or generated API keys in source code, URLs, or logs.

## API key lifecycle

Management endpoints require the server-side BLAZEGUARD_ADMIN_TOKEN:

- POST /api/v1/api-keys generates a key and reveals its raw value once.
- GET /api/v1/api-keys returns IDs, timestamps, and status only.
- DELETE /api/v1/api-keys/{key_id} permanently revokes the key; the revoked row remains in the database.
- GET /api/v1/connection authenticates a trusted client using Authorization: Bearer <API_KEY>.

Keys use a cryptographically secure random secret and random lookup identifier. Only the SHA-256 digest is stored. Each connection request checks the active database record and uses a constant-time digest comparison. Revoked keys are rejected immediately and cannot be reactivated. Lost keys must be replaced.

This is a standalone credential-management foundation. It does not implement upstream forwarding, queue endpoints, or SPPU integration. A future SPPU backend may call BlazeGuard using a server-side secret; no browser integration is implemented.

## Future integration

SPPU-result-demo remains a separate repository. The API surface can later support a separately designed authenticated integration without replacing this service architecture.
