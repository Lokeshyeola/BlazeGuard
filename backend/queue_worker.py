import asyncio
import logging
import os
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.admission_service import get_current_admission
from backend.request_forwarder import ProtectedServiceConfig, forward_request
from database.models import Request
from database.database import SessionLocal
from database.request_repository import update_status
from queue_management.queue_manager import process_next_request

logger = logging.getLogger(__name__)


def get_poll_interval_seconds() -> float:
    try:
        interval = float(os.getenv("QUEUE_WORKER_POLL_INTERVAL_SECONDS", "2"))
    except ValueError:
        return 2.0
    return interval if 0.1 <= interval <= 60 else 2.0


async def process_next_request_if_allowed(
    db: Session,
    forwarding_config: ProtectedServiceConfig | None = None,
) -> Request | None:
    """Claim and forward one FIFO request only while policy returns ALLOW."""
    admission = await get_current_admission()
    if admission["decision"] != "ALLOW":
        return None

    request = process_next_request(db)
    if request is None:
        return None

    try:
        result = await asyncio.to_thread(forward_request, request, forwarding_config)
        final_status = "COMPLETED" if result.succeeded else "FAILED"
    except Exception:
        final_status = "FAILED"
    return update_status(db, request.id, final_status)


class QueueWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        poll_interval_seconds: float | None = None,
        forwarding_config: ProtectedServiceConfig | None = None,
    ):
        self.session_factory = session_factory
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else get_poll_interval_seconds()
        )
        self.forwarding_config = forwarding_config
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
            try:
                await self._run_once()
            except Exception:
                logger.exception("Queue worker cycle failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
