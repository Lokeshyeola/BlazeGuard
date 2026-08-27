"""
BlazeGuard API — Endpoint Functions (Python)
-----------------------------------------------------------
This is the Python consumer layer for the active BlazeGuard backend.
It never touches `requests` directly — everything goes through api_client.

Naming convention: verb_noun (e.g. get_status, create_scan) so calls
read naturally: blazeguard_api.get_status()
"""

import api_client


# ---- GET examples: reading data from BlazeGuard ----

def get_system_status(eta_seconds=0):
    """Sample server metrics and get the engine decision."""
    return api_client.get("/system-status", params={"eta_seconds": eta_seconds})


def get_decision(cpu_percent, ram_percent, eta_seconds):
    """Apply the existing decision engine through POST /decision."""
    return api_client.post(
        "/decision",
        body={
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "eta_seconds": eta_seconds,
        },
    )
