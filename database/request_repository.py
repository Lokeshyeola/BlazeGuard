from sqlalchemy.orm import Session

from .models import Request


def add_request(
    db: Session,
    user_id: str,
    requested_url: str,
    queue_position: int,
) -> Request:
    request = Request(
        user_id=user_id,
        requested_url=requested_url,
        queue_position=queue_position,
        status="WAITING",
    )

    db.add(request)
    db.commit()
    db.refresh(request)

    return request


def get_next_request(db: Session) -> Request | None:
    return (
        db.query(Request)
        .filter(Request.status == "WAITING")
        .order_by(Request.queue_position.asc())
        .first()
    )


def update_status(
    db: Session,
    request_id: int,
    status: str,
) -> Request | None:
    request = db.query(Request).filter(Request.id == request_id).first()

    if request is None:
        return None

    request.status = status
    db.commit()
    db.refresh(request)

    return request