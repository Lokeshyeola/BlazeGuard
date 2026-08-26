from typing import Dict, Any, List


class ResponseMonitor:
    def __init__(self):
        self.samples: List[float] = []
        self.max_samples = 500
        self.slow_threshold_ms = 1000.0

    def record_response_time(self, duration_ms: float) -> None:
        self.samples.append(max(0.0, duration_ms))
        if len(self.samples) > self.max_samples:
            self.samples.pop(0)

    def get_metrics(self) -> Dict[str, Any]:
        if not self.samples:
            return {
                "average": 0.0,
                "min": 0.0,
                "max": 0.0,
                "slowRequests": 0,
                "count": 0,
            }

        avg = sum(self.samples) / len(self.samples)

        return {
            "average": round(avg, 2),
            "min": round(min(self.samples), 2),
            "max": round(max(self.samples), 2),
            "slowRequests": sum(
                1 for sample in self.samples
                if sample > self.slow_threshold_ms
            ),
            "count": len(self.samples),
        }

    def calculate_health_score(self) -> float:
        average = self.get_metrics()["average"]

        if average < 200:
            return 100.0
        if average <= 500:
            return 100.0 - ((average - 200.0) / 300.0) * 15.0
        if average <= 1000:
            return 85.0 - ((average - 500.0) / 500.0) * 25.0
        if average <= 2000:
            return 60.0 - ((average - 1000.0) / 1000.0) * 30.0

        return max(0.0, 30.0 - ((average - 2000.0) / 1000.0) * 30.0)


response_monitor = ResponseMonitor()
