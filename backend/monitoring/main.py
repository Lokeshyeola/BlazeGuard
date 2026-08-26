import asyncio
import time
import uvicorn

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from config import config
from cpu_monitor import cpu_monitor
from ram_monitor import ram_monitor
from response_monitor import response_monitor
from request_monitor import request_monitor
from error_monitor import error_monitor
from network_monitor import network_monitor
from metrics_store import metrics_store
from health_engine import health_engine
from capacity_engine import capacity_engine
from capacity_manager import capacity_manager
from alert_manager import alert_manager


async def background_monitoring_task():
    while True:
        try:
            await cpu_monitor.sample()

            health = health_engine.calculate_overall_health()
            ram_metrics = ram_monitor.get_metrics()
            response_metrics = response_monitor.get_metrics()
            request_metrics = request_monitor.get_metrics()
            error_metrics = error_monitor.get_metrics()
            network_metrics = network_monitor.get_metrics()

            metrics = {
                "cpuUsage": cpu_monitor.get_metrics()["usage"],
                "ramUsage": ram_metrics["usage"],
                "averageResponseTime": response_metrics["average"],
                "errorRate": error_metrics["errorRate"],
                "requestsPerSecond": request_metrics["rps"],
                "networkBytesRx": network_metrics["bytesRx"],
                "networkBytesTx": network_metrics["bytesTx"],
                "healthScore": health["healthScore"],
            }

            metrics_store.add_sample(metrics)

            safe_capacity = capacity_engine.calculate_safe_capacity(
                health["healthScore"]
            )["safeCapacity"]

            alert_manager.evaluate(
                metrics,
                health["healthScore"],
                capacity_manager.get_active_users(),
                safe_capacity,
            )

        except Exception as exc:
            print(f"Monitoring Loop Exception: {exc}")

        await asyncio.sleep(config.monitoring_interval_ms / 1000.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    request_monitor.start_monitoring()
    monitor_task = asyncio.create_task(background_monitoring_task())

    try:
        yield
    finally:
        monitor_task.cancel()
        if request_monitor._task and not request_monitor._task.done():
            request_monitor._task.cancel()


app = FastAPI(title="BLAZEGUARD", lifespan=lifespan)


@app.middleware("http")
async def inbound_request_middleware(request: Request, call_next):
    if request.url.path in {"/health", "/api/monitor", "/docs", "/openapi.json"}:
        return await call_next(request)

    start_time = time.perf_counter()
    req_id = str(uuid4())

    request_monitor.record_request()

    capacity_res = await capacity_manager.acquire_slot(
        req_id,
        health_engine,
        metrics_store,
    )

    if capacity_res["status"] == "EXPIRED":
        return JSONResponse(
            status_code=530,
            content={
                "status": "EXPIRED",
                "message": "Queue wait time exceeded.",
            },
        )

    request.state.blazeguard_status = capacity_res["status"]

    try:
        response = await call_next(request)

        if response.status_code >= 400:
            error_monitor.record_failure()
        else:
            error_monitor.record_success()

        return response

    except Exception:
        error_monitor.record_failure()
        raise

    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response_monitor.record_response_time(duration_ms)

        await capacity_manager.release_slot(
            health_engine,
            metrics_store,
        )


@app.get("/health")
async def health_endpoint():
    health = health_engine.calculate_overall_health()

    if health["status"] == "CRITICAL":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "DEGRADED",
                "availability": "LIMITED",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    return {
        "status": "UP",
        "availability": "AVAILABLE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/monitor")
async def monitor_endpoint():
    health = health_engine.calculate_overall_health()

    capacity = capacity_engine.calculate_safe_capacity(
        health["healthScore"],
        metrics_store,
    )

    active_users = capacity_manager.get_active_users()
    safe_capacity = capacity["safeCapacity"]

    available_capacity = max(0, safe_capacity - active_users)

    utilization = (
        round((active_users / safe_capacity) * 100.0, 2)
        if safe_capacity > 0
        else 100.0
    )

    ram_metrics = ram_monitor.get_metrics()
    response_metrics = response_monitor.get_metrics()
    request_metrics = request_monitor.get_metrics()
    error_metrics = error_monitor.get_metrics()
    network_metrics = network_monitor.get_metrics()

    return {
        "project": "BLAZEGUARD",
        "health": {
            "score": health["healthScore"],
            "status": health["status"],
            "breakdown": health["breakdown"],
        },
        "capacity": {
            "baseCapacity": config.base_capacity,
            "safeCapacity": safe_capacity,
            "activeUsers": active_users,
            "availableCapacity": available_capacity,
            "waitingUsers": len(capacity_manager.queue),
            "utilization": utilization,
        },
        "metrics": {
            "cpuUsage": cpu_monitor.get_metrics()["usage"],
            "ramUsage": ram_metrics["usage"],
            "averageResponseTime": response_metrics["average"],
            "errorRate": error_metrics["errorRate"],
            "requestsPerSecond": request_metrics["rps"],
            "networkBytesRx": network_metrics["bytesRx"],
            "networkBytesTx": network_metrics["bytesTx"],
        },
        "alerts": alert_manager.get_alerts(),
        "serverAvailability": (
            "LIMITED"
            if health["status"] == "CRITICAL"
            else "AVAILABLE"
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_catchall(request: Request, path: str):
    return {
        "message": "Request successfully routed through BLAZEGUARD.",
        "status": getattr(request.state, "blazeguard_status", "UNKNOWN"),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.port)
