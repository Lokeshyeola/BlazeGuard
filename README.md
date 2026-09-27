# BlazeGuard

BlazeGuard is a prototype backend for monitoring service load and making traffic decisions. The fictional student result portal is owned by the separate SPPU-result-demo repository; this repository does not provide that result website or result data.

## Repository components

- Core FastAPI prototype: backend/main.py with /, /decision, /system-status, authenticated admission, and request intake/status endpoints.
- Admission policy: backend/admission_policy.py returns ALLOW below 75%, QUEUE from 75% through below 90%, and REJECT at 90% or above. Decisions use trusted server-side CPU/RAM samples; missing or invalid monitoring data returns REJECT with MONITORING_UNAVAILABLE.
- Decision engine: backend/decision_engine/decision_engine.py retains its separate NORMAL, WARNING, CRITICAL, and DELAY labels for the existing legacy endpoints.
- Monitoring: backend/monitoring/ contains CPU, RAM, request-rate, response-time, and network metric prototypes. The root FastAPI app uses CPU/RAM sampling. backend/monitoring/main.py is an unfinished alternate application and is not the supported entry point.
- Queue management: queue_management/ and database/ provide the persistent SQLite request queue and atomic FIFO claim used by the authenticated intake API and worker.
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

## Admission endpoint

A client with an active BlazeGuard API key may call GET /api/v1/admission with Authorization: Bearer <API_KEY>. The endpoint samples CPU and RAM on BlazeGuard itself; it does not accept client-supplied metrics. It returns an admission of ALLOW, QUEUE, or REJECT with a reason. Missing, unavailable, or invalid measurements fail closed as REJECT / MONITORING_UNAVAILABLE.

POST /api/v1/requests accepts JSON containing `requested_url` and the same bearer API key. ALLOW and REJECT responses include the decision and reason without creating a queue record. QUEUE creates a persistent WAITING request and returns `request_id`, `queue_position`, and status. An optional `Idempotency-Key` header makes queued retries return the original request; reusing that key for a different URL returns 409. GET /api/v1/requests/{request_id} returns the status and queue position to the API key that created the request.

The internal `backend.queue_worker.process_next_request_if_allowed` performs one worker cycle. It checks the current server-side admission policy and atomically claims the next FIFO WAITING request only when the result is ALLOW, transitioning it to PROCESSING. It then forwards the stored method/path/query to the host configured by `PROTECTED_RESULT_SERVICE_URL` and marks the request COMPLETED for a successful response or FAILED for an error/timeout. QUEUE, REJECT, and an empty queue produce no claim. The forwarding timeout is controlled by `PROTECTED_RESULT_TIMEOUT_SECONDS` (default 5 seconds). The integration tests use a local fake protected result server; BlazeGuard does not implement result lookup or calculation and is not connected to a real SPPU/government server. No worker scheduler is configured.

## API key lifecycle

Management endpoints require the server-side BLAZEGUARD_ADMIN_TOKEN:

- POST /api/v1/api-keys generates a key and reveals its raw value once.
- GET /api/v1/api-keys returns IDs, timestamps, and status only.
- DELETE /api/v1/api-keys/{key_id} permanently revokes the key; the revoked row remains in the database.
- GET /api/v1/connection authenticates a trusted client using Authorization: Bearer <API_KEY>.

Keys use a cryptographically secure random secret and random lookup identifier. Only the SHA-256 digest is stored. Each connection request checks the active database record and uses a constant-time digest comparison. Revoked keys are rejected immediately and cannot be reactivated. Lost keys must be replaced.

This is a standalone credential-management foundation. It does not implement result processing or real SPPU integration. A future SPPU backend may call BlazeGuard using a server-side secret; no browser integration is implemented.

## Future integration

SPPU-result-demo remains a separate repository. The API surface can later support a separately designed authenticated integration without replacing this service architecture.
