from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.parse import parse_qs, urlsplit


class ProtectedResultHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        self.server.seen_paths.append(self.path)
        if parsed.path == "/api/health":
            self._json(200, {"status": "ok"})
            return
        if parsed.path.startswith("/api/result/"):
            self._json(
                200,
                {
                    "exam": "bsc-cs-sem4-apr-2025",
                    "seat_number": "DEMO24017",
                    "student_name": "Aarav Kulkarni",
                    "result": "PASS",
                },
            )
            return
        if parsed.path != "/result":
            self.send_error(404)
            return

        prn = parse_qs(parsed.query).get("prn", [""])[0]
        if prn == "slow":
            time.sleep(0.3)
        if prn == "service-error":
            self.send_error(503, "Demo protected service unavailable")
            return
        if not prn:
            self.send_error(400, "PRN is required")
            return

        self._json(200, {"prn": prn, "student": "Demo Student", "result": "PASS"})

    def _json(self, status, data):
        response = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        try:
            self.wfile.write(response)
        except OSError:
            pass

    def log_message(self, _format, *_args):
        pass


class ProtectedResultServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_paths = []

    def handle_error(self, _request, _client_address):
        pass
