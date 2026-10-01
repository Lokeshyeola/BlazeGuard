import asyncio
import logging
import os
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.admission_service import get_current_admission
from backend.operator_telemetry import real_traffic_telemetry
from backend.request_forwarder import ProtectedServiceConfig, forward_request
from database.models import Request
from database.database import SessionLocal
from database.request_repository import (
    begin_request_attempt,
    recover_abandoned_requests,
    record_request_failure,
    update_status,
)
from queue_management.queue_manager import process_next_request

logger = logging.getLogger(__name__)


def get_poll_interval_seconds() -> float:
    try:
        interval = float(os.getenv("QUEUE_WORKER_POLL_INTERVAL_SECONDS", "2"))
    except ValueError:
        return 2.0
    return interval if 0.1 <= interval <= 60 else 2.0


def _bounded_integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def get_max_attempts() -> int:
    return _bounded_integer_env("QUEUE_WORKER_MAX_ATTEMPTS", 3, 1, 10)


def get_processing_timeout_seconds() -> float:
    try:
        value = float(os.getenv("QUEUE_WORKER_PROCESSING_TIMEOUT_SECONDS", "300"))
    except ValueError:
        return 300.0
    return value if 61 <= value <= 3600 else 300.0


def get_retry_backoff_seconds() -> float:
    try:
        value = float(os.getenv("QUEUE_WORKER_RETRY_BACKOFF_SECONDS", "0.25"))
    except ValueError:
        return 0.25
    return value if 0 <= value <= 30 else 0.25


async def process_next_request_if_allowed(
    db: Session,
    forwarding_config: ProtectedServiceConfig | None = None,
) -> Request | None:
    """Claim and forward one FIFO request only while policy returns ALLOW."""
    admission = await get_current_admission()
    if admission["decision"] != "ALLOW":
        return None

    recover_abandoned_requests(
        db,
        stale_after_seconds=get_processing_timeout_seconds(),
        max_attempts=get_max_attempts(),
    )
    request = process_next_request(db)
    if request is None:
        return None

    max_attempts = get_max_attempts()
    backoff_seconds = get_retry_backoff_seconds()

    # DEMO-ONLY safety boundary: a persisted marker selects local completion before
    # any forwarding call. The request URL is deliberately not used for this decision.
    if request.is_demo is True:
        request = begin_request_attempt(db, request.id)
        if request is None:
            return None
        try:
            return update_status(db, request.id, "COMPLETED")
        except ValueError:
            # RESET may cancel a claimed demo row while this local branch is completing.
            current = db.get(Request, request.id)
            if current is not None and current.status == "CANCELLED":
                return current
            raise

    while True:
        request = begin_request_attempt(db, request.id)
        if request is None:
            return None
        try:
            result = await asyncio.to_thread(forward_request, request, forwarding_config)
            category = result.error or "INTERNAL_ERROR"
        except Exception as exc:
            # Do not log exception text: transport/config exceptions may contain URLs.
            logger.error(
                "Unexpected queue forwarding failure",
                extra={"request_id": request.id, "failure_category": "INTERNAL_ERROR", "exception_type": type(exc).__name__},
            )
            result = None
            category = "INTERNAL_ERROR"

        if result is not None and result.succeeded:
            completed = update_status(db, request.id, "COMPLETED")
            if completed is not None and request.is_demo is not True:
                # OBSERVABILITY-ONLY: queue state remains database-backed.
                real_traffic_telemetry.record_completed()
            return completed

        record_request_failure(
            db,
            request.id,
            category,
            _failure_message(category),
            result.status_code if result is not None else None,
        )
        if request.is_demo is not True:
            # Count failed forwarding attempts, including a transient attempt before retry.
            real_traffic_telemetry.record_failure()
        if category in {"TRANSPORT_ERROR", "TIMEOUT"} and request.attempt_count < max_attempts:
            delay = min(backoff_seconds * (2 ** (request.attempt_count - 1)), 30.0)
            if delay:
                await asyncio.sleep(delay)
            continue
        return update_status(db, request.id, "FAILED")


def _failure_message(category: str) -> str:
    """Fixed, non-sensitive descriptions for persisted request diagnostics."""
    return {
        "TRANSPORT_ERROR": "Could not connect to the protected service.",
        "TIMEOUT": "The protected service did not respond before the timeout.",
        "HTTP_STATUS_ERROR": "The protected service returned an unsuccessful HTTP status.",
        "INVALID_TARGET": "The requested forwarding target is invalid.",
        "INVALID_CONFIGURATION": "The protected service configuration is invalid.",
        "SERVICE_NOT_CONFIGURED": "The protected service is not configured.",
        "INVALID_METHOD": "The requested HTTP method is invalid.",
        "INTERNAL_ERROR": "An unexpected internal error occurred while forwarding.",
    }.get(category, "The request could not be forwarded.")


class QueueWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        poll_interval_seconds: float | None = None,
        forwarding_config: ProtectedServiceConfig | None = None,
        demo_simulator=None,
    ):
        self.session_factory = session_factory
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else get_poll_interval_seconds()
        )
        self.forwarding_config = forwarding_config
        self.demo_simulator = demo_simulator
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="blazeguard-queue-worker")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        if not self._task.done() and self._stop_event is not None:
            self._stop_event.set()
        await self._task
        self._task = None

    async def _run_once(self) -> Request | None:
        db = self.session_factory()
        try:
            return await process_next_request_if_allowed(db, self.forwarding_config)
        finally:
            db.close()

    async def _run(self) -> None:
        if self._stop_event is None:
            return
        while not self._stop_event.is_set():
            cycle_started = asyncio.get_running_loop().time()
            try:
                await self._run_once()
            except Exception as exc:
                logger.error(
                    "Queue worker cycle failed",
                    extra={
                        "failure_category": "INTERNAL_ERROR",
                        "exception_type": type(exc).__name__,
                    },
                )

            delay = self.poll_interval_seconds
            if self.demo_simulator is not None:
                try:
                    target_rate = self.demo_simulator.worker_service_rate()
                except Exception:
                    target_rate = None
                    logger.exception("Could not read DEMO-ONLY worker service rate")
                if target_rate and target_rate > 0:
                    elapsed = asyncio.get_running_loop().time() - cycle_started
                    delay = max(0.0, (1.0 / target_rate) - elapsed)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=delay,
                )
            except asyncio.TimeoutError:
                pass
