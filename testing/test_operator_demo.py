"""Focused tests for the removable, local-only operator demo layer."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.admission_routes import RequestIntake, intake_request
from backend.demo_simulator import (
    DEMO_OWNER_ID,
    DEMO_REQUEST_PATH,
    MAX_DEMO_DURATION_SECONDS,
    MAX_DEMO_GENERATION_RATE,
    MAX_DEMO_OUTSTANDING_REQUESTS,
    MAX_DEMO_REQUESTS_PER_SESSION,
    MAX_DEMO_SERVICE_RATE,
    MAX_DEMO_TOTAL_REQUESTS,
    DEMO_SESSION_STALE_AFTER_SECONDS,
    DemoSimulator,
    cleanup_stale_demo_rows,
)
from backend.operator_routes import require_operator
from backend.queue_worker import process_next_request_if_allowed
from backend.request_forwarder import ForwardResult
from database.database import Base
from database.models import OperatorDemoControl, Request
from database.operator_queries import estimate_wait_seconds
from database.request_repository import current_queue_rank, update_status
from queue_management.queue_manager import add_to_queue


class OperatorDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blazeguard-operator-demo-")
        path = Path(self.temp.name) / "demo.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.sessions()
        self.simulator = DemoSimulator(self.sessions)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def add(self, *, demo=False, session_id=None, url="/result"):
        return add_to_queue(
            self.db,
            "demo-owner" if demo else "real-key-id",
            url,
            is_demo=demo,
            demo_session_id=session_id,
        )

    def test_demo_mode_off_by_default_and_start_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEMO_MODE_DISABLED"):
                asyncio.run(self.simulator.start(10, 5))
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_only_one_demo_session_can_run(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "true"}):
                started = await self.simulator.start(2, 2)
                other_process_view = DemoSimulator(self.sessions)
                with self.assertRaisesRegex(RuntimeError, "DEMO_SESSION_ALREADY_ACTIVE"):
                    await other_process_view.start(2, 2)
                await self.simulator.stop()
                restarted_same_manager = await self.simulator.start(2, 2)
                await self.simulator.stop()
                restarted = await other_process_view.start(2, 2)
                await other_process_view.stop()
                return started, restarted_same_manager, restarted

        started, restarted_same_manager, restarted = asyncio.run(run())
        self.assertTrue(started["running"])
        self.assertTrue(restarted_same_manager["running"])
        self.assertTrue(restarted["running"])

    def test_generation_rate_and_request_count_are_server_bounded(self):
        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "ALLOW", "reason": "TEST"},
                ),
            ):
                await self.simulator.start(500, 100)
                session_id = self.simulator.state()["session_id"]
                for _ in range(MAX_DEMO_REQUESTS_PER_SESSION + 1):
                    await self.simulator._generate_one(session_id)
                await self.simulator.stop()
                return self.simulator.state()

        state = asyncio.run(run())
        self.assertEqual(state["effective_incoming_rate"], MAX_DEMO_GENERATION_RATE)
        self.assertEqual(state["effective_capacity"], MAX_DEMO_SERVICE_RATE)
        self.assertEqual(state["generated_attempts"], MAX_DEMO_REQUESTS_PER_SESSION)
        self.assertEqual(self.db.query(Request).count(), MAX_DEMO_REQUESTS_PER_SESSION)

    def test_outstanding_demo_row_limit_does_not_count_or_cancel_real_rows(self):
        real = self.add(url="/real/waiting")

        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch("backend.demo_simulator.MAX_DEMO_OUTSTANDING_REQUESTS", 1),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "ALLOW", "reason": "TEST"},
                ),
            ):
                await self.simulator.start(50, 10)
                session_id = self.simulator.state()["session_id"]
                await self.simulator._generate_one(session_id)
                await self.simulator._generate_one(session_id)
                state = self.simulator.state()
                await self.simulator.stop()
                return state

        state = asyncio.run(run())
        demo_rows = self.db.query(Request).filter(Request.is_demo.is_(True)).all()
        self.assertEqual(len(demo_rows), 1)
        self.assertEqual(state["limit_reason"], "OUTSTANDING_DEMO_ROW_LIMIT")
        self.assertEqual(self.db.get(Request, real.id).status, "WAITING")

    def test_cumulative_demo_row_limit_blocks_repeated_sessions(self):
        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch("backend.demo_simulator.MAX_DEMO_TOTAL_REQUESTS", 3),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "ALLOW", "reason": "TEST"},
                ),
            ):
                for index in range(3):
                    real = self.add(url=f"/real/completed/{index}")
                    update_status(self.db, real.id, "PROCESSING")
                    update_status(self.db, real.id, "COMPLETED")
                for _ in range(3):
                    await self.simulator.start(1, 1)
                    session_id = self.simulator.state()["session_id"]
                    await self.simulator._generate_one(session_id)
                    await self.simulator.stop()
                    row = self.db.query(Request).filter(Request.is_demo.is_(True)).order_by(Request.id.desc()).first()
                    update_status(self.db, row.id, "PROCESSING")
                    update_status(self.db, row.id, "COMPLETED")
                    self.simulator.worker_service_rate()  # allow the local drain to finish
                with self.assertRaisesRegex(RuntimeError, "DEMO_DATA_LIMIT_REACHED"):
                    await self.simulator.start(1, 1)

        asyncio.run(run())
        self.assertEqual(
            self.db.query(Request).filter(Request.is_demo.is_(True)).count(),
            3,
        )
        self.assertEqual(
            self.db.query(Request).filter(Request.is_demo.is_(False)).count(),
            3,
        )

    def test_session_stops_at_server_duration_limit(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                await self.simulator.start(1, 1)
                self.simulator._session.started_monotonic -= MAX_DEMO_DURATION_SECONDS + 1
                await asyncio.sleep(0.01)
                return self.simulator.state()
        state = asyncio.run(run())
        self.assertLessEqual(state["max_duration_seconds"], MAX_DEMO_DURATION_SECONDS)
        self.assertFalse(state["running"])
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_demo_rows_are_real_queue_rows_created_after_shared_admission(self):
        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "QUEUE", "reason": "SHARED_POLICY"},
                ) as admission,
            ):
                await self.simulator.start(1, 1)
                session_id = self.simulator.state()["session_id"]
                await self.simulator._generate_one(session_id)
                await self.simulator.stop()
                admission.assert_awaited_once_with()
                return session_id

        session_id = asyncio.run(run())
        row = self.db.query(Request).one()
        self.assertEqual(row.status, "WAITING")
        self.assertTrue(row.is_demo)
        self.assertEqual(row.demo_session_id, session_id)
        self.assertEqual(row.user_id, DEMO_OWNER_ID)
        self.assertEqual(row.requested_url, DEMO_REQUEST_PATH)

    def test_demo_reject_uses_same_admission_and_creates_no_row(self):
        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "REJECT", "reason": "SHARED_POLICY"},
                ),
            ):
                await self.simulator.start(1, 1)
                session_id = self.simulator.state()["session_id"]
                await self.simulator._generate_one(session_id)
                await self.simulator.stop()
                return self.simulator.state()

        state = asyncio.run(run())
        self.assertEqual(state["incoming"], 1)
        self.assertEqual(state["rejected"], 1)
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_real_allow_joins_the_same_fifo_while_demo_session_holds_queue(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                state = await self.simulator.start(1, 1)
                first = add_to_queue(
                    self.db, DEMO_OWNER_ID, DEMO_REQUEST_PATH,
                    is_demo=True, demo_session_id=state["session_id"],
                )
                http_request = SimpleNamespace(
                    app=SimpleNamespace(state=SimpleNamespace(demo_simulator=self.simulator))
                )
                result = await intake_request(
                    RequestIntake(requested_url="/student/result?prn=123"),
                    SimpleNamespace(id="real-key-id"),
                    self.db,
                    {"decision": "ALLOW", "reason": "RESOURCES_AVAILABLE"},
                    None,
                    http_request,
                )
                await self.simulator.stop()
                return first, result

        with patch("backend.admission_routes.forward_request") as forward:
            first, result = asyncio.run(run())
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["status"], "WAITING")
        self.assertEqual(result["queue_position"], first.queue_position + 1)
        self.assertEqual(result["current_queue_position"], 2)
        self.assertFalse(self.db.get(Request, int(result["request_id"])).is_demo)
        forward.assert_not_called()

    def test_real_allow_retry_preserves_idempotency_key_during_demo(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                state = await self.simulator.start(1, 1)
                demo_row = add_to_queue(
                    self.db, DEMO_OWNER_ID, DEMO_REQUEST_PATH,
                    is_demo=True, demo_session_id=state["session_id"],
                )
                request = SimpleNamespace(
                    app=SimpleNamespace(state=SimpleNamespace(demo_simulator=self.simulator))
                )
                body = RequestIntake(requested_url="/student/result?prn=123")
                key = "student-submit-001"
                first = await intake_request(
                    body, SimpleNamespace(id="real-key-id"), self.db,
                    {"decision": "ALLOW", "reason": "RESOURCES_AVAILABLE"}, key, request,
                )
                retry = await intake_request(
                    body, SimpleNamespace(id="real-key-id"), self.db,
                    {"decision": "ALLOW", "reason": "RESOURCES_AVAILABLE"}, key, request,
                )
                await self.simulator.stop()
                return demo_row, first, retry

        with patch("backend.admission_routes.forward_request") as forward:
            demo_row, first, retry = asyncio.run(run())
        real_rows = self.db.query(Request).filter(Request.is_demo.is_(False)).all()
        self.assertEqual(len(real_rows), 1)
        self.assertEqual(real_rows[0].idempotency_key, "student-submit-001")
        self.assertEqual(first["request_id"], retry["request_id"])
        self.assertEqual(retry["reason"], "IDEMPOTENT_REPLAY")
        self.assertEqual(first["queue_position"], demo_row.queue_position + 1)
        forward.assert_not_called()

    def test_real_and_demo_rows_are_fifo_without_demo_priority(self):
        demo = self.add(demo=True, session_id="session-a", url=DEMO_REQUEST_PATH)
        real = self.add(url="/student/result")
        self.assertLess(demo.queue_position, real.queue_position)
        self.assertEqual(current_queue_rank(self.db, real.id), 2)

    def test_demo_worker_completes_locally_without_forwarding(self):
        row = self.add(demo=True, session_id="session-a", url="/looks-like-a-real-path")
        async def run():
            with patch(
                "backend.queue_worker.get_current_admission",
                new_callable=AsyncMock,
                return_value={"decision": "ALLOW"},
            ):
                return await process_next_request_if_allowed(self.db)
        with patch("backend.queue_worker.forward_request") as forward:
            result = asyncio.run(run())
        self.assertEqual(result.id, row.id)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.attempt_count, 1)
        forward.assert_not_called()

    def test_real_worker_row_keeps_existing_forwarding_path(self):
        row = self.add(url="/student/result")
        async def run():
            with (
                patch(
                    "backend.queue_worker.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "ALLOW"},
                ),
                patch(
                    "backend.queue_worker.forward_request",
                    return_value=ForwardResult(True, status_code=200),
                ) as forward,
            ):
                result = await process_next_request_if_allowed(self.db)
                forward.assert_called_once()
                return result
        result = asyncio.run(run())
        self.assertEqual(result.id, row.id)
        self.assertEqual(result.status, "COMPLETED")

    def test_stop_prevents_future_generation(self):
        async def run():
            with (
                patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}),
                patch(
                    "backend.demo_simulator.get_current_admission",
                    new_callable=AsyncMock,
                    return_value={"decision": "ALLOW"},
                ),
            ):
                await self.simulator.start(1, 1)
                session_id = self.simulator.state()["session_id"]
                await self.simulator.stop()
                await self.simulator._generate_one(session_id)
                return self.simulator.state()
        state = asyncio.run(run())
        self.assertFalse(state["running"])
        self.assertEqual(state["generated_attempts"], 0)
        self.assertEqual(self.db.query(Request).count(), 0)

    def test_reset_cancels_only_session_demo_rows_and_keeps_completed_history(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                state = await self.simulator.start(1, 1)
                session_id = state["session_id"]
                waiting_demo = add_to_queue(
                    self.db, DEMO_OWNER_ID, DEMO_REQUEST_PATH,
                    is_demo=True, demo_session_id=session_id,
                )
                completed_demo = add_to_queue(
                    self.db, DEMO_OWNER_ID, DEMO_REQUEST_PATH,
                    is_demo=True, demo_session_id=session_id,
                )
                update_status(self.db, completed_demo.id, "PROCESSING")
                update_status(self.db, completed_demo.id, "COMPLETED")
                real = self.add(url="/real/waiting")
                await self.simulator.reset()
                restarted = await self.simulator.start(1, 1)
                await self.simulator.stop()
                return waiting_demo.id, completed_demo.id, real.id, restarted
        waiting_id, completed_id, real_id, restarted = asyncio.run(run())
        self.assertEqual(self.db.get(Request, waiting_id).status, "CANCELLED")
        self.assertEqual(self.db.get(Request, completed_id).status, "COMPLETED")
        self.assertEqual(self.db.get(Request, real_id).status, "WAITING")
        self.assertTrue(restarted["running"])

    def test_startup_cleanup_cancels_stale_demo_but_not_real_rows(self):
        stale = self.add(demo=True, session_id="stale-session")
        stale_processing = self.add(demo=True, session_id="stale-session")
        update_status(self.db, stale_processing.id, "PROCESSING")
        real = self.add(url="/real")
        count = cleanup_stale_demo_rows(self.sessions)
        self.assertEqual(count, 2)
        self.assertEqual(self.db.get(Request, stale.id).status, "CANCELLED")
        self.assertEqual(self.db.get(Request, stale_processing.id).status, "CANCELLED")
        self.assertEqual(self.db.get(Request, real.id).status, "WAITING")

    def test_stale_singleton_session_is_recovered_and_its_rows_are_cancelled(self):
        stale_id = "stale-demo-session"
        self.db.add(OperatorDemoControl(
            id=1,
            active_session_id=stale_id,
            active_started_at=datetime.utcnow() - timedelta(seconds=DEMO_SESSION_STALE_AFTER_SECONDS + 1),
        ))
        stale_demo = self.add(demo=True, session_id=stale_id)
        real = self.add(url="/real/keep")
        cancelled = cleanup_stale_demo_rows(self.sessions)
        self.assertEqual(cancelled, 1)
        self.assertEqual(self.db.get(Request, stale_demo.id).status, "CANCELLED")
        self.assertEqual(self.db.get(Request, real.id).status, "WAITING")
        self.db.expire_all()
        self.assertIsNone(self.db.get(OperatorDemoControl, 1).active_session_id)

        async def start_after_recovery():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                started = await self.simulator.start(1, 1)
                await self.simulator.stop()
                return started
        self.assertTrue(asyncio.run(start_after_recovery())["running"])

    def test_startup_cleanup_preserves_a_fresh_session_and_only_cancels_other_demo_rows(self):
        live_id = "live-demo-session"
        self.db.add(OperatorDemoControl(
            id=1,
            active_session_id=live_id,
            active_started_at=datetime.utcnow(),
        ))
        self.db.commit()
        live_demo = self.add(demo=True, session_id=live_id)
        orphan_demo = self.add(demo=True, session_id="orphan-session")
        real = self.add(url="/real/untouched")

        cancelled = cleanup_stale_demo_rows(self.sessions)
        self.assertEqual(cancelled, 1)
        self.db.expire_all()
        self.assertEqual(self.db.get(Request, live_demo.id).status, "WAITING")
        self.assertEqual(self.db.get(Request, orphan_demo.id).status, "CANCELLED")
        self.assertEqual(self.db.get(Request, real.id).status, "WAITING")
        self.assertEqual(self.db.get(OperatorDemoControl, 1).active_session_id, live_id)

        async def cannot_claim_again():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                with self.assertRaisesRegex(RuntimeError, "DEMO_SESSION_ALREADY_ACTIVE"):
                    await DemoSimulator(self.sessions).start(1, 1)
        asyncio.run(cannot_claim_again())

    def test_live_rank_compacts_after_prior_request_completes(self):
        first, second, third = [self.add(url=f"/r/{i}") for i in range(3)]
        self.assertEqual(current_queue_rank(self.db, third.id), 3)
        update_status(self.db, first.id, "PROCESSING")
        update_status(self.db, first.id, "COMPLETED")
        self.assertEqual(current_queue_rank(self.db, second.id), 1)
        self.assertEqual(current_queue_rank(self.db, third.id), 2)
        self.assertEqual(third.queue_position, 3)

    def test_estimated_wait_uses_queue_depth_and_service_rate(self):
        self.assertEqual(estimate_wait_seconds(87, 10), 8.7)
        self.assertIsNone(estimate_wait_seconds(87, 0))

    def test_capacity_change_updates_demo_worker_service_target(self):
        async def run():
            with patch.dict(os.environ, {"BLAZEGUARD_DEMO_ENABLED": "1"}):
                await self.simulator.start(10, 3)
                before = self.simulator.worker_service_rate()
                changed = self.simulator.update_settings(20, 100)
                after = self.simulator.worker_service_rate()
                await self.simulator.stop()
                return before, after, changed
        before, after, changed = asyncio.run(run())
        self.assertEqual(before, 3.0)
        self.assertEqual(after, float(MAX_DEMO_SERVICE_RATE))
        self.assertEqual(changed["effective_incoming_rate"], 20)

    def test_operator_api_requires_dedicated_local_credential(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        with patch.dict(os.environ, {
            "BLAZEGUARD_DEMO_ENABLED": "1",
            "BLAZEGUARD_OPERATOR_TOKEN": "operator-secret",
            "BLAZEGUARD_ADMIN_TOKEN": "different-admin-secret",
        }):
            with self.assertRaises(HTTPException) as missing:
                require_operator(request, None)
            self.assertEqual(missing.exception.status_code, 401)
            self.assertIsNone(require_operator(request, "Bearer operator-secret"))
            with self.assertRaises(HTTPException) as remote:
                require_operator(
                    SimpleNamespace(client=SimpleNamespace(host="203.0.113.1")),
                    "Bearer operator-secret",
                )
            self.assertEqual(remote.exception.status_code, 403)

    def test_operator_api_is_hidden_when_demo_feature_is_off(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as disabled:
                require_operator(request, "Bearer anything")
        self.assertEqual(disabled.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
