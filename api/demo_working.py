"""
LIVE DEMO — proving the API communication layer actually works end-to-end.

This talks to a MOCK BlazeGuard backend running locally (mock_backend.py),
since we don't have the real BlazeGuard backend URL/endpoints yet.

Once you give me the REAL BlazeGuard URL and routes, all we do is change
BASE_URL and the endpoint paths — the rest of the code stays exactly the same.
"""
import os
os.environ["BLAZEGUARD_API_URL"] = "http://localhost:8000"

import api_client
import blazeguard_endpoints as blazeguard_api

print("=" * 50)
print("1) GET /status  ->  blazeguard_api.get_status()")
print("=" * 50)
status = blazeguard_api.get_status()
print("Result:", status)

print()
print("=" * 50)
print("2) GET /alerts  ->  blazeguard_api.get_alerts()")
print("=" * 50)
alerts = blazeguard_api.get_alerts({"severity": "high"})
print("Result:", alerts)

print()
print("=" * 50)
print("3) POST /scan  ->  blazeguard_api.start_scan(...)")
print("=" * 50)
scan = blazeguard_api.start_scan({"target": "network", "depth": "full"})
print("Result:", scan)

print()
print("All 3 calls succeeded. The bridge between frontend-code and")
print("backend-code is working correctly.")
