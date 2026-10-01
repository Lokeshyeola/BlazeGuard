from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Request


def add_request(
    db: Session,
    user_id: str,
    requested_url: str,
    idempotency_key: str | None = None,
    request_method: str = "GET",
    is_demo: bool = False,
    demo_session_id: str | None = None,
) -> Request:
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if idempotency_key is not None:
            existing = db.scalar(
                select(Request).where(
                    Request.user_id == user_id,
                    Request.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.requested_url != requested_url:
                    raise ValueError("Idempotency key was already used for another request.")
                db.commit()
                return existing
        current_max = db.scalar(select(func.max(Request.queue_position))) or 0
        request = Request(
            user_id=user_id,
            requested_url=requested_url,
            request_method=request_method,
            queue_position=current_max + 1,
            idempotency_key=idempotency_key,
            status="WAITING",
            is_demo=is_demo,
            demo_session_id=demo_session_id,
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise


def get_next_request(db: Session) -> Request | None:
    return (
        db.query(Request)
        .filter(Request.status == "WAITING")
        .order_by(Request.queue_position.asc())
        .first()
    )


def claim_next_waiting_request(db: Session) -> Request | None:
    """Atomically claim the earliest waiting request in FIFO order."""
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        request = (
            db.query(Request)
            .filter(Request.status == "WAITING")
            .order_by(
                Request.queue_position.asc(),
                Request.created_at.asc(),
                Request.id.asc(),
            )
            .first()
        )
        if request is None:
            db.rollback()
            return None

        request.status = "PROCESSING"
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise


def begin_request_attempt(db: Session, request_id: int) -> Request | None:
    """Persist an attempt start only while this caller owns a PROCESSING row."""
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        request = db.query(Request).filter(Request.id == request_id).first()
        if request is None or request.status != "PROCESSING":
            db.rollback()
            return None
        request.attempt_count += 1
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise


def record_request_failure(
    db: Session,
    request_id: int,
    category: str,
    message: str,
    upstream_status_code: int | None = None,
) -> Request | None:
    """Persist sanitized diagnostics; never persist exception text or headers."""
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        request = db.query(Request).filter(Request.id == request_id).first()
        if request is None or request.status != "PROCESSING":
            db.rollback()
            return None
        request.failure_category = category[:32]
        request.failure_message = message[:300]
        request.failure_at = datetime.utcnow()
        request.upstream_status_code = upstream_status_code
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise


def recover_abandoned_requests(
    db: Session,
    stale_after_seconds: float = 300,
    max_attempts: int = 3,
) -> tuple[int, int]:
    """Requeue stale claims or finalize ones whose bounded attempts are spent."""
    cutoff = datetime.utcnow() - timedelta(seconds=max(0, stale_after_seconds))
    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        stale = list(
            db.scalars(
                select(Request).where(
                    Request.status == "PROCESSING",
                    Request.updated_at <= cutoff,
                ).order_by(Request.queue_position.asc(), Request.id.asc())
            ).all()
        )
        returned = 0
        failed = 0
        for request in stale:
            if request.attempt_count >= max_attempts:
                request.status = "FAILED"
                request.failure_category = "WORKER_INTERRUPTED"
                request.failure_message = "Worker stopped before the request completed."
                request.failure_at = datetime.utcnow()
                request.upstream_status_code = None
                failed += 1
            else:
                request.status = "WAITING"
                returned += 1
        db.commit()
        return returned, failed
    except Exception:
        db.rollback()
        raise


def get_idempotent_request(
    db: Session,
    user_id: str,
    idempotency_key: str,
) -> Request | None:
    return db.scalar(
        select(Request).where(
            Request.user_id == user_id,
            Request.idempotency_key == idempotency_key,
        )
    )


def current_queue_rank(db: Session, request_id: int) -> int | None:
    """Return a live 1-based rank among WAITING/PROCESSING rows; keep queue_position immutable."""
    request = db.get(Request, request_id)
    if request is None or request.status not in {"WAITING", "PROCESSING"}:
        return None
    return int(
        db.scalar(
            select(func.count())
            .select_from(Request)
            .where(
                Request.status.in_(("WAITING", "PROCESSING")),
                Request.queue_position <= request.queue_position,
            )
        )
        or 0
    )


def cancel_demo_session_requests(
    db: Session,
    demo_session_id: str,
) -> int:
    """Cancel only active demo rows from one session via the normal status transition helper."""
    rows = (
        db.query(Request)
        .filter(
            Request.is_demo.is_(True),
            Request.demo_session_id == demo_session_id,
            Request.status.in_(("WAITING", "PROCESSING")),
        )
        .order_by(Request.queue_position.asc(), Request.id.asc())
        .all()
    )
    cancelled = 0
    for row in rows:
        current = db.get(Request, row.id)
        if current is not None and current.status in {"WAITING", "PROCESSING"}:
            update_status(db, current.id, "CANCELLED")
            cancelled += 1
    return cancelled


def cancel_stale_demo_requests(db: Session) -> int:
    """Startup safety: stale demo work is cancelled before the worker starts; real rows are untouched."""
    stale_ids = [
        row[0]
        for row in db.query(Request.id)
        .filter(
            Request.is_demo.is_(True),
            Request.status.in_(("WAITING", "PROCESSING")),
        )
        .order_by(Request.queue_position.asc(), Request.id.asc())
        .all()
    ]
    cancelled = 0
    for request_id in stale_ids:
        current = db.get(Request, request_id)
        if current is not None and current.status in {"WAITING", "PROCESSING"}:
            update_status(db, current.id, "CANCELLED")
            cancelled += 1
    return cancelled


def update_status(
    db: Session,
    request_id: int,
    status: str,
) -> Request | None:
    allowed_transitions = {
        "WAITING": {"PROCESSING", "CANCELLED"},
        "PROCESSING": {"COMPLETED", "FAILED", "CANCELLED"},
        "COMPLETED": set(),
        "FAILED": set(),
        "CANCELLED": set(),
    }
    if status not in allowed_transitions:
        raise ValueError(f"Unknown request status: {status}")

    try:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        request = db.query(Request).filter(Request.id == request_id).first()
        if request is None:
            db.rollback()
            return None

        if status not in allowed_transitions.get(request.status, set()):
            raise ValueError(
                f"Invalid request status transition: {request.status} -> {status}"
            )

        request.status = status
        db.commit()
        db.refresh(request)
        return request
    except Exception:
        db.rollback()
        raise
