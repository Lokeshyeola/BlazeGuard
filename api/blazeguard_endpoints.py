"""
BlazeGuard API — Endpoint Functions (Python)
-----------------------------------------------------------
This is the layer the FRONTEND/consumer code actually imports and calls.
It never touches `requests` directly — everything goes through api_client.

PLACEHOLDER PATHS: replace "/status", "/scan", etc. with the real
BlazeGuard backend routes once documented (Swagger/Postman/README).

Naming convention: verb_noun (e.g. get_status, create_scan) so calls
read naturally: blazeguard_api.get_status()
"""

import api_client


# ---- GET examples: reading data from BlazeGuard ----

def get_status():
    """Get current system/security status."""
    return api_client.get("/status")


def get_alerts(filters=None):
    """Get list of detected threats/alerts (supports optional filters).

    e.g. get_alerts({"severity": "high", "limit": 20})
    """
    return api_client.get("/alerts", params=filters or {})


def get_alert_by_id(alert_id):
    """Get details for a single alert by ID."""
    return api_client.get(f"/alerts/{alert_id}")


# ---- POST examples: sending commands/data to BlazeGuard ----

def start_scan(scan_config):
    """Trigger a new security scan.

    e.g. start_scan({"target": "network", "depth": "full"})
    """
    return api_client.post("/scan", body=scan_config)


def acknowledge_alert(alert_id, notes=None):
    """Report/acknowledge an alert as handled."""
    return api_client.post(f"/alerts/{alert_id}/acknowledge", body={"notes": notes})


def login(username, password):
    """Login — example of how an auth token would be obtained."""
    return api_client.post("/auth/login", body={"username": username, "password": password})
