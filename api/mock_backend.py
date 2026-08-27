"""
A tiny FAKE BlazeGuard backend, running locally, just so you can see
the real request/response cycle happen with your own eyes.
This is NOT the real BlazeGuard — it's a stand-in until we have
the real one, so the demo works without internet access.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class MockBlazeGuard(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        if self.path == "/status":
            self._send(200, {"system": "BlazeGuard", "status": "operational", "threats_detected": 2})
        elif self.path.startswith("/alerts"):
            self._send(200, {"alerts": [{"id": 1, "severity": "high", "msg": "Suspicious login attempt"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/scan":
            self._send(200, {"scan_id": "scan_001", "status": "started", "config": body})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, format, *args):
        pass  # silence default logging

if __name__ == "__main__":
    server = HTTPServer(("localhost", 8000), MockBlazeGuard)
    server.serve_forever()
