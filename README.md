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

### DEMO/TEST ONLY admission override

For local demonstrations only, `BLAZEGUARD_DEMO_ADMISSION_MODE` can force the shared admission result used by both HTTP admission/request intake and the background queue worker. It is disabled by default: unset, empty, or `OFF` uses the real CPU/RAM admission policy and its unchanged thresholds. `ALLOW`, `QUEUE`, or `REJECT` (case-insensitive) forces that decision and returns a `DEMO_OVERRIDE_*` reason. Unknown values also fall back to the real policy. This setting has no API endpoint and must remain unset or `OFF` in normal deployments; do not enable it in production.

POST /api/v1/requests accepts JSON containing `requested_url` and the same bearer API key. ALLOW and REJECT responses include the decision and reason without creating a queue record. QUEUE creates a persistent WAITING request and returns `request_id`, `queue_position`, and status. An optional `Idempotency-Key` header makes queued retries return the original request; reusing that key for a different URL returns 409. GET /api/v1/requests/{request_id} returns the status and queue position to the API key that created the request.

The FastAPI lifespan starts one managed background queue worker by default. It polls using `QUEUE_WORKER_POLL_INTERVAL_SECONDS` (default 2 seconds), checks the current server-side admission policy, and atomically claims the next FIFO WAITING request only when the result is ALLOW. QUEUE and REJECT leave requests waiting and do not claim them. The worker forwards the stored method/path/query to the host configured by `PROTECTED_RESULT_SERVICE_URL`.

Queued requests follow `WAITING -> PROCESSING -> COMPLETED` or `WAITING -> PROCESSING -> FAILED`. Transient transport failures and timeouts retry up to `QUEUE_WORKER_MAX_ATTEMPTS` (default 3); upstream HTTP errors, invalid targets/configuration, and unexpected internal errors fail immediately. Retry delay starts at `QUEUE_WORKER_RETRY_BACKOFF_SECONDS` (default 0.25 seconds) and doubles, capped at 30 seconds. The worker returns an abandoned PROCESSING row to FIFO after it is older than `QUEUE_WORKER_PROCESSING_TIMEOUT_SECONDS` (default 300 seconds), unless its attempt limit is exhausted; then it marks the row FAILED / WORKER_INTERRUPTED. Failure category, safe diagnostic text, failure time, attempt count, and any received upstream HTTP status are available from the request record and authenticated request-status endpoint. Immediate ALLOW forwarding retains its synchronous response behavior and reports failure in its response; it does not create a queue record.

Set `QUEUE_WORKER_ENABLED=false` to disable the worker (for example, when running API-only tests). Run one application process with the worker enabled; each application process starts its own worker loop. The forwarding timeout is controlled by `PROTECTED_RESULT_TIMEOUT_SECONDS` (default 5 seconds). On interruption after a remote server has received a request but before BlazeGuard records its response, recovery can retry it; the remote service should make GET operations safe to repeat. The integration tests use local fake protected-service fixtures; BlazeGuard does not implement result lookup or calculation and is not connected to an external result service.

Important environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BLAZEGUARD_ADMIN_TOKEN` | unset | Private bearer token for API-key management; administration fails closed while unset. |
| `PROTECTED_RESULT_SERVICE_URL` | unset | Configurable upstream base URL; `.env.example` contains only an invalid placeholder host. |
| `PROTECTED_RESULT_TIMEOUT_SECONDS` | `5` | Per-attempt upstream timeout (valid range: greater than 0 through 60). |
| `QUEUE_WORKER_ENABLED` | `true` | Starts/stops the background worker. |
| `QUEUE_WORKER_POLL_INTERVAL_SECONDS` | `2` | Poll interval (valid range: 0.1–60; other values use the default). |
| `QUEUE_WORKER_MAX_ATTEMPTS` | `3` | Total bounded attempts (valid range: 1–10). |
| `QUEUE_WORKER_RETRY_BACKOFF_SECONDS` | `0.25` | Initial exponential retry delay (valid range: 0–30). |
| `QUEUE_WORKER_PROCESSING_TIMEOUT_SECONDS` | `300` | Age before an abandoned processing claim is recovered (valid range: 61–3600). |
| `BLAZEGUARD_DEMO_ADMISSION_MODE` | unset | Local demo/test-only ALLOW/QUEUE/REJECT override. Never enable for production. |

The test suite runs against isolated temporary SQLite databases and local fixture servers; it does not require either service to be running:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s testing -v
```

For the local two-service demo, start the separate fictional Result Portal from its own `backend` directory in Terminal 1:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Then in Terminal 2, from the BlazeGuard repository root, set the private admin token and the temporary upstream URL in that server process and start BlazeGuard:

```powershell
$env:BLAZEGUARD_ADMIN_TOKEN = "<set a private random value>"
$env:PROTECTED_RESULT_SERVICE_URL = "http://127.0.0.1:8001"
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Use a generated BlazeGuard API key with `Authorization: Bearer <API_KEY>` for `/api/v1` client routes; never put it in source, a URL, or logs. Keep `BLAZEGUARD_DEMO_ADMISSION_MODE` unset outside controlled demos. Tests 1–6 are reproduced by `testing/test_original_six_scenarios.py` using in-process local fixtures: successful forwarding to the fictional result path, queue admission, worker completion, reject without enqueue/forward, unavailable upstream transport handling, and configurable target verification. The tests do not use the separately running demo services.

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
