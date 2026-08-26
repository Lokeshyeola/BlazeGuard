from fastapi import FastAPI
from pydantic import BaseModel

from backend.decision_engine.decision_engine import make_decision


app = FastAPI(title="BlazeGuard")


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

Get-Content monitoring\main.py -TotalCount 40