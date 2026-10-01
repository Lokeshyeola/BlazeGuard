"""Focused tests for OBSERVABILITY-ONLY real traffic metrics."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.admission_routes import RequestIntake, intake_request
from backend.demo_simulator import DemoSimulator
from backend.operator_routes import with_traffic_telemetry
from backend.operator_telemetry import real_traffic_telemetry
from backend.queue_worker import process_next_request_if_allowed
from backend.request_forwarder import ForwardResult
from database.database import Base
from database.models import Request
from queue_management.queue_manager import add_to_queue


class OperatorTelemetryTests(unittest.TestCase):
    def setUp(self):
        real_traffic_telemetry.reset_for_tests()
        self.temp = tempfile.TemporaryDirectory(prefix="blazeguard-telemetry-")
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'telemetry.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.sessions()
        self.api_key = SimpleNamespace(id="telemetry-key")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()
        real_traffic_telemetry.reset_for_tests()

    def submit(self, decision, *, key=None, url="/protected/result", succeeded=True):
        result = ForwardResult(succeeded, status_code=200 if succeeded else 503,
                               error=None if succeeded else "HTTP_STATUS_ERROR")
        with patch("backend.admission_routes.forward_request", return_value=result):
            return asyncio.run(
                intake_request(
                    RequestIntake(requested_url=url),
                    self.api_key,
                    self.db,
                    {"decision": decision, "reason": "TEST"},
                    key,
                )
            )

    def test_real_allow_counts_incoming_allowed_and_successful_sync_completion(self):
        response = self.submit("ALLOW")
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(
            real_traffic_telemetry.snapshot(),
            {"incoming": 1, "allowed": 1, "queued": 0, "rejected": 0, "completed": 1, "failed": 0},
        )
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_real_reject_counts_without_queue_row(self):
        self.submit("REJECT")
        self.assertEqual(real_traffic_telemetry.snapshot()["rejected"], 1)
        self.assertEqual(real_traffic_telemetry.snapshot()["incoming"], 1)
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_real_queue_counts_and_keeps_existing_database_row(self):
        response = self.submit("QUEUE")
        row = self.db.get(Request, int(response["request_id"]))
        self.assertIsNotNone(row)
        self.assertFalse(row.is_demo)
        self.assertEqual(row.status, "WAITING")
        self.assertEqual(real_traffic_telemetry.snapshot()["queued"], 1)

    def test_real_queued_worker_completion_counts_once(self):
        queued = add_to_queue(self.db, "telemetry-key", "/protected/queued")

        async def admission():
            return {"decision": "ALLOW", "reason": "TEST"}

        with (
            patch("backend.queue_worker.get_current_admission", new=admission),
            patch("backend.queue_worker.forward_request", return_value=ForwardResult(True, 200)),
        ):
            completed = asyncio.run(process_next_request_if_allowed(self.db))
        self.assertEqual(completed.id, queued.id)
        self.assertEqual(real_traffic_telemetry.snapshot()["completed"], 1)

    def test_real_queued_worker_failure_counts_failed_forwarding_attempt(self):
        queued = add_to_queue(self.db, "telemetry-key", "/protected/fails")

        async def admission():
            return {"decision": "ALLOW", "reason": "TEST"}

        with (
            patch.dict(os.environ, {"QUEUE_WORKER_MAX_ATTEMPTS": "1"}),
            patch("backend.queue_worker.get_current_admission", new=admission),
            patch(
                "backend.queue_worker.forward_request",
                return_value=ForwardResult(False, 503, "HTTP_STATUS_ERROR"),
            ),
        ):
            failed = asyncio.run(process_next_request_if_allowed(self.db))
        self.assertEqual(failed.id, queued.id)
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(real_traffic_telemetry.snapshot()["failed"], 1)

    def test_failed_sync_forward_increments_failure_telemetry(self):
        response = self.submit("ALLOW", succeeded=False)
        self.assertEqual(response.status_code, 502)
        counts = real_traffic_telemetry.snapshot()
        self.assertEqual(counts["incoming"], 1)
        self.assertEqual(counts["allowed"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["completed"], 0)

    def test_idempotent_sync_retry_does_not_double_count_telemetry(self):
        first = self.submit("ALLOW", key="same-key")
        retry = self.submit("ALLOW", key="same-key")
        self.assertEqual(first["decision"], retry["decision"])
        self.assertEqual(real_traffic_telemetry.snapshot()["incoming"], 1)
        self.assertEqual(real_traffic_telemetry.snapshot()["allowed"], 1)
        self.assertEqual(real_traffic_telemetry.snapshot()["completed"], 1)

    def test_simulator_source_does_not_increment_real_counters_and_reset_preserves_them(self):
        real_traffic_telemetry.record_incoming("real", None, "/real")
        real_traffic_telemetry.record_decision("ALLOW")
        simulator = DemoSimulator(self.sessions)

        async def exercise():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                await simulator.start(1, 1)
                simulator._session.demo_incoming = 4
                simulator._session.demo_allowed = 3
                simulator._session.demo_queued = 3
                before = real_traffic_telemetry.snapshot()
                await simulator.reset()
                after = real_traffic_telemetry.snapshot()
                return before, after, simulator.state()

        before, after, state = asyncio.run(exercise())
        self.assertEqual(before, after)
        self.assertEqual(after["incoming"], 1)
        self.assertEqual(state["incoming"], 0)
        self.assertEqual(state["demo_incoming"], 0)

    def test_demo_worker_completion_does_not_increment_real_telemetry(self):
        demo = add_to_queue(
            self.db,
            "demo-owner",
            "/virtual/demo",
            is_demo=True,
            demo_session_id="demo-session",
        )

        async def admission():
            return {"decision": "ALLOW", "reason": "TEST"}

        with (
            patch("backend.queue_worker.get_current_admission", new=admission),
            patch("backend.queue_worker.forward_request") as forward,
        ):
            completed = asyncio.run(process_next_request_if_allowed(self.db))
        self.assertEqual(completed.id, demo.id)
        self.assertEqual(completed.status, "COMPLETED")
        forward.assert_not_called()
        self.assertEqual(real_traffic_telemetry.snapshot()["completed"], 0)

    def test_operator_state_combines_sources_but_keeps_queue_values_from_snapshot(self):
        real_traffic_telemetry.record_incoming("real", None, "/real")
        real_traffic_telemetry.record_decision("ALLOW")
        real_traffic_telemetry.record_completed()
        combined = with_traffic_telemetry({
            "demo_incoming": 2,
            "demo_allowed": 1,
            "demo_queued": 1,
            "demo_rejected": 1,
            "demo_completed": 1,
            "demo_failed": 0,
            "waiting": 7,
            "processing": 1,
            "queue_depth": 8,
        })
        self.assertEqual(combined["real_traffic"]["incoming"], 1)
        self.assertEqual(combined["demo_traffic"]["incoming"], 2)
        self.assertEqual(combined["combined"]["incoming"], 3)
        self.assertEqual(combined["combined"]["completed"], 2)
        self.assertEqual(combined["combined"]["waiting"], 7)
        self.assertEqual(combined["combined"]["processing"], 1)
        self.assertEqual(combined["combined"]["queue_depth"], 8)


if __name__ == "__main__":
    unittest.main()
