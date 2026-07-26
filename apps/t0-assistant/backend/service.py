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
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.marketdata.repositories.securities_store import SecuritiesStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services import SecuritiesSearchService
from packages.t0assistant.replay import REPLAY_COMMANDS, ReplayCommandApi


APP_COMMANDS = {
    "search_securities",
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
class DesktopServiceServer(ThreadingHTTPServer):
    """Loopback-only transport server owned by one Electron App instance."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        token: str,
        service_generation: int,
        search_service: SecuritiesSearchService | None = None,
        replay_api: ReplayCommandApi | None = None,
    ) -> None:
        if (
            replay_api is not None
            and replay_api.service_generation != service_generation
        ):
            raise ValueError(
                "Replay API service_generation must match the desktop service"
            )
        super().__init__(server_address, _Handler)
        self.token = token
        self.service_generation = service_generation
        self.search_service = search_service
        self.replay_api = replay_api
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
        if command == "search_securities":
            self._search_securities(request)
            return
        if command in REPLAY_COMMANDS:
            self._replay_command(command, request)
            return
        self._service_unavailable(command, request)

    def _replay_command(self, command: str, request: dict[str, Any]) -> None:
        replay_api = getattr(self.server, "replay_api", None)
        if replay_api is None:
            self._service_unavailable(command, request)
            return
        result = replay_api.dispatch(command, request)
        self._json(HTTPStatus(result.status), result.payload)
        result.response_delivered()

    def _search_securities(self, request: dict[str, Any]) -> None:
        request_id = request.get("request_id", "missing-request-id")
        payload = request.get("payload")
        query = payload.get("query") if isinstance(payload, dict) else None
        limit = payload.get("limit") if isinstance(payload, dict) else None
        if (
            not isinstance(query, str)
            or not query.strip()
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            error = {
                "error_code": "invalid_request",
                "category": "validation",
                "severity": "error",
                "retryable": False,
                "affected_capability": "symbol_selection",
                "message": "证券搜索条件无效",
                "request_id": request_id,
                "details": {},
            }
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "schema_version": "t0_app_v1",
                    "request_id": request_id,
                    "accepted": False,
                    "operation_id": None,
                    "data": None,
                    "error": error,
                },
            )
            return
        try:
            service = getattr(self.server, "search_service", None)
            if service is None:
                paths = RuntimePaths()
                paths.ensure_dirs()
                service = SecuritiesSearchService(
                    SecuritiesStore(paths.db_dir / "market_data.sqlite")
                )
                self.server.search_service = service
            securities = service.search(query, limit=limit)
        except Exception:
            traceback.print_exc()
            error = {
                "error_code": "security_search_failed",
                "category": "data",
                "severity": "error",
                "retryable": True,
                "affected_capability": "symbol_selection",
                "message": "证券搜索暂时不可用",
                "request_id": request_id,
                "details": {},
            }
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "schema_version": "t0_app_v1",
                    "request_id": request_id,
                    "accepted": False,
                    "operation_id": None,
                    "data": None,
                    "error": error,
                },
            )
            return
        self._json(
            HTTPStatus.OK,
            {
                "schema_version": "t0_app_v1",
                "request_id": request_id,
                "accepted": True,
                "operation_id": None,
                "data": {"securities": securities},
                "error": None,
            },
        )

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
    search_service: SecuritiesSearchService | None = None,
    replay_api: ReplayCommandApi | None = None,
) -> DesktopServiceServer:
    if host != "127.0.0.1":
        raise ValueError("desktop service must bind to 127.0.0.1")
    if not token:
        raise ValueError("a per-launch token is required")
    return DesktopServiceServer(
        (host, port),
        token,
        service_generation,
        search_service=search_service,
        replay_api=replay_api,
    )


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
