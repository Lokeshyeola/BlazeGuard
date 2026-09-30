from dataclasses import dataclass
import logging
import os
import re
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProtectedServiceConfig:
    base_url: str
    timeout_seconds: float = 5.0
    configuration_error: str | None = None


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
        return ProtectedServiceConfig(
            os.getenv("PROTECTED_RESULT_SERVICE_URL", "").strip(),
            configuration_error="PROTECTED_RESULT_TIMEOUT_SECONDS must be a number.",
        )
    if not 0 < timeout_seconds <= 60:
        return ProtectedServiceConfig(
            os.getenv("PROTECTED_RESULT_SERVICE_URL", "").strip(),
            configuration_error="PROTECTED_RESULT_TIMEOUT_SECONDS must be between 0 and 60.",
        )
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
        or any(ord(char) < 32 or char.isspace() for char in config.base_url)
    ):
        raise ValueError("Protected service URL is not configured correctly.")
    try:
        if base.port is not None and not 1 <= base.port <= 65535:
            raise ValueError("Invalid protected service port.")
    except ValueError as exc:
        raise ValueError("Protected service URL is not configured correctly.") from exc

    requested = urlsplit(requested_url)
    path = requested.path
    decoded_path = path
    for _ in range(10):
        expanded_path = unquote(decoded_path)
        if expanded_path == decoded_path:
            break
        decoded_path = expanded_path
    if (
        not path.startswith("/")
        or (requested.netloc and not requested.scheme)
        or path.startswith("//")
        or decoded_path.startswith("//")
        or "\\" in decoded_path
        or any(ord(char) < 32 for char in decoded_path)
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        or unquote(decoded_path) != decoded_path
    ):
        raise ValueError("Stored request path is invalid.")

    base_path = base.path.rstrip("/")
    decoded_base_path = base_path
    for _ in range(10):
        expanded_path = unquote(decoded_base_path)
        if expanded_path == decoded_base_path:
            break
        decoded_base_path = expanded_path
    if (
        decoded_base_path.startswith("//")
        or "\\" in decoded_base_path
        or any(ord(char) < 32 for char in decoded_base_path)
        or any(segment in {".", ".."} for segment in decoded_base_path.split("/"))
        or unquote(decoded_base_path) != decoded_base_path
    ):
        raise ValueError("Protected service base path is invalid.")
    return urlunsplit(
        (base.scheme, base.netloc, f"{base_path}{path}", requested.query, "")
    )


def forward_request(request, config: ProtectedServiceConfig | None = None) -> ForwardResult:
    """Forward stored request method/path/query only to the configured service."""
    protected_config = config or get_protected_service_config()
    if protected_config.configuration_error:
        logger.error("Protected service configuration is invalid", extra={"failure_category": "INVALID_CONFIGURATION"})
        return ForwardResult(False, error="INVALID_CONFIGURATION")
    if not protected_config.base_url:
        logger.error("Protected service is not configured", extra={"failure_category": "INVALID_CONFIGURATION"})
        return ForwardResult(False, error="SERVICE_NOT_CONFIGURED")

    method = getattr(request, "request_method", "GET").upper()
    if not re.fullmatch(r"[A-Z]{1,10}", method):
        return ForwardResult(False, error="INVALID_METHOD")
    try:
        target_url = _build_target_url(protected_config, request.requested_url)
    except ValueError:
        logger.warning(
            "Request forwarding target is invalid",
            extra={"request_id": getattr(request, "id", None), "failure_category": "INVALID_TARGET"},
        )
        return ForwardResult(False, error="INVALID_TARGET")

    try:
        response = requests.request(
            method,
            target_url,
            timeout=protected_config.timeout_seconds,
            allow_redirects=False,
        )
    except requests.Timeout:
        logger.warning(
            "Protected service request timed out",
            extra={"request_id": getattr(request, "id", None), "failure_category": "TIMEOUT"},
        )
        return ForwardResult(False, error="TIMEOUT")
    except requests.RequestException:
        logger.warning(
            "Protected service transport failed",
            extra={"request_id": getattr(request, "id", None), "failure_category": "TRANSPORT_ERROR"},
        )
        return ForwardResult(False, error="TRANSPORT_ERROR")

    if 200 <= response.status_code < 300:
        return ForwardResult(True, status_code=response.status_code)
    logger.warning(
        "Protected service returned an unsuccessful HTTP status",
        extra={
            "request_id": getattr(request, "id", None),
            "failure_category": "HTTP_STATUS_ERROR",
            "upstream_status_code": response.status_code,
        },
    )
    return ForwardResult(False, status_code=response.status_code, error="HTTP_STATUS_ERROR")
