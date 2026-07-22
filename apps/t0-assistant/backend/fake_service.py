"""Minimal authenticated loopback service used by the W0 desktop skeleton.

This is deliberately a lifecycle fake, not a business backend. Production API,
WebSocket events, Sessions, market data, and Replay behavior belong to later
issues and project-owned packages.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    server_version = "StockPilotFakeService/0.1"

    def _authorized(self) -> bool:
        expected = getattr(self.server, "token", "")
        return bool(expected) and self.headers.get("Authorization") == f"Bearer {expected}"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/health":
            self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "unauthorized"})
            return
        self._json(
            HTTPStatus.OK,
            {
                "status": "ready",
                "service_generation": getattr(self.server, "service_generation", 1),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/shutdown":
            self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "unauthorized"})
            return
        self._json(HTTPStatus.ACCEPTED, {"status": "stopping"})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:
        # stdout/stderr remain diagnostics only; they never carry business data.
        return


def create_server(host: str, port: int, token: str, service_generation: int) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("fake service must bind to 127.0.0.1")
    if not token:
        raise ValueError("a per-launch token is required")
    server = ThreadingHTTPServer((host, port), _Handler)
    server.token = token  # type: ignore[attr-defined]
    server.service_generation = service_generation  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="StockPilot T+0 lifecycle fake service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--service-generation", default=1, type=int)
    args = parser.parse_args()
    token = os.environ.get("T0_SERVICE_TOKEN", "")
    server = create_server(args.host, args.port, token, args.service_generation)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
