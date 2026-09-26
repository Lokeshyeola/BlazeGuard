# BlazeGuard

BlazeGuard is a prototype backend for monitoring service load and making traffic decisions. The fictional student result portal is owned by the separate SPPU-result-demo repository; this repository does not provide that result website or result data.

## Repository components

- Core FastAPI prototype: backend/main.py with /, /decision, and /system-status.
- Decision engine: backend/decision_engine/decision_engine.py; retains the existing NORMAL, WARNING, CRITICAL, and DELAY thresholds.
- Monitoring: backend/monitoring/ contains CPU, RAM, request-rate, response-time, and network metric prototypes. The root FastAPI app currently uses CPU/RAM sampling. backend/monitoring/main.py is an unfinished alternate application and currently imports modules that are absent from this repository; it is not the supported entry point.
- Queue management: queue_management/ contains SQLAlchemy queue operations, separate from and not currently wired into the FastAPI app.
- Database: database/ contains SQLite setup, the Request model, table creation, and repository functions.
- API client: api/ contains an outbound Python client, wrappers for the current local prototype routes, a README, and a smoke script. The client's optional bearer token is not equivalent to inbound authentication; the FastAPI app does not enforce API authentication.
- Prototype/synthetic monitoring simulator: frontend/blazeguard-monitor.js and frontend/config.js are retained as legacy test-only browser code. It does not generate 100/500/1000 actual concurrent requests. Its displayed request count is synthetic, and its decision simulation sends fixed CPU/RAM metrics. The old portal HTML is removed, so this script is not part of a student/result flow and its UI is not currently hosted.
- testing/ is reserved for tests; no automated tests currently exist.

The monitoring alternate app refers to missing configuration, error, metrics-store, capacity, and alert modules. The queue/database code has not been integrated with the active FastAPI app. These are known prototype boundaries, not claims of a complete production gateway.

## Local development

Requires Python and pip. From the repository root:

1. Create and activate a virtual environment.
2. Install dependencies with pip install -r requirements.txt.
3. Run python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000.

The root app uses the existing decision engine and samples CPU/RAM. The legacy client smoke script can be run with python api/demo_working.py while the backend is running.

## Future integration

SPPU-result-demo is a separate repository. No SPPU integration, API-key authentication, upstream forwarding, or production queue contract is implemented here. Those interfaces must be agreed before implementation.

