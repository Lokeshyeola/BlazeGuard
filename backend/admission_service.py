from datetime import datetime, timezone
import os

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
    # TEST/DEMO ONLY: an explicit environment setting may override the real
    # monitor-based decision for local demonstrations. It is OFF by default.
    demo_mode = os.getenv("BLAZEGUARD_DEMO_ADMISSION_MODE", "").strip().upper()
    if demo_mode in {"ALLOW", "QUEUE", "REJECT"}:
        decision_name = demo_mode
        reason = f"DEMO_OVERRIDE_{demo_mode}"
    else:
        decision_name = decision.admission
        reason = decision.reason

    return {
        "decision": decision_name,
        "reason": reason,
        "metrics": {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
