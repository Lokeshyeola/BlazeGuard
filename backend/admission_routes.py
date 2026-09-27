from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api_key_routes import get_db, require_api_key
from backend.monitoring.cpu_monitor import cpu_monitor
from backend.monitoring.ram_monitor import ram_monitor
from backend.admission_policy import decide_admission
from database.models import ApiKey, Request
from database.request_repository import get_idempotent_request
from queue_management.queue_manager import add_to_queue


router = APIRouter(prefix="/api/v1")


class RequestIntake(BaseModel):
    requested_url: str = Field(min_length=1, max_length=500)


async def get_current_admission() -> dict:
    """Sample server-side resource monitors and apply the admission policy."""
    cpu_percent = None
    ram_percent = None

    try:
        cpu_metrics = await cpu_monitor.sample()
        if cpu_metrics.get("available") is True:
            cpu_percent = cpu_metrics.get("usage")
    except Exception:
        pass

    try:
        ram_metrics = ram_monitor.get_metrics()
        if ram_metrics.get("available") is True:
            ram_percent = ram_metrics.get("usage")
    except Exception:
        pass

    decision = decide_admission(cpu_percent, ram_percent)
    return {
        "decision": decision.admission,
        "reason": decision.reason,
        "metrics": {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/admission", dependencies=[Depends(require_api_key)])
async def get_admission():
    admission = await get_current_admission()
    return {"admission": admission["decision"], **admission}


@router.post("/requests")
async def intake_request(
    body: RequestIntake,
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
    admission: dict = Depends(get_current_admission),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
):
    if idempotency_key is not None:
        existing = get_idempotent_request(db, api_key.id, idempotency_key)
        if existing is not None:
            if existing.requested_url != body.requested_url:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key was already used for another request.",
                )
            return {
                "decision": "QUEUE",
                "reason": "IDEMPOTENT_REPLAY",
                "request_id": str(existing.id),
                "queue_position": existing.queue_position,
                "status": existing.status,
            }

    decision = admission["decision"]
    response = {"decision": decision, "reason": admission["reason"]}
    if decision in {"ALLOW", "REJECT"}:
        return response
    if decision != "QUEUE":
        raise HTTPException(status_code=503, detail="Admission policy returned an invalid decision.")

    try:
        request = add_to_queue(
            db,
            api_key.id,
            body.requested_url,
            idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        **response,
        "request_id": str(request.id),
        "queue_position": request.queue_position,
        "status": request.status,
    }


@router.get("/requests/{request_id}")
def request_status(
    request_id: int,
    api_key: ApiKey = Depends(require_api_key),
    db: Session = Depends(get_db),
):
    request = db.query(Request).filter(
        Request.id == request_id,
        Request.user_id == api_key.id,
    ).first()
    if request is None:
        raise HTTPException(status_code=404, detail="Queued request not found.")
    return {
        "request_id": str(request.id),
        "status": request.status,
        "queue_position": request.queue_position,
    }
