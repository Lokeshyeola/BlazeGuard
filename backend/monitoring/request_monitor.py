import asyncio
from typing import Dict, Any, List, Optional


class RequestMonitor:
    def __init__(self):
        self.request_counts: List[int] = []
        self.current_second_count = 0
        self.last_second_rps = 0
        self.previous_rps = 0
        self._task: Optional[asyncio.Task] = None

    def start_monitoring(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._ticker())

    async def _ticker(self):
        while True:
            await asyncio.sleep(1.0)
            self.previous_rps = self.last_second_rps
            self.last_second_rps = self.current_second_count
            self.request_counts.append(self.current_second_count)

            if len(self.request_counts) > 60:
                self.request_counts.pop(0)

            self.current_second_count = 0

    def record_request(self) -> None:
        self.current_second_count += 1

    def get_metrics(self) -> Dict[str, Any]:
        surge_ratio = (
            1.0
            if self.previous_rps == 0
            else self.last_second_rps / self.previous_rps
        )

        return {
            "rps": self.last_second_rps,
            "previousRps": self.previous_rps,
            "surgeRatio": round(surge_ratio, 2),
        }

    def calculate_health_score(self) -> float:
        surge_ratio = self.get_metrics()["surgeRatio"]

        if surge_ratio <= 1.5:
            return 100.0
        if surge_ratio <= 2.5:
            return 80.0
        if surge_ratio <= 4.0:
            return 60.0
        return 40.0


request_monitor = RequestMonitor()
