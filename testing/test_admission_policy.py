import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.admission_policy import decide_admission
from backend.admission_routes import get_admission


class AdmissionPolicyTests(unittest.TestCase):
    def test_threshold_boundaries(self):
        cases = (
            (74, 74, "ALLOW"),
            (75, 50, "QUEUE"),
            (50, 75, "QUEUE"),
            (89, 89, "QUEUE"),
            (90, 50, "REJECT"),
            (50, 90, "REJECT"),
            (95, 95, "REJECT"),
        )
        for cpu, ram, expected in cases:
            with self.subTest(cpu=cpu, ram=ram):
                self.assertEqual(decide_admission(cpu, ram).admission, expected)

    def test_missing_and_invalid_metrics_fail_closed(self):
        invalid_cases = (
            (None, 20),
            (20, None),
            (float("nan"), 20),
            (20, float("inf")),
            (-0.1, 20),
            (20, 100.1),
            ("74", 20),
            (True, 20),
        )
        for cpu, ram in invalid_cases:
            with self.subTest(cpu=cpu, ram=ram):
                decision = decide_admission(cpu, ram)
                self.assertEqual(decision.admission, "REJECT")
                self.assertEqual(decision.reason, "MONITORING_UNAVAILABLE")

    def test_unavailable_cpu_monitor_does_not_allow(self):
        async def run():
            return await get_admission()

        with (
            patch(
                "backend.admission_service.cpu_monitor.sample",
                new_callable=AsyncMock,
                return_value={"usage": 0.0, "available": False},
            ),
            patch(
                "backend.admission_service.ram_monitor.get_metrics",
                return_value={"usage": 20.0, "available": True},
            ),
        ):
            response = asyncio.run(run())

        self.assertEqual(response["admission"], "REJECT")
        self.assertEqual(response["reason"], "MONITORING_UNAVAILABLE")
        self.assertIsNone(response["metrics"]["cpu_percent"])


if __name__ == "__main__":
    unittest.main()
