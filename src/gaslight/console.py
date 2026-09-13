"""Serve the gaslight console straight from the live in-process event bus.

The Wasmer Edge copy is fed by forwarding and is fine for a "runs anywhere"
demo, but it stores events in memory per serverless instance, so a POST and a
browser GET can land on different instances and it looks empty. This server
reads the very bus the proxy writes to, so what you see is always current.
The page HTML is reused verbatim from the Edge app.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _load_index() -> str:
    # Reuse the exact console UI shipped to Wasmer Edge.
    app = Path(__file__).resolve().parents[2] / "edge_app" / "app.py"
    src = app.read_text()
    marker = 'INDEX = r"""'
    start = src.index(marker) + len(marker)
    end = src.index('"""', start)
    return src[start:end]


INDEX = _load_index()


def serve_console(bus, host: str = "0.0.0.0", port: int = 18099):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/events"):
                self._send(200, json.dumps(bus.snapshot()), "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, INDEX, "text/html; charset=utf-8")
            elif self.path == "/healthz":
                self._send(200, json.dumps({"ok": True, "events": len(bus.snapshot())}),
                           "application/json")
            else:
                self._send(404, "{}", "application/json")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
