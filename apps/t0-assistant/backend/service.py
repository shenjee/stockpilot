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
import queue as _queue
import select
import sys
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

APP_CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

from packages.marketdata.repositories.securities_store import SecuritiesStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services import SecuritiesSearchService
from packages.t0assistant.replay import REPLAY_COMMANDS, ReplayCommandApi
from packages.t0assistant.repositories import (
    open_app_database,
    SqliteTradeRepository,
)
from packages.t0assistant.runtime.live_projection_store import (
    LiveProjectionSnapshotUnavailable as _LiveSnapshotUnavailable,
)
from packages.t0assistant.trading import TradeCommandApi, TradeService

# ``EventPublisher`` lives in this backend package. The import form depends on
# whether this module is imported as ``backend.service`` (tests, with the app
# root on ``sys.path``) or executed as a script (Electron, with the backend
# directory on ``sys.path``); resolve both.
try:  # package context: ``from backend.service import ...``
    from backend.event_publisher import EventPublisher
except ImportError:  # script context: ``python backend/service.py``
    from event_publisher import EventPublisher


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

_LIVE_COMMANDS = frozenset({"get_live_snapshot"})
_TRADE_COMMANDS = frozenset({"list_trades", "create_trade", "update_trade", "delete_trade"})


class LiveSnapshotApiPort(Protocol):
    """Transport-independent Live snapshot command boundary."""

    def get_live_snapshot(
        self,
        *,
        request_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Return a ``command_response`` payload for ``get_live_snapshot``."""


class LiveSnapshotApiError(RuntimeError):
    """Stable Live snapshot command rejection."""

    def __init__(
        self,
        error_code: str,
        *,
        message: str,
        retryable: bool = True,
    ) -> None:
        self.error_code = error_code
        self.user_message = message
        self.retryable = retryable
        super().__init__(message)


class LiveSnapshotApi:
    """Concrete Live snapshot API backed by a LiveProjectionStore.

    Validates the requested ``session_id`` against the store's current
    authoritative Session and returns a complete ``workbench_snapshot`` as the
    synchronous ``command_response.data`` payload.  Wrong Session, retired
    Session, or no baseline are rejected with structured ``application_error``.
    """

    def __init__(
        self,
        store: Any,
        *,
        service_generation: int,
    ) -> None:
        if not callable(getattr(store, "get_live_snapshot", None)):
            raise TypeError("store must implement get_live_snapshot")
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        self._store = store
        self._service_generation = service_generation

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def get_live_snapshot(
        self,
        *,
        request_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        if not isinstance(request_id, str) or not request_id:
            raise TypeError("request_id must be a non-empty string")
        if not isinstance(session_id, str) or not session_id:
            return self._reject(
                request_id, "invalid_request", "session_id is required", retryable=False
            )

        current = self._store.current_session
        if current is None or current[0] != session_id:
            return self._reject(
                request_id,
                "session_not_found",
                "Live Session not found or retired",
                retryable=False,
            )
        try:
            snapshot = self._store.get_live_snapshot(
                session_id=session_id,
                generation=current[1],
            )
        except _LiveSnapshotUnavailable:
            return self._reject(
                request_id,
                "session_not_found",
                "Live Session not found or retired",
                retryable=False,
            )
        except Exception:
            return self._reject(
                request_id,
                "service_unavailable",
                "Live snapshot is not available",
                retryable=True,
            )
        return {
            "schema_version": "t0_app_v1",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": snapshot,
            "error": None,
        }

    @staticmethod
    def _reject(
        request_id: str,
        error_code: str,
        message: str,
        *,
        retryable: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v1",
            "request_id": request_id,
            "accepted": False,
            "operation_id": None,
            "data": None,
            "error": {
                "error_code": error_code,
                "category": "session" if "session" in error_code else "service",
                "severity": "error",
                "retryable": retryable,
                "affected_capability": "live",
                "message": message,
                "request_id": request_id,
                "details": {},
            },
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
        live_snapshot_api: LiveSnapshotApiPort | None = None,
        trade_api: TradeCommandApi | None = None,
        event_publisher: "EventPublisher | None" = None,
    ) -> None:
        if (
            replay_api is not None
            and replay_api.service_generation != service_generation
        ):
            raise ValueError(
                "Replay API service_generation must match the desktop service"
            )
        if (
            live_snapshot_api is not None
            and getattr(live_snapshot_api, "service_generation", service_generation)
            != service_generation
        ):
            raise ValueError(
                "Live snapshot API service_generation must match the desktop service"
            )
        if (
            trade_api is not None
            and getattr(trade_api, "service_generation", service_generation)
            != service_generation
        ):
            raise ValueError(
                "Trade API service_generation must match the desktop service"
            )
        if (
            event_publisher is not None
            and getattr(event_publisher, "service_generation", service_generation)
            != service_generation
        ):
            raise ValueError(
                "Event publisher service_generation must match the desktop service"
            )
        super().__init__(server_address, _Handler)
        self.token = token
        self.service_generation = service_generation
        self.search_service = search_service
        self.replay_api = replay_api
        self.live_snapshot_api = live_snapshot_api
        self.trade_api = trade_api
        self.event_publisher = event_publisher
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
        if command in _LIVE_COMMANDS:
            self._live_command(command, request)
            return
        if command in _TRADE_COMMANDS:
            self._trade_command(command, request)
            return
        if command in REPLAY_COMMANDS:
            self._replay_command(command, request)
            return
        self._service_unavailable(command, request)

    def _live_command(self, command: str, request: dict[str, Any]) -> None:
        live_api = getattr(self.server, "live_snapshot_api", None)
        if live_api is None:
            self._service_unavailable(command, request)
            return
        error = _validate_live_snapshot_request(command, request)
        if error is not None:
            request_id = request.get("request_id", "missing-request-id")
            payload = {
                "schema_version": "t0_app_v1",
                "request_id": request_id,
                "accepted": False,
                "operation_id": None,
                "data": None,
                "error": error,
            }
            self._json(HTTPStatus.BAD_REQUEST, payload)
            return
        request_id = request["request_id"]
        session_id = request["session_id"]
        try:
            result = live_api.get_live_snapshot(
                request_id=request_id,
                session_id=session_id,
            )
        except Exception:
            traceback.print_exc()
            error = {
                "error_code": "service_unavailable",
                "category": "service",
                "severity": "error",
                "retryable": True,
                "affected_capability": "live",
                "message": "本地 Live 服务暂时不可用",
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
        status = HTTPStatus.OK if result.get("accepted") else HTTPStatus.NOT_FOUND
        result_error = result.get("error")
        if (
            isinstance(result_error, dict)
            and result_error.get("error_code") == "service_unavailable"
        ):
            status = HTTPStatus.SERVICE_UNAVAILABLE
        elif not result.get("accepted"):
            status = HTTPStatus.NOT_FOUND
        self._json(status, result)

    def _replay_command(self, command: str, request: dict[str, Any]) -> None:
        replay_api = getattr(self.server, "replay_api", None)
        if replay_api is None:
            self._service_unavailable(command, request)
            return
        result = replay_api.dispatch(command, request)
        self._json(HTTPStatus(result.status), result.payload)
        result.response_delivered()

    def _trade_command(self, command: str, request: dict[str, Any]) -> None:
        trade_api = getattr(self.server, "trade_api", None)
        if trade_api is None:
            self._service_unavailable(command, request)
            return
        error = _validate_trade_request(command, request)
        if error is not None:
            request_id = request.get("request_id", "missing-request-id")
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
        # TradeCommandApi persists synchronously and publishes the authoritative
        # trades_changed event itself (session_id: null) on success. The sync
        # response only signals acceptance; the renderer never reads its data.
        result = trade_api.dispatch(command, request)
        status = (
            HTTPStatus.OK if result.get("accepted") else _trade_error_status(result)
        )
        self._json(status, result)

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

        publisher = getattr(self.server, "event_publisher", None)
        # Claim the connect service_status revision from the publisher so the
        # service-scoped (session_id: null) revision sequence is single-sourced
        # and gap-free; fall back to 0 when no publisher is wired (contract-only
        # tests). The renderer's gateway seeds its gate from this revision.
        service_revision = publisher.claim() if publisher is not None else 0
        self._send_websocket_text(
            json.dumps(
                {
                    "schema_version": "t0_app_v1",
                    "service_generation": getattr(
                        self.server, "service_generation", 1
                    ),
                    "session_id": None,
                    "revision": service_revision,
                    "event_type": "service_status",
                    "payload": {
                        "state": "connected",
                        "message": "本地服务事件通道已连接",
                    },
                },
                ensure_ascii=False,
            )
        )
        # Subscribe before announcing readiness so no published event (e.g. an
        # authoritative trades_changed) is lost between connect and subscribe.
        subscriber = publisher.subscribe() if publisher is not None else None
        self.server.websocket_connected()
        try:
            while not self.server.shutdown_event.is_set():
                if subscriber is not None and not self._drain_websocket_events(
                    subscriber, publisher
                ):
                    # A write failure means the client is gone; let recv confirm.
                    return
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
            if subscriber is not None and publisher is not None:
                publisher.unsubscribe(subscriber)
            self.server.websocket_disconnected()

    def _drain_websocket_events(self, subscriber: Any, publisher: Any) -> bool:
        """Send all currently-queued event envelopes to this WebSocket.

        Returns ``False`` if a write failed (client likely gone) so the caller
        can exit the connection loop; ``True`` otherwise.
        """

        while True:
            try:
                envelope = subscriber.get_nowait()
            except _queue.Empty:
                return True
            try:
                self._send_websocket_text(publisher.encode(envelope))
            except OSError:
                return False

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


def _live_invalid_request(message: str, request_id: str) -> dict[str, Any]:
    """Build a structured ``invalid_request`` application_error for live commands."""

    return {
        "error_code": "invalid_request",
        "category": "validation",
        "severity": "error",
        "retryable": False,
        "affected_capability": "live",
        "message": message,
        "request_id": request_id,
        "details": {},
    }


def _trade_invalid_request(message: str, request_id: str) -> dict[str, Any]:
    """Build a structured ``invalid_trade_request`` error for trade commands."""

    return {
        "error_code": "invalid_trade_request",
        "category": "validation",
        "severity": "error",
        "retryable": False,
        "affected_capability": "trades",
        "message": message,
        "request_id": request_id,
        "details": {},
    }


def _trade_error_status(result: dict[str, Any]) -> HTTPStatus:
    """Map a rejected trade ``command_response`` to an HTTP status."""

    error_code = (result.get("error") or {}).get("error_code")
    if error_code == "trade_not_found":
        return HTTPStatus.NOT_FOUND
    if error_code == "trade_conflict":
        return HTTPStatus.CONFLICT
    if error_code == "invalid_trade_request":
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.SERVICE_UNAVAILABLE


def _validate_trade_request(
    url_command: str,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate a trade ``command_request`` envelope before dispatch.

    Mirrors :func:`_validate_live_snapshot_request`: enforce the frozen
    ``command_request`` contract via the formal JSON Schema validator
    (``additionalProperties: false``, required fields, and the per-command
    ``if/then`` payload rules), plus the URL/body command match. Returns a
    structured ``application_error`` dict when invalid, or ``None`` when
    accepted. An invalid request never reaches ``TradeCommandApi``.
    """

    errors = list(_COMMAND_REQUEST_VALIDATOR.iter_errors(request))
    if not errors:
        request_id = request["request_id"]
        if request.get("command") != url_command:
            return _trade_invalid_request(
                "body command must match the URL command", request_id
            )
        return None

    request_id = request.get("request_id")
    request_id_echo = (
        request_id
        if isinstance(request_id, str) and request_id
        else "missing-request-id"
    )
    first = errors[0]
    field = "/".join(str(part) for part in first.absolute_path) or "command_request"
    return _trade_invalid_request(f"{field}: {first.message}", request_id_echo)


def _build_command_request_validator() -> Draft202012Validator:
    """Load the frozen ``command_request`` schema for runtime validation.

    Using the formal schema (rather than hand-rolled field checks) keeps the
    backend aligned with the frozen contract, including
    ``additionalProperties: false`` and the per-command ``if/then`` payload
    constraints, so the validation never drifts if the contract evolves.
    """

    app_path = APP_CONTRACTS_DIR / "app-v1.schema.json"
    logical_path = APP_CONTRACTS_DIR / "logical-schema.json"
    with app_path.open(encoding="utf-8") as stream:
        app = json.load(stream)
    with logical_path.open(encoding="utf-8") as stream:
        logical = json.load(stream)
    registry = Registry().with_resources(
        [
            (app["$id"], Resource.from_contents(app)),
            (logical["$id"], Resource.from_contents(logical)),
        ]
    )
    return Draft202012Validator(
        {"$ref": f"{app['$id']}#/$defs/command_request"}, registry=registry
    )


_COMMAND_REQUEST_VALIDATOR = _build_command_request_validator()


def _validate_live_snapshot_request(
    url_command: str,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate a ``get_live_snapshot`` command envelope before dispatch.

    Enforces the frozen ``command_request`` contract via the formal JSON Schema
    validator (``additionalProperties: false``, required fields, and the
    ``get_live_snapshot`` if/then rule requiring a non-empty ``session_id`` and
    an empty ``payload``), plus the URL/body command match.  Returns a
    structured ``application_error`` dict when the request is invalid, or
    ``None`` when it is accepted.  An invalid request never reaches
    ``LiveSnapshotApi``.
    """

    errors = list(_COMMAND_REQUEST_VALIDATOR.iter_errors(request))
    if not errors:
        request_id = request["request_id"]
        if request.get("command") != url_command:
            return _live_invalid_request(
                "body command must match the URL command", request_id
            )
        return None

    # Schema validation failed: report the first cause with a stable echo of
    # request_id when one is present and valid.
    request_id = request.get("request_id")
    request_id_echo = (
        request_id
        if isinstance(request_id, str) and request_id
        else "missing-request-id"
    )
    first = errors[0]
    field = "/".join(str(part) for part in first.absolute_path) or "command_request"
    return _live_invalid_request(
        f"{field}: {first.message}", request_id_echo
    )


def create_server(
    host: str,
    port: int,
    token: str,
    service_generation: int,
    search_service: SecuritiesSearchService | None = None,
    replay_api: ReplayCommandApi | None = None,
    live_snapshot_api: LiveSnapshotApiPort | None = None,
    trade_api: TradeCommandApi | None = None,
    event_publisher: "EventPublisher | None" = None,
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
        live_snapshot_api=live_snapshot_api,
        trade_api=trade_api,
        event_publisher=event_publisher,
    )


def _build_trade_api(
    service_generation: int,
) -> tuple[TradeCommandApi | None, "EventPublisher | None"]:
    """Construct the real-trade command API + event publisher for ``main()``.

    Opens (or creates) the private App database under the runtime ``db`` dir,
    wires the SQLite trade repository and :class:`TradeService`, and binds a
    :class:`TradeCommandApi` to an :class:`EventPublisher` so accepted trade
    commands publish authoritative ``trades_changed`` events. Returns
    ``(None, None)`` only if the App database cannot be opened at all, in which
    case trade commands fall back to ``service_unavailable`` rather than
    crashing the service.
    """

    paths = RuntimePaths()
    paths.ensure_dirs()
    db_path = paths.db_dir / "t0_assistant.sqlite"
    try:
        database = open_app_database(db_path)
    except Exception:  # pragma: no cover - degraded startup path
        traceback.print_exc()
        return None, None
    repository = SqliteTradeRepository(database)
    service = TradeService(repository)
    publisher = EventPublisher(service_generation=service_generation)
    trade_api = TradeCommandApi(
        service,
        service_generation=service_generation,
        publisher=publisher,
    )
    return trade_api, publisher


def main() -> None:
    parser = argparse.ArgumentParser(description="StockPilot T+0 desktop service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--service-generation", default=1, type=int)
    args = parser.parse_args()
    trade_api, event_publisher = _build_trade_api(args.service_generation)
    server = create_server(
        args.host,
        args.port,
        os.environ.get("T0_SERVICE_TOKEN", ""),
        args.service_generation,
        trade_api=trade_api,
        event_publisher=event_publisher,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
