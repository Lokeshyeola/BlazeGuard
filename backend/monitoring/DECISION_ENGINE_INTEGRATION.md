# Decision Engine integration

The monitoring fixes in this bundle are complete for:
- real CPU/RAM/RPS/response-time metrics
- weighted health calculation
- `/health`
- `/api/monitor`
- request/response metric recording

The existing BlazeGuard Decision Engine should remain the single source of PROCESS/QUEUE decisions.

Do not create a second decision engine inside the monitoring module.

After the API teammate's final contract is merged, connect the monitoring values to that existing decision endpoint/engine using its confirmed request schema. This avoids creating two competing decision rules.
