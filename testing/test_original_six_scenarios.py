"""Regression coverage for the six locally demonstrated integration cases."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.admission_routes import RequestIntake, intake_request
from backend.queue_worker import process_next_request_if_allowed
from backend.request_forwarder import ProtectedServiceConfig
from database.database import Base
from database.models import Request
from queue_management.queue_manager import add_to_queue
from testing.fake_protected_result_server import ProtectedResultHandler, ProtectedResultServer


class OriginalSixScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ProtectedResultServer(("127.0.0.1", 0), ProtectedResultHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.protected_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blazeguard-six-scenarios-")
        engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'scenario.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(engine)
        self.engine = engine
        self.sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.sessions()
        self.api_key = SimpleNamespace(id="scenario-key")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def intake(self, decision, path):
        with patch(
            "backend.admission_routes.get_protected_service_config",
            return_value=ProtectedServiceConfig(self.protected_url),
        ):
            return asyncio.run(
                intake_request(
                    RequestIntake(requested_url=path),
                    self.api_key,
                    self.db,
                    {"decision": decision, "reason": f"DEMO_OVERRIDE_{decision}"},
                    None,
                )
            )

    def count_requests(self):
        return self.db.scalar(select(func.count()).select_from(Request))

    def test_1_allow_forwards_to_fictional_result_endpoint(self):
        path = "/api/result/bsc-cs-sem4-apr-2025/DEMO24017?mother_name=Leela%20Kulkarni"
        response = self.intake("ALLOW", path)
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(response["forwarding"], {"succeeded": True, "status_code": 200})
        self.assertIn(path, self.server.seen_paths)
        self.assertEqual(self.count_requests(), 0)

    def test_2_queue_returns_request_id_and_waiting_status(self):
        response = self.intake("QUEUE", "/api/health")
        self.assertEqual(response["decision"], "QUEUE")
        self.assertEqual(response["status"], "WAITING")
        self.assertTrue(response["request_id"].isdigit())
        record = self.db.get(Request, int(response["request_id"]))
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "WAITING")
        self.assertEqual(self.count_requests(), 1)

    def test_3_queue_worker_forwards_and_completes(self):
        queued = add_to_queue(
            self.db,
            self.api_key.id,
            "/api/result/bsc-cs-sem4-apr-2025/DEMO24017",
        )

        async def allow():
            return {"decision": "ALLOW", "reason": "TEST_ALLOW"}

        with patch("backend.queue_worker.get_current_admission", new=allow):
            result = asyncio.run(
                process_next_request_if_allowed(
                    self.db,
                    ProtectedServiceConfig(self.protected_url),
                )
            )
        self.assertEqual(result.id, queued.id)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.attempt_count, 1)
        self.assertIn("/api/result/bsc-cs-sem4-apr-2025/DEMO24017", self.server.seen_paths)

    def test_4_reject_does_not_queue_or_forward(self):
        with patch("backend.admission_routes.forward_request") as forward:
            response = self.intake("REJECT", "/api/health")
        self.assertEqual(response, {"decision": "REJECT", "reason": "DEMO_OVERRIDE_REJECT"})
        self.assertEqual(self.count_requests(), 0)
        forward.assert_not_called()

    def test_5_unavailable_upstream_returns_transport_error(self):
        with patch(
            "backend.admission_routes.get_protected_service_config",
            return_value=ProtectedServiceConfig(self.protected_url, 0.5),
        ), patch(
            "backend.request_forwarder.requests.request",
            side_effect=requests.ConnectionError("private upstream detail"),
        ):
            response = asyncio.run(
                intake_request(
                    RequestIntake(requested_url="/api/health"),
                    self.api_key,
                    self.db,
                    {"decision": "ALLOW", "reason": "TEST_ALLOW"},
                    None,
                )
            )
        self.assertEqual(response.status_code, 502)
        payload = json.loads(response.body)
        self.assertEqual(payload["forwarding"]["error"], "TRANSPORT_ERROR")
        self.assertFalse(payload["forwarding"]["succeeded"])
        self.assertEqual(self.count_requests(), 0)

    def test_6_target_uses_configuration_and_not_a_demo_host(self):
        result = self.intake("ALLOW", "http://untrusted.invalid/api/health")
        self.assertTrue(result["forwarding"]["succeeded"])
        self.assertIn("/api/health", self.server.seen_paths)
        forwarder_source = Path(__file__).resolve().parents[1] / "backend" / "request_forwarder.py"
        self.assertNotIn("result-portal-demo", forwarder_source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
