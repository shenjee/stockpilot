"""Integration tests: the formal Python service wired for real trade commands.

These tests guard the T0-043 guarantee that the Electron-managed Python
service *actually* dispatches ``list_trades`` / ``create_trade`` /
``update_trade`` / ``delete_trade`` against the real SQLite repository and
publishes authoritative ``trades_changed`` events over the ``/events``
WebSocket - not just the Fake Safe Bridge.

Contract obligations verified end-to-end:

* ``list_trades`` response is ``operation_id: null, data: null``; the
  authoritative list arrives as a ``trades_changed`` event with
  ``session_id: null``.
* An empty repository still publishes one ``trades_changed`` with
  ``payload.trades: []``.
* ``payload.trades`` is a complete repository snapshot (spans the request's
  symbol/date and others).
* Envelope ``revision`` is monotonic ``+1`` for ``session_id: null`` events
  (the renderer gateway drops gaps); ``payload.trade_revision`` is monotonic
  within the ``service_generation`` and starts at ``0``.
* A failed delete (missing trade) returns ``trade_not_found`` and publishes
  *no* event (a failed write never publishes an empty fact).
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(APP_ROOT))

from backend.service import create_server  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqliteTradeRepository,
    open_app_database,
)
from packages.t0assistant.trading import TradeCommandApi, TradeService  # noqa: E402
from backend.event_publisher import EventPublisher  # noqa: E402


def _draft(**overrides) -> dict:
    base = {
        "trade_scope": "real",
        "symbol": "sh.600584",
        "side": "buy",
        "executed_at": "2026-07-24 10:03:00",
        "price": 38.25,
        "quantity": 200,
        "fee": 5.01,
        "note": "manual fill",
        "fee_plan_id": "shenwan-hongyuan",
    }
    base.update(overrides)
    return base


def _envelope(command: str, payload: dict, rid: str) -> bytes:
    return json.dumps(
        {
            "schema_version": "t0_app_v1",
            "request_id": rid,
            "command": command,
            "session_id": None,
            "payload": payload,
        }
    ).encode()


class _WebSocketClient:
    """Minimal raw-socket WebSocket client for the /events text stream.

    The handshake and subsequent frames may arrive in the same TCP segment, so
    bytes after the ``\\r\\n\\r\\n`` header terminator are buffered and fed to
    frame reads instead of being swallowed with the handshake.
    """

    def __init__(self, host: str, port: int, token: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=2)
        request = (
            "GET /events HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            f"Sec-WebSocket-Protocol: stockpilot-auth.{token}\r\n"
            "\r\n"
        ).encode()
        self.sock.sendall(request)
        self._buf = b""
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("websocket handshake closed prematurely")
            self._buf += chunk
        header_end = self._buf.index(b"\r\n\r\n")
        headers = self._buf[:header_end]
        if b"101 Switching Protocols" not in headers:
            raise AssertionError(f"websocket handshake failed: {headers[:80]!r}")
        self._buf = self._buf[header_end + 4 :]

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("connection closed before frame completed")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def recv_text(self, timeout: float = 2.0) -> dict:
        self.sock.settimeout(timeout)
        first = self._read(1)
        second = self._read(1)
        length = second[0] & 0x7F
        if length == 126:
            length = int.from_bytes(self._read(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read(8), "big")
        data = self._read(length)
        return json.loads(data.decode("utf-8"))

    def assert_no_event(self, timeout: float = 0.4) -> None:
        self.sock.settimeout(timeout)
        if self._buf:
            raise AssertionError("expected no event but one is buffered")
        try:
            chunk = self.sock.recv(1)
        except (TimeoutError, OSError):
            return
        if chunk:
            raise AssertionError("expected no event but received one")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class TradeServiceWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0_assistant.sqlite"
        self._database = open_app_database(self.db_path)
        repository = SqliteTradeRepository(self._database)
        service = TradeService(repository)
        self.publisher = EventPublisher(service_generation=7)
        self.trade_api = TradeCommandApi(
            service, service_generation=7, publisher=self.publisher
        )
        self.server = create_server(
            "127.0.0.1",
            0,
            "wiring-token",
            7,
            trade_api=self.trade_api,
            event_publisher=self.publisher,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        # Give the server a moment to accept connections.
        time.sleep(0.2)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._database.close()
        self._tempdir.cleanup()

    def _post(self, command: str, payload: dict, rid: str) -> tuple[int, dict]:
        request = Request(
            f"{self.base_url}/api/commands/{command}",
            data=_envelope(command, payload, rid),
            headers={
                "Authorization": "Bearer wiring-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            payload = json.load(error)
            error.close()
            return error.code, payload

    def test_list_create_update_delete_publish_authoritative_trades_changed(self) -> None:
        client = _WebSocketClient("127.0.0.1", self.server.server_port, "wiring-token")
        try:
            service_status = client.recv_text()
            self.assertEqual(service_status["event_type"], "service_status")
            self.assertEqual(service_status["revision"], 0)
            self.assertIsNone(service_status["session_id"])

            # list_trades on an empty repository publishes trades: [].
            status, response = self._post(
                "list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"},
                "r-list-empty",
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["accepted"])
            self.assertIsNone(response["operation_id"])
            self.assertIsNone(response["data"])  # renderer must not read sync data
            event = client.recv_text()
            self.assertEqual(event["event_type"], "trades_changed")
            self.assertIsNone(event["session_id"])
            self.assertEqual(event["revision"], 1)  # strict +1 over service_status
            self.assertEqual(event["payload"]["trade_revision"], 0)
            self.assertEqual(event["payload"]["trades"], [])

            # create_trade bumps the revision and publishes a 1-trade snapshot.
            status, response = self._post(
                "create_trade", {"trade": _draft()}, "r-create"
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["accepted"])
            event = client.recv_text()
            self.assertEqual(event["revision"], 2)
            self.assertEqual(event["payload"]["trade_revision"], 1)
            self.assertEqual(len(event["payload"]["trades"]), 1)
            trade_id = event["payload"]["trades"][0]["trade_id"]
            self.assertEqual(
                event["payload"]["trades"][0]["bucket_start"], "2026-07-24 10:00:00"
            )

            # Add a second trade for a different symbol/date to prove the
            # snapshot is complete, not scoped to the request.
            self._post(
                "create_trade",
                {"trade": _draft(symbol="sz.000001", executed_at="2026-07-25 14:10:00",
                                 side="sell", price=12.40, quantity=500)},
                "r-create-2",
            )
            client.recv_text()  # consume the create event

            # list_trades for one symbol/date still publishes EVERY trade.
            self._post(
                "list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"},
                "r-list-2",
            )
            event = client.recv_text()
            symbols = {t["symbol"] for t in event["payload"]["trades"]}
            self.assertEqual(symbols, {"sh.600584", "sz.000001"})

            # update_trade preserves trade_id and the user-confirmed fee.
            status, response = self._post(
                "update_trade",
                {"trade_id": trade_id,
                 "trade": _draft(price=40.0, fee=9.99, note="edited")},
                "r-update",
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["accepted"])
            event = client.recv_text()
            updated = next(
                t for t in event["payload"]["trades"] if t["trade_id"] == trade_id
            )
            self.assertEqual(updated["price"], 40.0)
            self.assertEqual(updated["fee"], 9.99)  # not recomputed
            self.assertEqual(updated["note"], "edited")

            # delete_trade is a hard delete and publishes the reduced snapshot.
            status, response = self._post(
                "delete_trade",
                {"trade_id": trade_id, "trade_scope": "real"},
                "r-delete",
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["accepted"])
            event = client.recv_text()
            self.assertNotIn(
                trade_id, {t["trade_id"] for t in event["payload"]["trades"]}
            )
        finally:
            client.close()

    def test_immediate_list_trades_after_connect_does_not_drop_trades_changed(self) -> None:
        """Regression for the connect-race window.

        The renderer may send list_trades as soon as the WebSocket handshake
        completes, before (or concurrently with) reading service_status. The
        service must already be subscribed before sending service_status, so the
        authoritative trades_changed is enqueued to this connection and is not
        lost. A lost event would leave the client with revision 0 and no trades,
        and the next service-scoped event would look like a gap.
        """
        client = _WebSocketClient("127.0.0.1", self.server.server_port, "wiring-token")
        try:
            # Fire list_trades BEFORE reading service_status, while the server
            # has just finished the handshake. This deterministically exercises
            # the old race window where service_status was sent before subscribe.
            status, response = self._post(
                "list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"},
                "r-list-race",
            )
            self.assertEqual(status, 200)
            self.assertTrue(response["accepted"])

            # Now drain events. service_status (revision 0) and trades_changed
            # (revision 1) must both arrive, in order, on this connection.
            first = client.recv_text()
            self.assertEqual(first["event_type"], "service_status")
            self.assertEqual(first["revision"], 0)
            second = client.recv_text()
            self.assertEqual(second["event_type"], "trades_changed")
            self.assertEqual(second["revision"], 1)
            self.assertEqual(second["payload"]["trade_revision"], 0)
            self.assertEqual(second["payload"]["trades"], [])
        finally:
            client.close()

    def test_delete_missing_trade_returns_not_found_and_publishes_nothing(self) -> None:
        client = _WebSocketClient("127.0.0.1", self.server.server_port, "wiring-token")
        try:
            client.recv_text()  # consume service_status

            status, response = self._post(
                "delete_trade",
                {"trade_id": "does-not-exist", "trade_scope": "real"},
                "r-delete-missing",
            )
            self.assertEqual(status, 404)
            self.assertFalse(response["accepted"])
            self.assertEqual(response["error"]["error_code"], "trade_not_found")
            # A failed write publishes no event (no empty fact over last state).
            client.assert_no_event()
        finally:
            client.close()

    def test_trade_command_unavailable_without_trade_api(self) -> None:
        # A server with no trade_api wired returns service_unavailable, not a
        # crash - the formal service degrades gracefully when the trade stack
        # cannot be built.
        server = create_server("127.0.0.1", 0, "no-trade-token", 3)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            time.sleep(0.2)
            request = Request(
                f"{base_url}/api/commands/list_trades",
                data=_envelope(
                    "list_trades",
                    {"trade_scope": "real", "symbol": "sh.600584",
                     "trade_date": "2026-07-24"},
                    "r-no-api",
                ),
                headers={
                    "Authorization": "Bearer no-trade-token",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=2)
            self.assertEqual(rejected.exception.code, 503)
            payload = json.load(rejected.exception)
            rejected.exception.close()
            self.assertFalse(payload["accepted"])
            self.assertEqual(payload["error"]["error_code"], "service_unavailable")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
