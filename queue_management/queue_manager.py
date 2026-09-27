from sqlalchemy.orm import Session

from database.request_repository import (
    add_request,
    claim_next_waiting_request,
    get_next_request,
    update_status,
)


def add_to_queue(
    db: Session,
    user_id: str,
    requested_url: str,
    idempotency_key: str | None = None,
    request_method: str = "GET",
):
    """Add a new request to the FIFO queue."""

    return add_request(
        db=db,
        user_id=user_id,
        requested_url=requested_url,
        idempotency_key=idempotency_key,
        request_method=request_method,
    )


def get_next_waiting_request(db: Session):
    """Get the first request waiting in the queue."""

    return get_next_request(db)


def process_next_request(db: Session):
    """Atomically move the earliest waiting request to PROCESSING."""
    return claim_next_waiting_request(db)


def complete_request(db: Session, request_id: int):
    """Mark a request as completed."""

    return update_status(
        db=db,
        request_id=request_id,
        status="COMPLETED",
    )
