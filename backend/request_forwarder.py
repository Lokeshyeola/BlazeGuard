from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit, urlunsplit

import requests


@dataclass(frozen=True)
class ProtectedServiceConfig:
    base_url: str
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ForwardResult:
    succeeded: bool
    status_code: int | None = None
    error: str | None = None


def get_protected_service_config() -> ProtectedServiceConfig:
    configured_timeout = os.getenv("PROTECTED_RESULT_TIMEOUT_SECONDS", "5")
    try:
        timeout_seconds = float(configured_timeout)
    except ValueError:
        timeout_seconds = 5.0
    if not 0 < timeout_seconds <= 60:
        timeout_seconds = 5.0
    return ProtectedServiceConfig(
        base_url=os.getenv("PROTECTED_RESULT_SERVICE_URL", "").strip(),
        timeout_seconds=timeout_seconds,
    )


def _build_target_url(config: ProtectedServiceConfig, requested_url: str) -> str:
    base = urlsplit(config.base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise ValueError("Protected service URL is not configured correctly.")

    requested = urlsplit(requested_url)
    path = requested.path
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise ValueError("Stored request path is invalid.")

    base_path = base.path.rstrip("/")
    return urlunsplit(
        (base.scheme, base.netloc, f"{base_path}{path}", requested.query, "")
    )


def forward_request(request, config: ProtectedServiceConfig | None = None) -> ForwardResult:
    """Forward stored request method/path/query only to the configured service."""
    protected_config = config or get_protected_service_config()
    if not protected_config.base_url:
        return ForwardResult(False, error="SERVICE_NOT_CONFIGURED")

    method = getattr(request, "request_method", "GET").upper()
    if not re.fullmatch(r"[A-Z]{1,10}", method):
        return ForwardResult(False, error="INVALID_METHOD")
    try:
        target_url = _build_target_url(protected_config, request.requested_url)
    except ValueError:
        return ForwardResult(False, error="INVALID_TARGET")

    try:
        response = requests.request(
            method,
            target_url,
            timeout=protected_config.timeout_seconds,
            allow_redirects=False,
        )
    except requests.Timeout:
        return ForwardResult(False, error="TIMEOUT")
    except requests.RequestException:
        return ForwardResult(False, error="TRANSPORT_ERROR")

    if 200 <= response.status_code < 300:
        return ForwardResult(True, status_code=response.status_code)
    return ForwardResult(False, status_code=response.status_code, error="HTTP_STATUS_ERROR")
