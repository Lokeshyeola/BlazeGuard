import asyncio
import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from backend.admission_routes import RequestIntake, intake_request, request_status
from backend.queue_worker import QueueWorker, process_next_request_if_allowed
from backend.request_forwarder import (
    ForwardResult,
    ProtectedServiceConfig,
    forward_request,
)
from database.database import Base
from database.create_tables import create_tables
from database.models import Request
from database.request_repository import update_status
from queue_management.queue_manager import add_to_queue
from testing.fake_protected_result_server import (
    ProtectedResultHandler,
    ProtectedResultServer,
)


class QueueIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protected_server = ProtectedResultServer(
            ("127.0.0.1", 0), ProtectedResultHandler
        )
        cls.protected_thread = threading.Thread(
            target=cls.protected_server.serve_forever,
            daemon=True,
        )
        cls.protected_thread.start()
        cls.protected_url = f"http://127.0.0.1:{cls.protected_server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.protected_server.shutdown()
        cls.protected_server.server_close()
        cls.protected_thread.join(timeout=5)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blazeguard-queue-test-")
        database_path = Path(self.temp.name) / "test.db"
        self.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.sessions()
        self.api_key = SimpleNamespace(id="active-key-id")
        self.body = RequestIntake(requested_url="https://example.test/resource")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def request_count(self):
        return self.db.scalar(select(func.count()).select_from(Request))

    def submit(self, decision, idempotency_key=None, requested_url=None):
        admission = {"decision": decision, "reason": "TEST_REASON"}
        with patch(
            "backend.admission_routes.get_protected_service_config",
            return_value=ProtectedServiceConfig(self.protected_url),
        ):
            return asyncio.run(
                intake_request(
                    RequestIntake(requested_url=requested_url or self.body.requested_url),
                    self.api_key,
                    self.db,
                    admission,
                    idempotency_key,
                )
            )

    def test_allow_and_reject_are_not_queued(self):
        for decision in ("ALLOW", "REJECT"):
            with self.subTest(decision=decision):
                response = self.submit(
                    decision,
                    requested_url=f"{self.protected_url}/result?prn=123456",
                )
                if decision == "ALLOW":
                    self.assertTrue(response["forwarding"]["succeeded"])
                self.assertEqual(response["decision"], decision)
                self.assertEqual(response["reason"], "TEST_REASON")
                self.assertNotIn("request_id", response)
                self.assertEqual(self.request_count(), 0)

    def test_allow_immediately_forwards_success_without_queue_record(self):
        response = self.submit(
            "ALLOW",
            requested_url=f"{self.protected_url}/result?prn=123456",
        )
        self.assertEqual(response["decision"], "ALLOW")
        self.assertEqual(
            response["forwarding"],
            {"succeeded": True, "status_code": 200},
        )
        self.assertEqual(self.request_count(), 0)

    def test_allow_forwarding_failure_preserves_decision_and_reports_upstream_status(self):
        result = self.submit(
            "ALLOW",
            requested_url=f"{self.protected_url}/result?prn=service-error",
        )
        self.assertEqual(result.status_code, 502)
        payload = json.loads(result.body)
        self.assertEqual(payload["decision"], "ALLOW")
        self.assertEqual(payload["forwarding"]["succeeded"], False)
        self.assertEqual(payload["forwarding"]["status_code"], 503)
        self.assertEqual(payload["forwarding"]["error"], "HTTP_STATUS_ERROR")
        self.assertEqual(self.request_count(), 0)

    def test_queue_creates_one_record_and_status_is_queryable(self):
        response = self.submit("QUEUE")
        self.assertEqual(response["decision"], "QUEUE")
        self.assertEqual(response["status"], "WAITING")
        self.assertEqual(response["queue_position"], 1)
        self.assertEqual(self.request_count(), 1)

        status = request_status(int(response["request_id"]), self.api_key, self.db)
        self.assertEqual(
            status,
            {
                "request_id": response["request_id"],
                "status": "WAITING",
                "queue_position": 1,
                "attempt_count": 0,
                "failure_category": None,
                "failure_message": None,
                "failure_at": None,
                "upstream_status_code": None,
            },
        )
        with self.assertRaises(HTTPException) as missing:
            request_status(999, self.api_key, self.db)
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as wrong_owner:
            request_status(int(response["request_id"]), SimpleNamespace(id="another-key"), self.db)
        self.assertEqual(wrong_owner.exception.status_code, 404)

    def test_request_ids_and_queue_positions_are_unique_and_ordered(self):
        responses = [self.submit("QUEUE") for _ in range(5)]
        ids = [response["request_id"] for response in responses]
        positions = [response["queue_position"] for response in responses]
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(positions, [1, 2, 3, 4, 5])

    def test_idempotent_retry_returns_same_queue_record(self):
        first = self.submit("QUEUE", "retry-123")
        retry = self.submit("QUEUE", "retry-123")
        self.assertEqual(retry["request_id"], first["request_id"])
        self.assertEqual(retry["queue_position"], first["queue_position"])
        self.assertEqual(self.request_count(), 1)
        with self.assertRaises(HTTPException) as conflict:
            self.submit("QUEUE", "retry-123", "https://example.test/other")
        self.assertEqual(conflict.exception.status_code, 409)

    def test_concurrent_duplicate_enqueue_returns_one_record(self):
        def enqueue(_):
            with self.sessions() as session:
                record = add_to_queue(
                    session,
                    "active-key-id",
                    "https://example.test/resource",
                    "same-client-operation",
                )
                return record.id, record.queue_position

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(enqueue, range(8)))
        self.assertEqual(len({request_id for request_id, _ in values}), 1)
        self.assertEqual(len({position for _, position in values}), 1)
        self.assertEqual(self.request_count(), 1)

    def test_concurrent_enqueues_receive_unique_sequential_positions(self):
        def enqueue(_):
            with self.sessions() as session:
                record = add_to_queue(session, "active-key-id", "https://example.test/resource")
                return record.id, record.queue_position

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(enqueue, range(12)))
        ids, positions = zip(*values)
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(sorted(positions), list(range(1, 13)))
        self.assertEqual(self.request_count(), 12)

    def test_concurrent_intake_calls_keep_unique_ids_positions_and_rows(self):
        def intake(index):
            with self.sessions() as session:
                response = asyncio.run(
                    intake_request(
                        RequestIntake(requested_url=f"/resource/{index}"),
                        self.api_key,
                        session,
                        {"decision": "QUEUE", "reason": "TEST"},
                        None,
                    )
                )
                return response["request_id"], response["queue_position"], response["status"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            values = list(pool.map(intake, range(12)))
        ids, positions, statuses = zip(*values)
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(sorted(positions), list(range(1, 13)))
        self.assertEqual(set(statuses), {"WAITING"})
        self.assertEqual(self.request_count(), 12)

    def test_invalid_and_valid_status_transitions(self):
        record = add_to_queue(self.db, "active-key-id", "https://example.test/resource")
        with self.assertRaisesRegex(ValueError, "Invalid request status transition"):
            update_status(self.db, record.id, "COMPLETED")
        self.assertEqual(update_status(self.db, record.id, "PROCESSING").status, "PROCESSING")
        self.assertEqual(update_status(self.db, record.id, "COMPLETED").status, "COMPLETED")
        with self.assertRaisesRegex(ValueError, "Invalid request status transition"):
            update_status(self.db, record.id, "FAILED")
        with self.assertRaisesRegex(ValueError, "Unknown request status"):
            update_status(self.db, record.id, "UNKNOWN")

        waiting = add_to_queue(self.db, "active-key-id", "https://example.test/waiting")
        self.assertEqual(update_status(self.db, waiting.id, "CANCELLED").status, "CANCELLED")
        processing = add_to_queue(self.db, "active-key-id", "https://example.test/processing")
        update_status(self.db, processing.id, "PROCESSING")
        self.assertEqual(update_status(self.db, processing.id, "FAILED").status, "FAILED")
        processing_again = add_to_queue(
            self.db, "active-key-id", "https://example.test/processing-again"
        )
        update_status(self.db, processing_again.id, "PROCESSING")
        self.assertEqual(
            update_status(self.db, processing_again.id, "CANCELLED").status,
            "CANCELLED",
        )

    def test_concurrent_status_transitions_allow_only_one_winner(self):
        record = add_to_queue(self.db, "active-key-id", "https://example.test/race")
        update_status(self.db, record.id, "PROCESSING")

        def transition(target_status):
            with self.sessions() as session:
                try:
                    return update_status(session, record.id, target_status).status
                except ValueError:
                    return "REJECTED"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(transition, ("COMPLETED", "FAILED")))
        self.assertEqual(results.count("REJECTED"), 1)
        self.assertEqual(results.count("COMPLETED") + results.count("FAILED"), 1)
        with self.sessions() as session:
            final_status = session.get(Request, record.id).status
        self.assertIn(final_status, {"COMPLETED", "FAILED"})
        self.assertNotIn("PROCESSING", results)

    def test_existing_duplicate_waiting_positions_are_migrated(self):
        legacy_engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'legacy.db'}",
            connect_args={"check_same_thread": False},
        )
        with legacy_engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE requests ("
                "id INTEGER PRIMARY KEY, user_id VARCHAR(100) NOT NULL, "
                "requested_url VARCHAR(500) NOT NULL, queue_position INTEGER NOT NULL, "
                "status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO requests "
                "(id,user_id,requested_url,queue_position,status,created_at,updated_at) "
                "VALUES (1,'key','https://example.test/1',1,'WAITING',"
                "'2026-01-01','2026-01-01'), (2,'key','https://example.test/2',1,'WAITING',"
                "'2026-01-02','2026-01-02')"
            ))

        create_tables(legacy_engine)
        with legacy_engine.connect() as connection:
            positions = connection.execute(text(
                "SELECT queue_position FROM requests WHERE status='WAITING' ORDER BY id"
            )).scalars().all()
            columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(requests)")
            }
        self.assertEqual(positions, [1, 2])
        self.assertIn("idempotency_key", columns)
        self.assertIn("request_method", columns)
        self.assertIn("attempt_count", columns)
        self.assertIn("failure_category", columns)
        self.assertIn("upstream_status_code", columns)
        legacy_engine.dispose()

    def run_worker(self, decision, forwarding_config=None):
        async def admission():
            return {"decision": decision, "reason": "TEST"}

        with patch("backend.queue_worker.get_current_admission", new=admission):
            return asyncio.run(
                process_next_request_if_allowed(
                    self.db,
                    forwarding_config or ProtectedServiceConfig(self.protected_url),
                )
            )

    def test_worker_empty_queue_returns_none(self):
        self.assertIsNone(self.run_worker("ALLOW"))

    def test_worker_forwards_claimed_request_and_completes_it(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )
        claimed = self.run_worker("ALLOW")
        self.assertEqual(claimed.id, waiting.id)
        self.assertEqual(claimed.status, "COMPLETED")

    def test_worker_claims_in_fifo_order(self):
        queued = [
            add_to_queue(
                self.db,
                "active-key-id",
                f"{self.protected_url}/result?prn={number}",
            )
            for number in range(3)
        ]
        claimed = [self.run_worker("ALLOW") for _ in range(3)]
        self.assertEqual([item.id for item in claimed], [item.id for item in queued])
        self.assertTrue(all(item.status == "COMPLETED" for item in claimed))
        self.assertIsNone(self.run_worker("ALLOW"))

    def test_worker_does_not_claim_when_admission_is_not_allow(self):
        waiting = add_to_queue(self.db, "active-key-id", "https://example.test/held")
        for decision in ("QUEUE", "REJECT"):
            with self.subTest(decision=decision):
                self.assertIsNone(self.run_worker(decision))
                self.db.refresh(waiting)
                self.assertEqual(waiting.status, "WAITING")

    def test_worker_respects_demo_queue_override(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )
        with (
            patch.dict(
                os.environ,
                {"BLAZEGUARD_DEMO_ADMISSION_MODE": "QUEUE"},
                clear=True,
            ),
            patch(
                "backend.admission_service.cpu_monitor.sample",
                new_callable=AsyncMock,
                return_value={"usage": 10.0, "available": True},
            ),
            patch(
                "backend.admission_service.ram_monitor.get_metrics",
                return_value={"usage": 10.0, "available": True},
            ),
        ):
            self.assertEqual(os.environ.get("BLAZEGUARD_DEMO_ADMISSION_MODE"), "QUEUE")
            from backend.admission_service import get_current_admission
            admission = asyncio.run(get_current_admission())
            self.assertEqual(admission["decision"], "QUEUE")
            async def forced_queue():
                return admission
            with patch("backend.queue_worker.get_current_admission", new=forced_queue):
                result = asyncio.run(
                    process_next_request_if_allowed(
                        self.db,
                        ProtectedServiceConfig(self.protected_url),
                    )
                )
        self.assertIsNone(result)
        self.db.refresh(waiting)
        self.assertEqual(waiting.status, "WAITING")

    def test_worker_respects_demo_allow_override_and_processes_waiting_request(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )
        with (
            patch.dict(
                os.environ,
                {"BLAZEGUARD_DEMO_ADMISSION_MODE": "ALLOW"},
                clear=True,
            ),
            patch(
                "backend.admission_service.cpu_monitor.sample",
                new_callable=AsyncMock,
                return_value={"usage": 99.0, "available": True},
            ),
            patch(
                "backend.admission_service.ram_monitor.get_metrics",
                return_value={"usage": 99.0, "available": True},
            ),
        ):
            result = asyncio.run(
                process_next_request_if_allowed(
                    self.db,
                    ProtectedServiceConfig(self.protected_url),
                )
            )
        self.assertEqual(result.id, waiting.id)
        self.assertEqual(result.status, "COMPLETED")

    def test_worker_does_not_reclaim_processing_or_terminal_requests(self):
        processing = add_to_queue(
            self.db, "active-key-id", "https://example.test/processing"
        )
        update_status(self.db, processing.id, "PROCESSING")

        cancelled = add_to_queue(self.db, "active-key-id", "https://example.test/cancelled")
        update_status(self.db, cancelled.id, "CANCELLED")

        completed = add_to_queue(self.db, "active-key-id", "https://example.test/completed")
        update_status(self.db, completed.id, "PROCESSING")
        update_status(self.db, completed.id, "COMPLETED")

        failed = add_to_queue(self.db, "active-key-id", "https://example.test/failed")
        update_status(self.db, failed.id, "PROCESSING")
        update_status(self.db, failed.id, "FAILED")

        self.assertIsNone(self.run_worker("ALLOW"))

    def test_forwarder_sends_stored_path_and_query_to_configured_service(self):
        request = SimpleNamespace(
            request_method="GET",
            requested_url="http://untrusted.invalid/result?prn=123456",
        )
        result = forward_request(
            request,
            ProtectedServiceConfig(self.protected_url),
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.status_code, 200)

    def test_forwarder_rejects_encoded_path_traversal(self):
        for path in ("/%2e%2e/private", "/%252e%252e/private", "//untrusted.invalid/path"):
            with self.subTest(path=path):
                result = forward_request(
                    SimpleNamespace(request_method="GET", requested_url=path),
                    ProtectedServiceConfig(self.protected_url),
                )
                self.assertFalse(result.succeeded)
                self.assertEqual(result.error, "INVALID_TARGET")

    def test_forwarder_reports_protected_service_failure(self):
        request = SimpleNamespace(
            request_method="GET",
            requested_url="/result?prn=service-error",
        )
        result = forward_request(
            request,
            ProtectedServiceConfig(self.protected_url),
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.status_code, 503)

    def test_forwarder_enforces_timeout(self):
        request = SimpleNamespace(
            request_method="GET",
            requested_url="/result?prn=slow",
        )
        result = forward_request(
            request,
            ProtectedServiceConfig(self.protected_url, timeout_seconds=0.05),
        )
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error, "TIMEOUT")

    def test_successful_forwarding_completes_claimed_request(self):
        queued = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )
        completed = self.run_worker(
            "ALLOW",
            ProtectedServiceConfig(self.protected_url),
        )
        self.assertEqual(completed.id, queued.id)
        self.assertEqual(completed.status, "COMPLETED")

    def test_failed_forwarding_marks_claimed_request_failed(self):
        queued = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=service-error",
        )
        failed = self.run_worker(
            "ALLOW",
            ProtectedServiceConfig(self.protected_url),
        )
        self.assertEqual(failed.id, queued.id)
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.attempt_count, 1)
        self.assertEqual(failed.failure_category, "HTTP_STATUS_ERROR")
        self.assertEqual(failed.upstream_status_code, 503)
        self.assertIsNotNone(failed.failure_at)

    def test_failed_transport_persists_sanitized_diagnostic(self):
        queued = add_to_queue(self.db, "active-key-id", "/result?prn=private-value")
        with patch.dict(os.environ, {"QUEUE_WORKER_MAX_ATTEMPTS": "1"}):
            failed = self.run_worker(
                "ALLOW",
                ProtectedServiceConfig("http://127.0.0.1:1"),
            )
        self.assertEqual(failed.id, queued.id)
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.failure_category, "TRANSPORT_ERROR")
        self.assertEqual(failed.failure_message, "Could not connect to the protected service.")
        self.assertEqual(failed.upstream_status_code, None)
        self.assertNotIn("private-value", failed.failure_message)
        self.assertEqual(failed.attempt_count, 1)

    def test_invalid_target_and_configuration_are_persisted_as_distinct_categories(self):
        invalid_target = add_to_queue(self.db, "active-key-id", "/%252e%252e/private")
        with patch.dict(os.environ, {"QUEUE_WORKER_MAX_ATTEMPTS": "1"}):
            target_failure = self.run_worker("ALLOW", ProtectedServiceConfig(self.protected_url))
        self.assertEqual(target_failure.id, invalid_target.id)
        self.assertEqual(target_failure.status, "FAILED")
        self.assertEqual(target_failure.failure_category, "INVALID_TARGET")
        self.assertIsNone(target_failure.upstream_status_code)

        missing_config = add_to_queue(self.db, "active-key-id", "/api/health")
        with patch.dict(os.environ, {"QUEUE_WORKER_MAX_ATTEMPTS": "1"}):
            config_failure = self.run_worker("ALLOW", ProtectedServiceConfig(""))
        self.assertEqual(config_failure.id, missing_config.id)
        self.assertEqual(config_failure.failure_category, "SERVICE_NOT_CONFIGURED")
        self.assertIsNotNone(config_failure.failure_at)

    def test_unexpected_forwarding_exception_is_persisted_without_exception_text(self):
        queued = add_to_queue(self.db, "active-key-id", "/api/health")
        with patch(
            "backend.queue_worker.forward_request",
            side_effect=RuntimeError("sensitive upstream detail"),
        ):
            failed = self.run_worker("ALLOW")
        self.assertEqual(failed.id, queued.id)
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.failure_category, "INTERNAL_ERROR")
        self.assertNotIn("sensitive upstream detail", failed.failure_message)

    def test_transport_failure_retries_then_completes_once(self):
        queued = add_to_queue(self.db, "active-key-id", "/result?prn=retry")
        results = iter((
            ForwardResult(False, error="TRANSPORT_ERROR"),
            ForwardResult(True, status_code=200),
        ))
        with (
            patch.dict(
                os.environ,
                {"QUEUE_WORKER_MAX_ATTEMPTS": "3", "QUEUE_WORKER_RETRY_BACKOFF_SECONDS": "0"},
                clear=True,
            ),
            patch("backend.queue_worker.forward_request", side_effect=lambda *_: next(results)) as forward,
        ):
            completed = self.run_worker("ALLOW")
        self.assertEqual(completed.id, queued.id)
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.attempt_count, 2)
        self.assertEqual(completed.failure_category, "TRANSPORT_ERROR")
        self.assertEqual(forward.call_count, 2)

    def test_timeout_is_transient_and_can_recover(self):
        queued = add_to_queue(self.db, "active-key-id", "/result?prn=timeout-retry")
        results = iter((
            ForwardResult(False, error="TIMEOUT"),
            ForwardResult(True, status_code=200),
        ))
        with (
            patch.dict(
                os.environ,
                {"QUEUE_WORKER_MAX_ATTEMPTS": "2", "QUEUE_WORKER_RETRY_BACKOFF_SECONDS": "0"},
                clear=True,
            ),
            patch("backend.queue_worker.forward_request", side_effect=lambda *_: next(results)),
        ):
            completed = self.run_worker("ALLOW")
        self.assertEqual(completed.id, queued.id)
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.attempt_count, 2)
        self.assertEqual(completed.failure_category, "TIMEOUT")

    def test_repeated_transport_failures_stop_at_bounded_attempt_count(self):
        queued = add_to_queue(self.db, "active-key-id", "/result?prn=retry-limit")
        with (
            patch.dict(
                os.environ,
                {"QUEUE_WORKER_MAX_ATTEMPTS": "3", "QUEUE_WORKER_RETRY_BACKOFF_SECONDS": "0"},
                clear=True,
            ),
            patch(
                "backend.queue_worker.forward_request",
                return_value=ForwardResult(False, error="TRANSPORT_ERROR"),
            ) as forward,
        ):
            failed = self.run_worker("ALLOW")
        self.assertEqual(failed.id, queued.id)
        self.assertEqual(failed.status, "FAILED")
        self.assertEqual(failed.attempt_count, 3)
        self.assertEqual(forward.call_count, 3)

    def test_interrupted_processing_is_recovered_after_worker_restart(self):
        queued = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=recovered",
        )
        queued.status = "PROCESSING"
        queued.attempt_count = 1
        from datetime import datetime, timedelta
        queued.updated_at = datetime.utcnow() - timedelta(seconds=400)
        self.db.commit()

        with patch.dict(os.environ, {"QUEUE_WORKER_PROCESSING_TIMEOUT_SECONDS": "61"}):
            completed = self.run_worker("ALLOW")
        self.assertEqual(completed.id, queued.id)
        self.assertEqual(completed.status, "COMPLETED")
        self.assertEqual(completed.attempt_count, 2)

    def test_stale_processing_at_attempt_limit_becomes_visible_failure(self):
        queued = add_to_queue(self.db, "active-key-id", "/result?prn=interrupted")
        queued.status = "PROCESSING"
        queued.attempt_count = 3
        from datetime import datetime, timedelta
        queued.updated_at = datetime.utcnow() - timedelta(seconds=400)
        self.db.commit()
        with patch.dict(os.environ, {"QUEUE_WORKER_PROCESSING_TIMEOUT_SECONDS": "61"}):
            self.assertIsNone(self.run_worker("ALLOW"))
        self.db.refresh(queued)
        self.assertEqual(queued.status, "FAILED")
        self.assertEqual(queued.failure_category, "WORKER_INTERRUPTED")
        self.assertEqual(queued.attempt_count, 3)

    def test_concurrent_workers_claim_a_request_at_most_once(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )

        async def admission():
            return {"decision": "ALLOW", "reason": "TEST"}

        def run_worker():
            with self.sessions() as session:
                with patch("backend.queue_worker.get_current_admission", new=admission):
                    return asyncio.run(
                        process_next_request_if_allowed(
                            session,
                            ProtectedServiceConfig(self.protected_url),
                        )
                    )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_worker(), range(2)))
        claims = [item for item in results if item is not None]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].id, waiting.id)
        self.assertEqual(claims[0].status, "COMPLETED")
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_automatic_worker_starts_once_and_stops_cleanly(self):
        calls = []

        async def admission():
            calls.append(True)
            return {"decision": "REJECT", "reason": "TEST"}

        async def exercise():
            worker = QueueWorker(self.sessions, poll_interval_seconds=0.02)
            with patch("backend.queue_worker.get_current_admission", new=admission):
                first_task = worker.start()
                self.assertIs(worker.start(), first_task)
                await asyncio.sleep(0.06)
                await worker.stop()
            self.assertTrue(first_task.done())
            self.assertIsNone(worker.task)

        asyncio.run(exercise())
        self.assertGreaterEqual(len(calls), 2)

    def test_automatic_worker_keeps_queue_waiting_until_allow(self):
        waiting = add_to_queue(self.db, "active-key-id", "https://example.test/held")

        async def admission():
            return {"decision": "QUEUE", "reason": "TEST"}

        async def exercise():
            worker = QueueWorker(self.sessions, poll_interval_seconds=0.02)
            with patch("backend.queue_worker.get_current_admission", new=admission):
                worker.start()
                await asyncio.sleep(0.06)
                with self.sessions() as session:
                    self.assertEqual(session.get(Request, waiting.id).status, "WAITING")
                await worker.stop()

        asyncio.run(exercise())

    def test_automatic_worker_processes_once_when_admission_becomes_allow(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=123456",
        )
        decisions = iter(("QUEUE", "ALLOW", "ALLOW", "ALLOW"))
        forwarded_ids = []
        original_forwarder = forward_request

        async def admission():
            return {"decision": next(decisions, "ALLOW"), "reason": "TEST"}

        def count_forward(request, config):
            forwarded_ids.append(request.id)
            return original_forwarder(request, config)

        async def wait_for_status():
            for _ in range(200):
                with self.sessions() as session:
                    request = session.get(Request, waiting.id)
                    if request.status in {"COMPLETED", "FAILED"}:
                        return request.status
                await asyncio.sleep(0.01)
            return None

        async def exercise():
            worker = QueueWorker(
                self.sessions,
                poll_interval_seconds=0.02,
                forwarding_config=ProtectedServiceConfig(self.protected_url),
            )
            with (
                patch("backend.queue_worker.get_current_admission", new=admission),
                patch("backend.queue_worker.forward_request", new=count_forward),
            ):
                worker.start()
                status = await wait_for_status()
                await asyncio.sleep(0.06)
                await worker.stop()
            return status

        self.assertEqual(asyncio.run(exercise()), "COMPLETED")
        self.assertEqual(forwarded_ids, [waiting.id])

    def test_waiting_request_survives_worker_stop_and_restart(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=restart",
        )

        async def exercise():
            stopped = QueueWorker(self.sessions, poll_interval_seconds=0.01)
            with patch(
                "backend.queue_worker.get_current_admission",
                new=AsyncMock(return_value={"decision": "QUEUE", "reason": "TEST"}),
            ):
                stopped.start()
                await asyncio.sleep(0.04)
                await stopped.stop()
            with self.sessions() as session:
                self.assertEqual(session.get(Request, waiting.id).status, "WAITING")

            restarted = QueueWorker(
                self.sessions,
                poll_interval_seconds=0.01,
                forwarding_config=ProtectedServiceConfig(self.protected_url),
            )
            with patch(
                "backend.queue_worker.get_current_admission",
                new=AsyncMock(return_value={"decision": "ALLOW", "reason": "TEST"}),
            ):
                restarted.start()
                for _ in range(200):
                    with self.sessions() as session:
                        status = session.get(Request, waiting.id).status
                    if status in {"COMPLETED", "FAILED"}:
                        break
                    await asyncio.sleep(0.01)
                await restarted.stop()
            return status

        self.assertEqual(asyncio.run(exercise()), "COMPLETED")

    def test_automatic_worker_marks_forwarding_failure_failed(self):
        waiting = add_to_queue(
            self.db,
            "active-key-id",
            f"{self.protected_url}/result?prn=service-error",
        )

        async def admission():
            return {"decision": "ALLOW", "reason": "TEST"}

        async def exercise():
            worker = QueueWorker(
                self.sessions,
                poll_interval_seconds=0.02,
                forwarding_config=ProtectedServiceConfig(self.protected_url),
            )
            with patch("backend.queue_worker.get_current_admission", new=admission):
                worker.start()
                for _ in range(200):
                    with self.sessions() as session:
                        request = session.get(Request, waiting.id)
                        if request.status in {"COMPLETED", "FAILED"}:
                            status = request.status
                            break
                    await asyncio.sleep(0.01)
                else:
                    status = None
                await worker.stop()
            return status

        self.assertEqual(asyncio.run(exercise()), "FAILED")


if __name__ == "__main__":
    unittest.main()
