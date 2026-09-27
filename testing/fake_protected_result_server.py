from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.parse import parse_qs, urlsplit


class ProtectedResultHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
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

        response = json.dumps(
            {"prn": prn, "student": "Demo Student", "result": "PASS"}
        ).encode("utf-8")
        self.send_response(200)
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

    def handle_error(self, _request, _client_address):
        pass
