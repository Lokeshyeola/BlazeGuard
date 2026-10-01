import asyncio
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request as HttpRequest
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api_key_routes import get_db, require_api_key
from backend.admission_service import get_current_admission
from backend.request_forwarder import forward_request, get_protected_service_config
from database.models import ApiKey, Request
from database.request_repository import current_queue_rank, get_idempotent_request
from queue_management.queue_manager import add_to_queue


router = APIRouter(prefix="/api/v1")


class RequestIntake(BaseModel):
    requested_url: str = Field(min_length=1, max_length=500)
    method: Literal["GET"] = "GET"


def get_http_request(request: HttpRequest) -> HttpRequest:
    return request


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
    http_request: HttpRequest = Depends(get_http_request),
):
    # DEMO-ONLY telemetry is inert without a running, explicitly enabled session.
    demo_simulator = (
        getattr(http_request.app.state, "demo_simulator", None)
        if hasattr(http_request, "app")
        else None
    )
    if demo_simulator is not None:
        demo_simulator.record_real_arrival()

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
    if demo_simulator is not None:
        demo_simulator.record_real_decision(decision)
    response = {"decision": decision, "reason": admission["reason"]}
    if decision == "ALLOW":
        # DEMO-ONLY post-admission routing: hold eligible real requests in the same FIFO
        # while the explicit demo session has work. The admission result remains ALLOW.
        queued = (
            demo_simulator.enqueue_real_if_holding(
                db,
                api_key.id,
                body.requested_url,
                body.method,
                idempotency_key,
            )
            if demo_simulator is not None
            else None
        )
        if queued is not None:
            return {
                **response,
                "request_id": str(queued.id),
                "queue_position": queued.queue_position,
                "current_queue_position": current_queue_rank(db, queued.id),
                "status": queued.status,
            }
        forwarded = await asyncio.to_thread(
            forward_request,
            SimpleNamespace(
                request_method=body.method,
                requested_url=body.requested_url,
            ),
            get_protected_service_config(),
        )
        result = {
            **response,
            "forwarding": {
                "succeeded": forwarded.succeeded,
                "status_code": forwarded.status_code,
            },
        }
        if not forwarded.succeeded:
            result["forwarding"]["error"] = forwarded.error
            return JSONResponse(status_code=502, content=result)
        return result
    if decision == "REJECT":
        return response
    if decision != "QUEUE":
        raise HTTPException(status_code=503, detail="Admission policy returned an invalid decision.")

    try:
        request = add_to_queue(
            db,
            api_key.id,
            body.requested_url,
            idempotency_key,
            body.method,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        **response,
        "request_id": str(request.id),
        "queue_position": request.queue_position,
        "current_queue_position": current_queue_rank(db, request.id),
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
        "current_queue_position": current_queue_rank(db, request.id),
        "attempt_count": request.attempt_count,
        "failure_category": request.failure_category,
        "failure_message": request.failure_message,
        "failure_at": request.failure_at.isoformat() + "Z" if request.failure_at else None,
        "upstream_status_code": request.upstream_status_code,
    }
