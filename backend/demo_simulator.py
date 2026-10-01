"""DEMO-ONLY server-side traffic generator. Remove this module to remove simulation control."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
import threading
import time
import uuid
from collections.abc import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.admission_service import get_current_admission
from database.database import SessionLocal
from database.models import OperatorDemoControl, Request
from database.request_repository import cancel_demo_session_requests
from queue_management.queue_manager import add_to_queue


# Hard limits remain server-side; slider values outside them are reported as clamped.
MAX_DEMO_DURATION_SECONDS = 60
MAX_DEMO_REQUESTS_PER_SESSION = 100
MAX_DEMO_OUTSTANDING_REQUESTS = 100
MAX_DEMO_TOTAL_REQUESTS = 500
MAX_DEMO_GENERATION_RATE = 50
MAX_DEMO_SERVICE_RATE = 10
DEMO_SESSION_STALE_AFTER_SECONDS = MAX_DEMO_DURATION_SECONDS + 15
DEMO_OWNER_ID = "operator-demo"
DEMO_REQUEST_PATH = "/__blazeguard_demo__/virtual"


def demo_mode_enabled() -> bool:
    return os.getenv("BLAZEGUARD_DEMO_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


@dataclass
class DemoSession:
    session_id: str
    started_at: datetime
    metrics_since: datetime
    started_monotonic: float
    requested_incoming_rate: int
    effective_incoming_rate: int
    requested_capacity: int
    effective_capacity: int
    generation_limit: int
    duration_limit_seconds: int
    generated_attempts: int = 0
    incoming: int = 0
    allowed: int = 0
    rejected: int = 0
    running: bool = True
    holding_queue: bool = True
    stop_event: asyncio.Event | None = None
    task: asyncio.Task | None = None
    limit_reason: str | None = None


class DemoSimulator:
    """Owns one bounded demo session and routes all generated rows through the Phase 1 queue helper."""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self.session_factory = session_factory
        self._lock = threading.RLock()
        self._session: DemoSession | None = None

    async def start(self, incoming_rate: int, capacity: int) -> dict:
        if not demo_mode_enabled():
            raise RuntimeError("DEMO_MODE_DISABLED")
        with self._lock:
            if self._session and self._session.running:
                raise RuntimeError("DEMO_SESSION_ALREADY_ACTIVE")
            now = datetime.utcnow()
            session_id = str(uuid.uuid4())
            stale_session_id = self._recover_stale_claim(now)
            if stale_session_id:
                db = self.session_factory()
                try:
                    cancel_demo_session_requests(db, stale_session_id)
                finally:
                    db.close()
            self._claim_session(session_id, now)
            try:
                db = self.session_factory()
                try:
                    outstanding = self._count_demo_rows(db, active_only=True)
                    cumulative = self._count_demo_rows(db, active_only=False)
                finally:
                    db.close()
            except Exception:
                self._release_session_claim(session_id)
                raise
            if outstanding >= MAX_DEMO_OUTSTANDING_REQUESTS:
                self._release_session_claim(session_id)
                raise RuntimeError("DEMO_OUTSTANDING_LIMIT")
            if cumulative >= MAX_DEMO_TOTAL_REQUESTS:
                self._release_session_claim(session_id)
                raise RuntimeError("DEMO_DATA_LIMIT_REACHED")
            session = DemoSession(
                session_id=session_id,
                started_at=now,
                metrics_since=now,
                started_monotonic=time.monotonic(),
                requested_incoming_rate=incoming_rate,
                effective_incoming_rate=min(incoming_rate, MAX_DEMO_GENERATION_RATE),
                requested_capacity=capacity,
                effective_capacity=min(capacity, MAX_DEMO_SERVICE_RATE),
                generation_limit=MAX_DEMO_REQUESTS_PER_SESSION,
                duration_limit_seconds=MAX_DEMO_DURATION_SECONDS,
                stop_event=asyncio.Event(),
            )
            self._session = session
            session.task = asyncio.create_task(self._generate(session.session_id))
            return self._session_payload(session)

    async def _generate(self, session_id: str) -> None:
        try:
            while True:
                with self._lock:
                    session = self._session
                    if session is None or session.session_id != session_id or not session.running:
                        return
                    if (
                        session.generated_attempts >= session.generation_limit
                        or time.monotonic() - session.started_monotonic >= session.duration_limit_seconds
                    ):
                        session.running = False
                        return
                    delay = 1.0 / session.effective_incoming_rate
                    stop_event = session.stop_event
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    pass
                await self._generate_one(session_id)
        except Exception:
            with self._lock:
                if self._session and self._session.session_id == session_id:
                    self._session.running = False
        finally:
            self._release_session_claim(session_id)

    async def _generate_one(self, session_id: str) -> None:
        with self._lock:
            session = self._session
            if session is None or session.session_id != session_id or not session.running:
                return
            if session.generated_attempts >= session.generation_limit:
                session.running = False
                return
            session.generated_attempts += 1

        # Every generated arrival uses the shared Phase 1 monitor/admission service.
        admission = await get_current_admission()
        decision = admission["decision"]

        with self._lock:
            session = self._session
            if session is None or session.session_id != session_id or not session.running:
                return
            if time.monotonic() - session.started_monotonic >= session.duration_limit_seconds:
                session.running = False
                return
            session.incoming += 1
            if decision == "REJECT":
                session.rejected += 1
                return
            if decision == "ALLOW":
                session.allowed += 1

            # ALLOW and QUEUE both become ordinary FIFO rows; is_demo controls only worker disposition.
            db = self.session_factory()
            try:
                cumulative = self._count_demo_rows(db, active_only=False)
                outstanding = self._count_demo_rows(db, active_only=True)
                if cumulative >= MAX_DEMO_TOTAL_REQUESTS:
                    session.limit_reason = "CUMULATIVE_DEMO_DATA_LIMIT"
                    session.running = False
                    if session.stop_event is not None:
                        session.stop_event.set()
                    return
                if outstanding >= MAX_DEMO_OUTSTANDING_REQUESTS:
                    session.limit_reason = "OUTSTANDING_DEMO_ROW_LIMIT"
                    session.running = False
                    if session.stop_event is not None:
                        session.stop_event.set()
                    return
                add_to_queue(
                    db,
                    DEMO_OWNER_ID,
                    DEMO_REQUEST_PATH,
                    idempotency_key=f"demo-{session_id}-{session.generated_attempts}",
                    request_method="GET",
                    is_demo=True,
                    demo_session_id=session_id,
                )
            finally:
                db.close()

    async def stop(self) -> dict:
        with self._lock:
            session = self._session
            if session is None:
                return self.state()
            session.running = False
            if session.stop_event is not None:
                session.stop_event.set()
            task = session.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            await task
        self._release_session_claim(session.session_id)
        return self.state()

    async def reset(self) -> dict:
        await self.stop()
        with self._lock:
            session = self._session
            if session is None:
                return self.state()
            session_id = session.session_id
            session.metrics_since = datetime.utcnow()
            session.incoming = 0
            session.allowed = 0
            session.rejected = 0
            session.limit_reason = None
        db = self.session_factory()
        try:
            cancel_demo_session_requests(db, session_id)
            self._refresh_queue_hold(db)
        finally:
            db.close()
        return self.state()

    def update_settings(self, incoming_rate: int, capacity: int) -> dict:
        with self._lock:
            session = self._session
            if session is None or not (session.running or session.holding_queue):
                raise RuntimeError("NO_ACTIVE_DEMO_SESSION")
            session.requested_incoming_rate = incoming_rate
            session.effective_incoming_rate = min(incoming_rate, MAX_DEMO_GENERATION_RATE)
            session.requested_capacity = capacity
            session.effective_capacity = min(capacity, MAX_DEMO_SERVICE_RATE)
            return self._session_payload(session)

    def record_real_arrival(self) -> None:
        """Count a real intake attempt within the current demo session."""
        with self._lock:
            session = self._session
            if session is None or not (session.running or session.holding_queue):
                return
            session.incoming += 1

    def record_real_decision(self, decision: str) -> None:
        """Count a real admission result without changing it."""
        with self._lock:
            session = self._session
            if session is None or not (session.running or session.holding_queue):
                return
            if decision == "ALLOW":
                session.allowed += 1
            elif decision == "REJECT":
                session.rejected += 1

    def enqueue_real_if_holding(
        self,
        db: Session,
        user_id: str,
        requested_url: str,
        request_method: str,
        idempotency_key: str | None = None,
    ):
        """Preserve the shared FIFO while an explicitly started demo session holds the queue."""
        with self._lock:
            session = self._session
            if session is not None and not session.running:
                self._refresh_queue_hold(db)
                session = self._session
            if session is None or not (session.running or session.holding_queue):
                return None
            return add_to_queue(
                db,
                user_id,
                requested_url,
                idempotency_key=idempotency_key,
                request_method=request_method,
            )

    def worker_service_rate(self) -> float | None:
        """Rate target supplied only during demo/drain mode; None preserves Phase 1 timing."""
        with self._lock:
            session = self._session
            if session is None:
                return None
            if not session.running and session.holding_queue:
                db = self.session_factory()
                try:
                    self._refresh_queue_hold(db)
                finally:
                    db.close()
                session = self._session
            if session and (session.running or session.holding_queue):
                return float(session.effective_capacity)
            return None

    def state(self) -> dict:
        with self._lock:
            if self._session is None:
                return {
                    "demo_enabled": demo_mode_enabled(),
                    "running": False,
                    "session_id": None,
                    "state": "STOPPED",
                    "incoming": 0,
                    "allowed": 0,
                    "rejected": 0,
                    "requested_incoming_rate": 0,
                    "effective_incoming_rate": 0,
                    "requested_capacity": 0,
                    "effective_capacity": 0,
                    "generated_attempts": 0,
                    "max_requests": MAX_DEMO_REQUESTS_PER_SESSION,
                    "max_total_requests": MAX_DEMO_TOTAL_REQUESTS,
                    "max_outstanding_requests": MAX_DEMO_OUTSTANDING_REQUESTS,
                    "max_duration_seconds": MAX_DEMO_DURATION_SECONDS,
                    "session_started_at": None,
                    "metrics_since": None,
                    "limit_reason": None,
                }
            return {"demo_enabled": demo_mode_enabled(), **self._session_payload(self._session)}

    def _session_payload(self, session: DemoSession) -> dict:
        return {
            "running": session.running,
            "session_id": session.session_id,
            "state": "RUNNING" if session.running else ("DRAINING" if session.holding_queue else "STOPPED"),
            "incoming": session.incoming,
            "allowed": session.allowed,
            "rejected": session.rejected,
            "requested_incoming_rate": session.requested_incoming_rate,
            "effective_incoming_rate": session.effective_incoming_rate,
            "requested_capacity": session.requested_capacity,
            "effective_capacity": session.effective_capacity,
            "generated_attempts": session.generated_attempts,
            "max_requests": session.generation_limit,
            "max_total_requests": MAX_DEMO_TOTAL_REQUESTS,
            "max_outstanding_requests": MAX_DEMO_OUTSTANDING_REQUESTS,
            "max_duration_seconds": session.duration_limit_seconds,
            "session_started_at": session.started_at.isoformat() + "Z",
            "metrics_since": session.metrics_since.isoformat() + "Z",
            "limit_reason": session.limit_reason,
        }

    def snapshot(self, db: Session) -> dict:
        with self._lock:
            if self._session is not None and not self._session.running:
                self._refresh_queue_hold(db)
        session_state = self.state()
        rate = session_state["effective_capacity"]
        from database.operator_queries import operator_queue_snapshot

        metrics = operator_queue_snapshot(
            db,
            session_started_at=(
                datetime.fromisoformat(session_state["metrics_since"].removesuffix("Z"))
                if session_state["metrics_since"] else None
            ),
            effective_service_rate=rate,
        )
        return {**session_state, **metrics}

    def _refresh_queue_hold(self, db: Session) -> None:
        with self._lock:
            session = self._session
            if session is None or session.running:
                return
            active_count = int(
                db.scalar(
                    select(func.count()).select_from(Request).where(
                    Request.status.in_(("WAITING", "PROCESSING"))
                    )
                )
                or 0
            )
            if active_count == 0:
                session.holding_queue = False

    def _recover_stale_claim(self, now: datetime) -> str | None:
        """Release a crashed claim only after the hard session duration plus grace."""
        db = self.session_factory()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            control = db.get(OperatorDemoControl, 1)
            if control is None or control.active_session_id is None:
                db.rollback()
                return None
            stale = (
                control.active_started_at is None
                or (now - control.active_started_at).total_seconds()
                > DEMO_SESSION_STALE_AFTER_SECONDS
            )
            if not stale:
                db.rollback()
                return None
            stale_session_id = control.active_session_id
            control.active_session_id = None
            control.active_started_at = None
            db.commit()
            return stale_session_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _claim_session(self, session_id: str, started_at: datetime) -> None:
        """Atomically claim the singleton using SQLite's existing write lock."""
        db = self.session_factory()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            control = db.get(OperatorDemoControl, 1)
            if control is None:
                control = OperatorDemoControl(id=1)
                db.add(control)
                db.flush()
            if control.active_session_id is not None:
                raise RuntimeError("DEMO_SESSION_ALREADY_ACTIVE")
            control.active_session_id = session_id
            control.active_started_at = started_at
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _release_session_claim(self, session_id: str) -> None:
        """Release only this session's singleton claim; safe to call more than once."""
        db = self.session_factory()
        try:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            control = db.get(OperatorDemoControl, 1)
            if control is not None and control.active_session_id == session_id:
                control.active_session_id = None
                control.active_started_at = None
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _count_demo_rows(db: Session, *, active_only: bool) -> int:
        query = select(func.count()).select_from(Request).where(Request.is_demo.is_(True))
        if active_only:
            query = query.where(Request.status.in_(("WAITING", "PROCESSING")))
        return int(db.scalar(query) or 0)


def cleanup_stale_demo_rows(session_factory: Callable[[], Session] = SessionLocal) -> int:
    """Recover an expired singleton and cancel marked rows except a live session's rows."""
    db = session_factory()
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        now = datetime.utcnow()
        control = db.get(OperatorDemoControl, 1)
        active_session_id = None
        if control is not None and control.active_session_id is not None:
            is_stale = (
                control.active_started_at is None
                or (now - control.active_started_at).total_seconds()
                > DEMO_SESSION_STALE_AFTER_SECONDS
            )
            if is_stale:
                control.active_session_id = None
                control.active_started_at = None
            else:
                active_session_id = control.active_session_id

        stale_rows = update(Request).where(
            Request.is_demo.is_(True),
            Request.status.in_(("WAITING", "PROCESSING")),
        )
        if active_session_id is not None:
            stale_rows = stale_rows.where(
                (Request.demo_session_id != active_session_id)
                | Request.demo_session_id.is_(None)
            )
        result = db.execute(
            stale_rows.values(status="CANCELLED", updated_at=now),
            execution_options={"synchronize_session": False},
        )
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
