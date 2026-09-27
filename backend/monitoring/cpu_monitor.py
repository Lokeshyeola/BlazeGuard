import math
import os

import psutil


class CpuMonitor:
    def __init__(self):
        self.current_usage = 0.0
        self.available = False
        self.cores = os.cpu_count() or 1
        self._process = psutil.Process(os.getpid())

    async def sample(self) -> dict:
        try:
            measured_usage = self._process.cpu_percent(interval=None)
            if not math.isfinite(measured_usage) or measured_usage < 0:
                raise ValueError("CPU monitor returned an invalid sample.")
            self.current_usage = min(100.0, measured_usage)
            self.available = True
        except Exception:
            try:
                if not hasattr(os, "getloadavg"):
                    raise RuntimeError("CPU load average is unavailable.")
                load_avg = os.getloadavg()[0]
                if not math.isfinite(load_avg) or load_avg < 0:
                    raise ValueError("CPU load average is invalid.")
                self.current_usage = min(
                    100.0,
                    (load_avg / self.cores) * 100.0,
                )
                # A load-average estimate is not an actual CPU utilization reading.
                self.available = False
            except Exception:
                self.current_usage = 0.0
                self.available = False

        return self.get_metrics()

    def get_metrics(self) -> dict:
        load_avg = (
            list(os.getloadavg())
            if hasattr(os, "getloadavg")
            else [0.0, 0.0, 0.0]
        )

        return {
            "usage": round(self.current_usage, 2),
            "available": self.available,
            "cores": self.cores,
            "loadAvg": load_avg,
        }

    def calculate_health_score(self) -> float:
        usage = self.current_usage

        if usage < 60:
            return 100.0
        if usage <= 75:
            return 100.0 - ((usage - 60.0) / 15.0) * 15.0
        if usage <= 85:
            return 85.0 - ((usage - 75.0) / 10.0) * 25.0
        if usage <= 95:
            return 60.0 - ((usage - 85.0) / 10.0) * 30.0

        return max(0.0, 30.0 - ((usage - 95.0) / 5.0) * 30.0)


cpu_monitor = CpuMonitor()
