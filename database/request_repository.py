from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Request


def add_request(
    db: Session,
    user_id: str,
    requested_url: str,
    idempotency_key: str | None = None,
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
            queue_position=current_max + 1,
            idempotency_key=idempotency_key,
            status="WAITING",
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
