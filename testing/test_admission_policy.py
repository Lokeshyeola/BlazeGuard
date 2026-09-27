import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.admission_policy import decide_admission
from backend.admission_routes import get_admission


class AdmissionPolicyTests(unittest.TestCase):
    def run_current_admission(self, demo_mode=None, cpu_usage=20.0, ram_usage=30.0):
        async def run():
            from backend.admission_service import get_current_admission

            return await get_current_admission()

        environment = (
            {} if demo_mode is None
            else {"BLAZEGUARD_DEMO_ADMISSION_MODE": demo_mode}
        )
        with (
            patch.dict(os.environ, environment, clear=demo_mode is None),
            patch(
                "backend.admission_service.cpu_monitor.sample",
                new_callable=AsyncMock,
                return_value={"usage": cpu_usage, "available": True},
            ),
            patch(
                "backend.admission_service.ram_monitor.get_metrics",
                return_value={"usage": ram_usage, "available": True},
            ),
        ):
            return asyncio.run(run())

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

    def test_default_demo_mode_uses_real_admission_policy(self):
        response = self.run_current_admission()
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(response["reason"], "RESOURCES_AVAILABLE")

    def test_demo_mode_forces_each_supported_decision(self):
        for mode in ("ALLOW", "QUEUE", "REJECT"):
            with self.subTest(mode=mode):
                response = self.run_current_admission(mode, cpu_usage=99, ram_usage=99)
                self.assertEqual(response["decision"], mode)
                self.assertEqual(response["reason"], f"DEMO_OVERRIDE_{mode}")

    def test_demo_mode_is_case_insensitive_and_off_uses_real_policy(self):
        forced = self.run_current_admission("queue", cpu_usage=20, ram_usage=20)
        off = self.run_current_admission("OFF", cpu_usage=80, ram_usage=20)
        self.assertEqual(forced["decision"], "QUEUE")
        self.assertEqual(off["decision"], "QUEUE")
        self.assertEqual(off["reason"], "HIGH_RESOURCE_USAGE")


if __name__ == "__main__":
    unittest.main()
