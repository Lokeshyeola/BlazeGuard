from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api_key_routes import get_db, require_api_key
from backend.admission_service import get_current_admission
from database.models import ApiKey, Request
from database.request_repository import get_idempotent_request
from queue_management.queue_manager import add_to_queue


router = APIRouter(prefix="/api/v1")


class RequestIntake(BaseModel):
    requested_url: str = Field(min_length=1, max_length=500)


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
