"""Formal local-service transport bootstrap for the T+0 desktop application.

Electron owns this process and supplies a loopback port, service generation,
and per-launch credential. Domain command handlers are registered by later
Backend issues; until then, recognized commands fail explicitly instead of
returning fixture-backed success.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import select
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


APP_COMMANDS = {
    "select_security",
    "get_live_snapshot",
    "retry_live",
    "list_trades",
    "create_trade",
    "update_trade",
    "delete_trade",
    "get_preferences",
    "save_preferences",
}
REPLAY_COMMANDS = {
    "select_symbol",
    "begin_replay",
    "set_replay_playback",
    "set_replay_speed",
    "step_replay",
    "seek_replay",
    "end_replay",
    "get_replay_snapshot",
}


class DesktopServiceServer(ThreadingHTTPServer):
    """Loopback-only transport server owned by one Electron App instance."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        service_generation: int,
    ) -> None:
        super().__init__(server_address, _Handler)
        self.token = token
        self.service_generation = service_generation
        self.shutdown_event = threading.Event()
        self._websocket_lock = threading.Lock()
        self._active_websockets = 0

    @property
    def active_websockets(self) -> int:
        with self._websocket_lock:
            return self._active_websockets

    def websocket_connected(self) -> None:
        with self._websocket_lock:
            self._active_websockets += 1

    def websocket_disconnected(self) -> None:
        with self._websocket_lock:
            self._active_websockets -= 1

    def shutdown(self) -> None:
        self.shutdown_event.set()
        super().shutdown()


class _Handler(BaseHTTPRequestHandler):
    server_version = "StockPilotDesktopService/1"

    def _loopback_host(self) -> bool:
        return self.headers.get("Host", "").split(":", 1)[0] == "127.0.0.1"

    def _authorized(self) -> bool:
        expected = getattr(self.server, "token", "")
        return bool(expected) and self.headers.get("Authorization") == f"Bearer {expected}"

    def _websocket_authorized(self) -> bool:
        expected = getattr(self.server, "token", "")
        offered = {
            item.strip()
            for item in self.headers.get("Sec-WebSocket-Protocol", "").split(",")
        }
        return bool(expected) and f"stockpilot-auth.{expected}" in offered

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/events":
            self._serve_websocket()
            return
        if self.path != "/health":
            self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        if not self._loopback_host() or not self._authorized():
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
        if not self._loopback_host() or not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "unauthorized"})
            return
        if self.path == "/shutdown":
            self._json(HTTPStatus.ACCEPTED, {"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        prefix = "/api/commands/"
        if not self.path.startswith(prefix):
            self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        command = self.path[len(prefix) :]
        if command not in APP_COMMANDS | REPLAY_COMMANDS:
            self._json(HTTPStatus.NOT_FOUND, {"status": "unknown_command"})
            return
        request = self._read_request()
        if request is None:
            return
        self._service_unavailable(command, request)

    def _read_request(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 0 or content_length > 1_048_576:
                raise ValueError("request body exceeds limit")
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"status": "invalid_json"})
            return None

    def _service_unavailable(self, command: str, request: dict[str, Any]) -> None:
        request_id = request.get("request_id", "missing-request-id")
        error = {
            "error_code": "service_unavailable",
            "category": "service",
            "severity": "error",
            "retryable": True,
            "affected_capability": "service",
            "message": "本地业务服务尚未接入",
            "request_id": request_id,
            "details": {},
        }
        if command in APP_COMMANDS:
            payload = {
                "schema_version": "t0_app_v1",
                "request_id": request_id,
                "accepted": False,
                "operation_id": None,
                "data": None,
                "error": error,
            }
        else:
            payload = error
        self._json(HTTPStatus.SERVICE_UNAVAILABLE, payload)

    def _serve_websocket(self) -> None:
        if (
            not self._loopback_host()
            or not self._websocket_authorized()
            or self.headers.get("Upgrade", "").lower() != "websocket"
        ):
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "unauthorized"})
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "missing_websocket_key"})
            return
        accept = base64.b64encode(
            hashlib.sha1(
                f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode()
            ).digest()
        ).decode()
        protocol = f"stockpilot-auth.{getattr(self.server, 'token', '')}"
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.send_header("Sec-WebSocket-Protocol", protocol)
        self.end_headers()
        self._send_websocket_text(
            json.dumps(
                {
                    "schema_version": "t0_app_v1",
                    "service_generation": getattr(
                        self.server, "service_generation", 1
                    ),
                    "session_id": None,
                    "revision": 0,
                    "event_type": "service_status",
                    "payload": {
                        "state": "connected",
                        "message": "本地服务事件通道已连接",
                    },
                },
                ensure_ascii=False,
            )
        )
        self.server.websocket_connected()
        try:
            while not self.server.shutdown_event.is_set():
                readable, _, _ = select.select([self.connection], [], [], 0.25)
                if not readable:
                    continue
                try:
                    data = self.connection.recv(4096)
                except OSError:
                    return
                if not data or data[0] & 0x0F == 0x08:
                    return
            try:
                self.wfile.write(b"\x88\x00")
                self.wfile.flush()
            except OSError:
                pass
        finally:
            self.server.websocket_disconnected()

    def _send_websocket_text(self, message: str) -> None:
        payload = message.encode("utf-8")
        if len(payload) < 126:
            header = bytes((0x81, len(payload)))
        elif len(payload) <= 0xFFFF:
            header = bytes((0x81, 126)) + len(payload).to_bytes(2, "big")
        else:
            header = bytes((0x81, 127)) + len(payload).to_bytes(8, "big")
        self.wfile.write(header + payload)
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    host: str,
    port: int,
    token: str,
    service_generation: int,
) -> DesktopServiceServer:
    if host != "127.0.0.1":
        raise ValueError("desktop service must bind to 127.0.0.1")
    if not token:
        raise ValueError("a per-launch token is required")
    return DesktopServiceServer((host, port), token, service_generation)


def main() -> None:
    parser = argparse.ArgumentParser(description="StockPilot T+0 desktop service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--service-generation", default=1, type=int)
    args = parser.parse_args()
    server = create_server(
        args.host,
        args.port,
        os.environ.get("T0_SERVICE_TOKEN", ""),
        args.service_generation,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
