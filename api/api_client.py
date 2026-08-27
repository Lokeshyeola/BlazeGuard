"""
BlazeGuard API Communication Layer — Core Client (Python)
-----------------------------------------------------------
Purpose: Acts as the single "bridge" between the Frontend and the
BlazeGuard Backend. All requests (GET/POST/etc.) go through this
layer so the rest of the app never calls `requests` directly.

Why this exists:
 - Centralizes base URL, auth headers, error handling, timeouts
 - Calling code stays clean — it just calls endpoint functions
 - Easy to swap backend URL / auth strategy in ONE place
 - Consistent error format across the whole app

BACKEND CONTRACT:
 - Local backend speaks JSON over HTTP at http://127.0.0.1:8000
 - Auth via Bearer token, read from env var BLAZEGUARD_TOKEN
 - Base URL comes from env var BLAZEGUARD_API_URL
Change the CONFIG block below once real backend details are known.

Dependency: pip install requests
"""

import os
import requests


# ---------- CONFIG ----------
class Config:
    BASE_URL = os.environ.get("BLAZEGUARD_API_URL", "http://127.0.0.1:8000").rstrip("/")
    TIMEOUT_SECONDS = 10
    TOKEN_ENV_VAR = "BLAZEGUARD_TOKEN"


# ---------- Custom error type so callers can distinguish API errors ----------
class ApiError(Exception):
    def __init__(self, message, status=0, data=None):
        super().__init__(message)
        self.message = message
        self.status = status  # HTTP status code (0 = network/timeout error)
        self.data = data  # raw error payload from backend, if any

    def __str__(self):
        return f"ApiError(status={self.status}): {self.message}"


# ---------- Helper: get auth token (swap this if using cookies/OAuth) ----------
def _get_auth_token():
    return os.environ.get(Config.TOKEN_ENV_VAR)


# ---------- Helper: build headers ----------
def _build_headers(custom_headers=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if custom_headers:
        headers.update(custom_headers)

    token = _get_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


# ---------- Core request function (used internally by get/post helpers) ----------
def _request(method, path, params=None, body=None, headers=None):
    url = f"{Config.BASE_URL}{path}"

    try:
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=body,
            headers=_build_headers(headers),
            timeout=Config.TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        raise ApiError(f"Request to {path} timed out", status=0)
    except requests.exceptions.RequestException as err:
        raise ApiError(f"Network error calling {path}: {err}", status=0)

    # Try to parse JSON body regardless of status (backend may send error details)
    try:
        data = response.json() if response.text else None
    except ValueError:
        data = response.text  # non-JSON response, keep raw text

    if not response.ok:
        message = f"Request to {path} failed with status {response.status_code}"
        if isinstance(data, dict):
            message = data.get("message") or data.get("error") or message
        raise ApiError(message, status=response.status_code, data=data)

    return data


# ---------- Public methods: GET and POST ----------
def get(path, params=None):
    """GET → fetch data from BlazeGuard, does not change backend state."""
    return _request("GET", path, params=params)


def post(path, body=None):
    """POST → send/create data on BlazeGuard, changes backend state."""
    return _request("POST", path, body=body)


def put(path, body=None):
    return _request("PUT", path, body=body)


def delete(path):
    return _request("DELETE", path)
