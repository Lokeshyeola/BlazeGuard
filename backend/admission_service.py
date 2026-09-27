from datetime import datetime, timezone

from backend.admission_policy import decide_admission
from backend.monitoring.cpu_monitor import cpu_monitor
from backend.monitoring.ram_monitor import ram_monitor


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
