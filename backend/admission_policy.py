from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AdmissionDecision:
    admission: str
    reason: str


def _valid_percentage(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 100.0
    )


def decide_admission(cpu_percent: object, ram_percent: object) -> AdmissionDecision:
    """Apply admission bands to trusted server measurements; invalid data fails closed."""
    if not _valid_percentage(cpu_percent) or not _valid_percentage(ram_percent):
        return AdmissionDecision("REJECT", "MONITORING_UNAVAILABLE")

    if cpu_percent >= 90.0 or ram_percent >= 90.0:
        return AdmissionDecision("REJECT", "CRITICAL_RESOURCE_USAGE")

    if cpu_percent >= 75.0 or ram_percent >= 75.0:
        return AdmissionDecision("QUEUE", "HIGH_RESOURCE_USAGE")

    return AdmissionDecision("ALLOW", "RESOURCES_AVAILABLE")
