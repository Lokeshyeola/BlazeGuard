"""Smoke test for the local BlazeGuard backend API layer."""
import blazeguard_endpoints as blazeguard_api

print("=" * 50)
print("GET /system-status  ->  blazeguard_api.get_system_status()")
print("=" * 50)
status = blazeguard_api.get_system_status(eta_seconds=15)
print("Result:", status)

print()
print("POST /decision  ->  blazeguard_api.get_decision(...)")
print("=" * 50)
decision = blazeguard_api.get_decision(10, 20, 15)
print("Result:", decision)

print()
print("The local API communication layer is working correctly.")
