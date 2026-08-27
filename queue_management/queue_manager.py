from sqlalchemy.orm import Session

from database.request_repository import (
    add_request,
    get_next_request,
    update_status,
)


def add_to_queue(
    db: Session,
    user_id: str,
    requested_url: str,
):
    """Add a new request to the FIFO queue."""

    next_request = get_next_request(db)

    if next_request:
        queue_position = next_request.queue_position + 1
    else:
        queue_position = 1

    return add_request(
        db=db,
        user_id=user_id,
        requested_url=requested_url,
        queue_position=queue_position,
    )


def get_next_waiting_request(db: Session):
    """Get the first request waiting in the queue."""

    return get_next_request(db)


def process_next_request(db: Session):
    """Move the next waiting request to PROCESSING."""

    request = get_next_request(db)

    if request is None:
        return None

    return update_status(
        db=db,
        request_id=request.id,
        status="PROCESSING",
    )


def complete_request(db: Session, request_id: int):
    """Mark a request as completed."""

    return update_status(
        db=db,
        request_id=request_id,
        status="COMPLETED",
    )