from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.decision_engine.decision_engine import make_decision
from backend.monitoring.cpu_monitor import cpu_monitor
from backend.monitoring.ram_monitor import ram_monitor


app = FastAPI(title="BlazeGuard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ServerMetrics(BaseModel):
    cpu_percent: float
    ram_percent: float
    eta_seconds: float


@app.get("/")
def home():
    return {
        "message": "BlazeGuard is running",
        "status": "active"
    }


@app.post("/decision")
def get_decision(metrics: ServerMetrics):
    decision = make_decision(
        metrics.cpu_percent,
        metrics.ram_percent,
        metrics.eta_seconds
    )

    return {
        "decision": decision
    }


@app.get("/system-status")
async def get_system_status(
    eta_seconds: float = Query(default=0, ge=0),
):
    """Sample real server metrics and apply the existing decision engine."""
    await cpu_monitor.sample()
    cpu_metrics = cpu_monitor.get_metrics()
    ram_metrics = ram_monitor.get_metrics()
    decision = make_decision(
        cpu_metrics["usage"],
        ram_metrics["usage"],
        eta_seconds,
    )

    return {
        "decision": decision,
        "metrics": {
            "cpu_percent": cpu_metrics["usage"],
            "ram_percent": ram_metrics["usage"],
            "eta_seconds": eta_seconds,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

