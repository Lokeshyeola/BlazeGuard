"""Process-local, OBSERVABILITY-ONLY counters for the removable operator dashboard."""

from collections import OrderedDict
import hashlib
import threading
import time


_IDEMPOTENCY_CACHE_LIMIT = 10_000
_IDEMPOTENCY_CACHE_TTL_SECONDS = 24 * 60 * 60


class RealTrafficTelemetry:
    """Count real public request outcomes without participating in request handling."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = self._empty_counts()
        # Bounded hashes deduplicate telemetry for ALLOW requests, which have no DB row.
        self._seen_idempotency: OrderedDict[bytes, float] = OrderedDict()

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {
            "incoming": 0,
            "allowed": 0,
            "queued": 0,
            "rejected": 0,
            "completed": 0,
            "failed": 0,
        }

    def record_incoming(
        self,
        user_id: str,
        idempotency_key: str | None,
        requested_url: str,
    ) -> bool:
        """Record one logical request; return False for a recent idempotent replay."""
        with self._lock:
            if idempotency_key is not None:
                signature = hashlib.sha256(
                    f"{user_id}\0{idempotency_key}\0{requested_url}".encode("utf-8")
                ).digest()
                now = time.monotonic()
                while self._seen_idempotency:
                    oldest, timestamp = next(iter(self._seen_idempotency.items()))
                    if now - timestamp <= _IDEMPOTENCY_CACHE_TTL_SECONDS:
                        break
                    self._seen_idempotency.pop(oldest)
                if signature in self._seen_idempotency:
                    return False
                self._seen_idempotency[signature] = now
                while len(self._seen_idempotency) > _IDEMPOTENCY_CACHE_LIMIT:
                    self._seen_idempotency.popitem(last=False)
            self._counts["incoming"] += 1
            return True

    def record_decision(self, decision: str) -> None:
        key = {"ALLOW": "allowed", "QUEUE": "queued", "REJECT": "rejected"}.get(decision)
        if key is not None:
            self._increment(key)

    def record_queued(self) -> None:
        """Record an ALLOW routed into the shared FIFO during demo queue hold."""
        self._increment("queued")

    def record_completed(self) -> None:
        self._increment("completed")

    def record_failure(self) -> None:
        """Count a failed forwarding attempt (including retryable worker attempts)."""
        self._increment("failed")

    def _increment(self, key: str) -> None:
        with self._lock:
            self._counts[key] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset_for_tests(self) -> None:
        """Reset in-memory values for isolated tests; not exposed by the operator API."""
        with self._lock:
            self._counts = self._empty_counts()
            self._seen_idempotency.clear()


real_traffic_telemetry = RealTrafficTelemetry()
