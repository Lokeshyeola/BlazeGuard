import math
from typing import Any, Dict

import psutil


class RamMonitor:
    def get_metrics(self) -> Dict[str, Any]:
        mem = psutil.virtual_memory()
        total_bytes = mem.total
        used_bytes = mem.used
        available_bytes = mem.available
        values_valid = all(
            isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
            for value in (total_bytes, used_bytes, available_bytes)
        )
        is_available = (
            values_valid
            and total_bytes > 0
            and used_bytes <= total_bytes
            and available_bytes <= total_bytes
        )
        usage_percentage = (
            (used_bytes / total_bytes) * 100.0
            if is_available
            else 0.0
        )
        is_available = is_available and math.isfinite(usage_percentage)

        return {
            "totalBytes": total_bytes,
            "usedBytes": used_bytes,
            "freeBytes": available_bytes,
            "usage": round(usage_percentage, 2) if is_available else None,
            "available": is_available,
        }

    def calculate_health_score(self) -> float:
        usage = self.get_metrics()["usage"]

        if usage < 60:
            return 100.0
        if usage <= 75:
            return 100.0 - ((usage - 60.0) / 15.0) * 15.0
        if usage <= 85:
            return 85.0 - ((usage - 75.0) / 10.0) * 25.0
        if usage <= 95:
            return 60.0 - ((usage - 85.0) / 10.0) * 30.0

        return max(0.0, 30.0 - ((usage - 95.0) / 5.0) * 30.0)


ram_monitor = RamMonitor()
