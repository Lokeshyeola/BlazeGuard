"""DEMO-ONLY authenticated local operator API. Remove this router with the demo layer."""

import hmac
import ipaddress
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request as HttpRequest
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.admission_routes import get_db
from backend.demo_simulator import DemoSimulator, demo_mode_enabled


router = APIRouter(prefix="/api/v1/operator", tags=["operator-demo"])


class SimulatorSettings(BaseModel):
    incoming_rate: int = Field(ge=1, le=500)
    capacity: int = Field(ge=1, le=100)


def require_operator(
    request: HttpRequest,
    authorization: str | None = Header(default=None),
) -> None:
    if not demo_mode_enabled():
        raise HTTPException(status_code=404, detail="Operator demo is disabled.")
    token = os.getenv("BLAZEGUARD_OPERATOR_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Operator demo credential is not configured.")
    if hmac.compare_digest(token, os.getenv("BLAZEGUARD_ADMIN_TOKEN", "")):
        raise HTTPException(status_code=503, detail="Operator credential must be separate from the administrator token.")
    supplied = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Operator authentication required.")
    host = request.client.host if request.client is not None else ""
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise HTTPException(status_code=403, detail="Operator demo is local-only.")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Operator demo is local-only.") from exc


def get_simulator(request: HttpRequest) -> DemoSimulator:
    return request.app.state.demo_simulator


@router.get("/state", dependencies=[Depends(require_operator)])
def current_operator_state(
    request: HttpRequest,
    db: Session = Depends(get_db),
):
    return get_simulator(request).snapshot(db)


@router.post("/simulator/start", dependencies=[Depends(require_operator)])
async def start_simulator(
    settings: SimulatorSettings,
    request: HttpRequest,
    db: Session = Depends(get_db),
):
    simulator = get_simulator(request)
    try:
        await simulator.start(settings.incoming_rate, settings.capacity)
    except RuntimeError as exc:
        if str(exc) == "DEMO_MODE_DISABLED":
            raise HTTPException(status_code=404, detail="Operator demo is disabled.") from exc
        if str(exc) == "DEMO_OUTSTANDING_LIMIT":
            raise HTTPException(status_code=429, detail="Outstanding demo-row limit reached; drain or reset demo work first.") from exc
        if str(exc) == "DEMO_DATA_LIMIT_REACHED":
            raise HTTPException(status_code=429, detail="Cumulative demo-data limit reached.") from exc
        raise HTTPException(status_code=409, detail="A demo session or shared queue drain is already active.") from exc
    return simulator.snapshot(db)


@router.post("/simulator/settings", dependencies=[Depends(require_operator)])
def update_simulator_settings(
    settings: SimulatorSettings,
    request: HttpRequest,
    db: Session = Depends(get_db),
):
    simulator = get_simulator(request)
    try:
        simulator.update_settings(settings.incoming_rate, settings.capacity)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="No active demo session to configure.") from exc
    return simulator.snapshot(db)


@router.post("/simulator/stop", dependencies=[Depends(require_operator)])
async def stop_simulator(request: HttpRequest, db: Session = Depends(get_db)):
    simulator = get_simulator(request)
    await simulator.stop()
    return simulator.snapshot(db)


@router.post("/simulator/reset", dependencies=[Depends(require_operator)])
async def reset_simulator(request: HttpRequest, db: Session = Depends(get_db)):
    simulator = get_simulator(request)
    await simulator.reset()
    return simulator.snapshot(db)
