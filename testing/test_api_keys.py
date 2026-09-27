import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

class ApiKeyLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="blazeguard-api-test-", ignore_cleanup_errors=True)
        cls.work = Path(cls.temp.name)
        cls.token = secrets.token_urlsafe(32)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env["BLAZEGUARD_ADMIN_TOKEN"] = cls.token
        env["QUEUE_WORKER_ENABLED"] = "false"
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        cls.log_path = cls.work / "server.log"
        cls.log = cls.log_path.open("w+", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(cls.port)], cwd=cls.work, env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        import requests
        for _ in range(100):
            if cls.proc.poll() is not None:
                raise RuntimeError("FastAPI failed to start; inspect test server output.")
            try:
                if requests.get(cls.base + "/", timeout=.3).ok: break
            except requests.RequestException: time.sleep(.1)
        else: raise RuntimeError("FastAPI did not become ready.")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try: cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired: cls.proc.kill(); cls.proc.wait(timeout=5)
        cls.log.close()
        cls.temp.cleanup()

    def test_admin_auth_required(self):
        self.assertEqual(requests.get(self.base + "/api/v1/api-keys", timeout=3).status_code, 401)
        response = requests.get(self.base + "/api/v1/api-keys", headers={"Authorization": "Bearer " + self.token}, timeout=3)
        self.assertEqual(response.status_code, 200)

    def test_key_hash_auth_invalid_and_revoke(self):
        admin = {"Authorization": "Bearer " + self.token}
        created = requests.post(self.base + "/api/v1/api-keys", headers=admin, timeout=3)
        self.assertEqual(created.status_code, 201)
        result = created.json()
        secret, key_id = result["api_key"], result["key_id"]
        self.assertTrue(secret.startswith("BG_" + key_id + "_"))
        listed = requests.get(self.base + "/api/v1/api-keys", headers=admin, timeout=3)
        self.assertNotIn(secret, listed.text)
        self.assertNotIn("key_hash", listed.text)
        db = sqlite3.connect(self.work / "blazeguard.db")
        try:
            fields = [row[1] for row in db.execute("PRAGMA table_info(api_keys)")]
            self.assertNotIn("api_key", fields)
            digest = db.execute("SELECT key_hash FROM api_keys WHERE id=?", (key_id,)).fetchone()[0]
        finally:
            db.close()
        self.assertNotEqual(secret, digest)
        self.assertEqual(len(digest), 64)
        valid = {"Authorization": "Bearer " + secret}
        self.assertEqual(requests.get(self.base + "/api/v1/connection", headers=valid, timeout=3).status_code, 200)
        self.assertEqual(requests.get(self.base + "/api/v1/connection", headers={"Authorization": "Bearer BG_invalid_invalid"}, timeout=3).status_code, 401)
        revoked = requests.delete(self.base + "/api/v1/api-keys/" + key_id, headers=admin, timeout=3)
        self.assertEqual(revoked.json()["status"], "revoked")
        self.assertEqual(requests.get(self.base + "/api/v1/connection", headers=valid, timeout=3).status_code, 401)
        self.log.flush()
        self.assertNotIn(secret, self.log_path.read_text(encoding="utf-8"))

    def test_admission_route_requires_active_api_key(self):
        admin = {"Authorization": "Bearer " + self.token}
        self.assertEqual(
            requests.get(self.base + "/api/v1/admission", timeout=3).status_code,
            401,
        )
        request_url = self.base + "/api/v1/requests"
        status_url = self.base + "/api/v1/requests/not-a-number"
        request_body = {"requested_url": "https://example.test/resource"}
        self.assertEqual(requests.post(request_url, json=request_body, timeout=3).status_code, 401)
        self.assertEqual(requests.get(status_url, timeout=3).status_code, 401)
        self.assertEqual(
            requests.post(
                request_url,
                headers={"Authorization": "Bearer BG_invalid_invalid"},
                json=request_body,
                timeout=3,
            ).status_code,
            401,
        )
        created = requests.post(
            self.base + "/api/v1/api-keys", headers=admin, timeout=3
        )
        self.assertEqual(created.status_code, 201)
        api_key = created.json()["api_key"]
        auth = {"Authorization": "Bearer " + api_key}
        response = requests.get(
            self.base + "/api/v1/admission", headers=auth, timeout=3
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["admission"], {"ALLOW", "QUEUE", "REJECT"})
        self.assertIn("cpu_percent", payload["metrics"])
        self.assertIn("ram_percent", payload["metrics"])
        self.assertNotIn("api_key", payload)
        self.assertEqual(requests.get(status_url, headers=auth, timeout=3).status_code, 422)
        self.assertEqual(
            requests.get(self.base + "/api/v1/requests/999999", headers=auth, timeout=3).status_code,
            404,
        )

        db = sqlite3.connect(self.work / "blazeguard.db")
        try:
            before = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        finally:
            db.close()
        intake = requests.post(request_url, headers=auth, json=request_body, timeout=3)
        intake_payload = intake.json()
        self.assertIn(intake_payload["decision"], {"ALLOW", "QUEUE", "REJECT"})
        if intake_payload["decision"] == "ALLOW":
            self.assertEqual(intake.status_code, 502)
            self.assertFalse(intake_payload["forwarding"]["succeeded"])
        else:
            self.assertEqual(intake.status_code, 200)
        db = sqlite3.connect(self.work / "blazeguard.db")
        try:
            after = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        finally:
            db.close()
        if intake_payload["decision"] == "QUEUE":
            self.assertEqual(after, before + 1)
            self.assertEqual(intake_payload["status"], "WAITING")
            self.assertGreaterEqual(intake_payload["queue_position"], 1)
            status_response = requests.get(
                self.base + "/api/v1/requests/" + intake_payload["request_id"],
                headers=auth,
                timeout=3,
            )
            self.assertEqual(status_response.status_code, 200)
            self.assertEqual(status_response.json()["status"], "WAITING")
        else:
            self.assertEqual(after, before)

        key_id = created.json()["key_id"]
        requests.delete(
            self.base + "/api/v1/api-keys/" + key_id,
            headers=admin,
            timeout=3,
        )
        self.assertEqual(
            requests.get(self.base + "/api/v1/admission", headers=auth, timeout=3).status_code,
            401,
        )
        self.assertEqual(
            requests.post(request_url, headers=auth, json=request_body, timeout=3).status_code,
            401,
        )

    def test_existing_root_still_works(self):
        self.assertEqual(requests.get(self.base + "/", timeout=3).json()["status"], "active")

if __name__ == "__main__":
    unittest.main()
