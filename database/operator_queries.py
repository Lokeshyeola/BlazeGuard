"""Read-only, bounded queries used only by the removable operator demo layer."""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Request


ACTIVE_STATUSES = ("WAITING", "PROCESSING")
QUEUE_SNAPSHOT_LIMIT = 100


def estimate_wait_seconds(waiting: int, service_rate: float) -> float | None:
    """Estimate FIFO wait using active waiting depth and a positive service rate."""
    if service_rate <= 0:
        return None
    return max(0, waiting) / service_rate


def operator_queue_snapshot(
    db: Session,
    *,
    session_started_at: datetime | None,
    demo_session_id: str | None = None,
    effective_service_rate: float,
    observed_window_seconds: int = 5,
) -> dict:
    """Return totals and a bounded active queue view without exposing paths or API-key IDs."""
    counts = dict(
        db.query(Request.status, func.count(Request.id))
        .filter(Request.status.in_(ACTIVE_STATUSES))
        .group_by(Request.status)
        .all()
    )
    waiting = int(counts.get("WAITING", 0))
    processing = int(counts.get("PROCESSING", 0))

    rows = (
        db.query(Request)
        .filter(Request.status.in_(ACTIVE_STATUSES))
        .order_by(Request.queue_position.asc(), Request.created_at.asc(), Request.id.asc())
        .limit(QUEUE_SNAPSHOT_LIMIT)
        .all()
    )
    queue = [
        {
            "request_id": str(row.id),
            "current_queue_position": index,
            "status": row.status,
            "is_demo": bool(row.is_demo),
            "created_at": row.created_at.isoformat() + "Z",
        }
        for index, row in enumerate(rows, start=1)
    ]

    since = session_started_at or datetime.utcnow()
    completed = int(
        db.scalar(
            select(func.count()).select_from(Request).where(
                Request.status == "COMPLETED",
                Request.updated_at >= since,
            )
        )
        or 0
    )
    failed = int(
        db.scalar(
            select(func.count()).select_from(Request).where(
                Request.status == "FAILED",
                Request.updated_at >= since,
            )
        )
        or 0
    )

    # Source-separated demo outcomes supplement the legacy session totals above.
    demo_completed = 0
    demo_failed = 0
    if demo_session_id is not None:
        demo_completed = int(
            db.scalar(
                select(func.count()).select_from(Request).where(
                    Request.is_demo.is_(True),
                    Request.demo_session_id == demo_session_id,
                    Request.status == "COMPLETED",
                    Request.updated_at >= since,
                )
            )
            or 0
        )
        demo_failed = int(
            db.scalar(
                select(func.count()).select_from(Request).where(
                    Request.is_demo.is_(True),
                    Request.demo_session_id == demo_session_id,
                    Request.status == "FAILED",
                    Request.updated_at >= since,
                )
            )
            or 0
        )

    observed_since = datetime.utcnow() - timedelta(seconds=observed_window_seconds)
    observed_completions = int(
        db.scalar(
            select(func.count()).select_from(Request).where(
                Request.status == "COMPLETED",
                Request.updated_at >= observed_since,
            )
        )
        or 0
    )
    observed_rate = observed_completions / max(1, observed_window_seconds)

    return {
        "waiting": waiting,
        "processing": processing,
        "queue_depth": waiting + processing,
        "completed": completed,
        "failed": failed,
        "demo_completed": demo_completed,
        "demo_failed": demo_failed,
        "effective_service_rate": effective_service_rate,
        "observed_service_rate": round(observed_rate, 2),
        "estimated_wait_seconds": estimate_wait_seconds(waiting, effective_service_rate),
        "queue": queue,
        "queue_truncated": waiting + processing > len(queue),
    }
