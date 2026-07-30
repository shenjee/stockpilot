from __future__ import annotations

import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.historical_snapshot_api import HistoricalSnapshotApi  # noqa: E402
from backend.service import create_server  # noqa: E402
from backend.service import LiveSnapshotApi  # noqa: E402
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)
from packages.t0assistant.replay import ReplayAccepted, ReplayCommandApi  # noqa: E402
from packages.t0assistant.runtime import SessionType  # noqa: E402
from packages.t0assistant.runtime.live_projection_store import (  # noqa: E402
    LiveProjectionStore,
)


class _FakeSearchService:
    def search(self, query: str, limit: int):
        return [
            {
                "symbol": "sh.600519",
                "code": "600519",
                "market": "sh",
                "name": "贵州茅台",
                "security_type": "a_share",
            }
        ][:limit]


class _FakeReplayPort:
    def __init__(self) -> None:
        self.requests = []
        self.started = []

    def execute(self, command, request):
        self.requests.append((command, dict(request)))
        if command == "step_replay":
            return ReplayAccepted(
                session_id=request["session_id"],
                operation_id="operation-step",
                start_operation=lambda: self.started.append("operation-step"),
            )
        return ReplayAccepted(session_id=request.get("session_id", "replay-new"))


class DesktopServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            search_service=_FakeSearchService(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_requires_authentication_and_reports_generation(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            urlopen(f"{self.base_url}/health", timeout=1)
        self.assertEqual(rejected.exception.code, 401)
        rejected.exception.close()

        request = Request(
            f"{self.base_url}/health",
            headers={"Authorization": "Bearer formal-token"},
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
        self.assertEqual(payload, {"status": "ready", "service_generation": 5})

    def test_unimplemented_app_command_returns_structured_service_error(self) -> None:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": "formal-command-1",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/get_live_snapshot",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=1)
        self.assertEqual(rejected.exception.code, 503)
        payload = json.load(rejected.exception)
        rejected.exception.close()
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "service_unavailable")
        self.assertEqual(payload["error"]["request_id"], "formal-command-1")

    def test_security_search_returns_multiple_result_contract(self) -> None:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": "search-1",
                "command": "search_securities",
                "session_id": None,
                "payload": {"query": "gzmt", "limit": 20},
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/search_securities",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            payload = json.load(response)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["data"]["securities"][0]["symbol"], "sh.600519")

    def test_non_object_command_body_is_rejected_without_handler_failure(self) -> None:
        request = Request(
            f"{self.base_url}/api/commands/get_preferences",
            data=b"[]",
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=1)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()

    def test_replay_command_is_dispatched_through_v1_api(self) -> None:
        port = _FakeReplayPort()
        replay_api = ReplayCommandApi(port, service_generation=5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            search_service=_FakeSearchService(),
            replay_api=replay_api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        body = json.dumps(
            {
                "schema_version": "t0_replay_v1",
                "request_id": "pause-1",
                "session_id": "replay-1",
                "playing": False,
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/set_replay_playback",
            data=body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=1) as response:
            payload = json.load(response)

        self.assertEqual(payload["request_id"], "pause-1")
        self.assertEqual(payload["session_id"], "replay-1")
        self.assertEqual(port.requests[0][0], "set_replay_playback")

        step_body = json.dumps(
            {
                "schema_version": "t0_replay_v1",
                "request_id": "step-1",
                "session_id": "replay-1",
            }
        ).encode()
        step_request = Request(
            f"{self.base_url}/api/commands/step_replay",
            data=step_body,
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(step_request, timeout=1) as response:
            step_payload = json.load(response)

        self.assertEqual(step_payload["operation_id"], "operation-step")
        self.assertEqual(port.started, ["operation-step"])

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_server("0.0.0.0", 0, "token", 1)

    def test_replay_api_generation_must_match_server_generation(self) -> None:
        replay_api = ReplayCommandApi(_FakeReplayPort(), service_generation=6)

        with self.assertRaisesRegex(ValueError, "service_generation"):
            create_server(
                "127.0.0.1",
                0,
                "token",
                5,
                replay_api=replay_api,
            )

    def test_websocket_handler_exits_when_the_client_disconnects(self) -> None:
        client = socket.create_connection(("127.0.0.1", self.server.server_port))
        request = (
            "GET /events HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.server.server_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Protocol: stockpilot-auth.formal-token\r\n"
            "\r\n"
        ).encode()
        client.sendall(request)
        response = client.recv(4096)
        self.assertIn(b"101 Switching Protocols", response)
        deadline = time.monotonic() + 1
        while self.server.active_websockets != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_websockets, 1)

        client.close()
        deadline = time.monotonic() + 1
        while self.server.active_websockets and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_websockets, 0)


class _FakeCoordinator:
    """Minimal coordinator implementing commit_if_accepted for store tests."""

    def __init__(self) -> None:
        self._accepted: tuple[str, int] | None = None

    def set_accepted(self, session_id: str, generation: int) -> None:
        self._accepted = (session_id, generation)

    def clear(self) -> None:
        self._accepted = None

    def commit_if_accepted(
        self,
        *,
        session_type,
        session_id: str,
        generation: int,
        commit,
    ) -> bool:
        if self._accepted is None:
            return False
        resolved = (
            session_type if isinstance(session_type, SessionType) else SessionType(session_type)
        )
        if resolved is not SessionType.LIVE:
            return False
        if self._accepted != (session_id, generation):
            return False
        commit()
        return True


class _LiveSnapshotMixin:
    """Builds a LiveProjectionStore with a schema-valid baseline snapshot."""

    @staticmethod
    def _build_store(coordinator: _FakeCoordinator) -> LiveProjectionStore:
        from datetime import date, datetime
        from packages.t0assistant.runtime import PipelineMarketInput
        from packages.t0assistant.runtime.live_session import (
            LiveSnapshotCandidate,
            PreparedLiveWarmup,
        )
        from packages.marketdata.services.market_context_service import (
            MarketContextService,
        )
        from packages.t0assistant.runtime.pipeline import WorkbenchPipeline

        def _bar(ts, o, h, l, c, v, a):
            return {
                "timestamp": ts, "open": o, "high": h, "low": l,
                "close": c, "volume": v, "amount": a, "closed": True,
            }

        def _chan(symbol):
            return {
                "symbol": symbol, "timeframe": "5m", "source": "fixture",
                "engine": "czsc", "engine_version": "0.10.12", "parameters": {},
                "fractals": [], "strokes": [], "segments": [], "pivot_zones": [],
                "divergences": [], "structure_alerts": [], "signal_series": [],
                "signal_events": [], "signal_snapshots": [],
                "candidate_point_events": [], "candidate_buy_points": [],
                "candidate_sell_points": [], "plot_primitives": [],
                "summary": [], "warnings": [], "meta": {},
            }

        calendar = MarketContextService(["2026-07-24", "2026-07-23"])
        market_session = calendar.require_session("2026-07-24", "sh")
        target_time = datetime(2026, 7, 24, 9, 31, 0)
        market_input = PipelineMarketInput(
            symbol="sh.600000",
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 1000, 10020),
                _bar("2026-07-23 15:00:00", 10.02, 10.08, 10.0, 10.05, 1200, 12060),
            ],
            bars_1m=[_bar("2026-07-24 09:31:00", 10.05, 10.08, 10.0, 10.06, 800, 8048)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )

        class _SinglePort:
            def read(self, target_time):
                return market_input

        prepared = PreparedLiveWarmup(
            market_session=market_session,
            target_time=target_time,
            market_input_port=_SinglePort(),
        )
        pipeline = WorkbenchPipeline(
            session=market_session,
            market_input_port=prepared.market_input_port,
            analyzer=lambda bars, sym: _chan(sym),
        )
        result = pipeline.preview(prepared.target_time)
        store = LiveProjectionStore(coordinator, service_generation=5)
        coordinator.set_accepted("live-1", 1)
        store.accept_candidate(
            LiveSnapshotCandidate(
                session_id="live-1",
                generation=1,
                symbol="sh.600000",
                pipeline_result=result,
            )
        )
        return store


class LiveSnapshotServiceTest(unittest.TestCase, _LiveSnapshotMixin):
    def setUp(self) -> None:
        self.coordinator = _FakeCoordinator()
        self.store = self._build_store(self.coordinator)
        self.live_api = LiveSnapshotApi(self.store, service_generation=5)
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            live_snapshot_api=self.live_api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _post(self, command: str, body: dict) -> tuple[int, dict]:
        request = Request(
            f"{self.base_url}/api/commands/{command}",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = None
        try:
            response = urlopen(request, timeout=1)
            return response.status, json.load(response)
        except HTTPError as exc:
            response = exc
            return exc.code, json.load(exc)
        finally:
            if response is not None:
                response.close()

    def test_get_live_snapshot_returns_authoritative_snapshot(self) -> None:
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-1",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        self.assertIsNone(payload["operation_id"])
        self.assertIsNotNone(payload["data"])
        self.assertEqual(payload["data"]["session"]["session_id"], "live-1")
        self.assertEqual(payload["data"]["session"]["revision"], 0)

    def test_get_live_snapshot_rejects_envelope_with_wrong_schema_version(self) -> None:
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "wrong",
                "request_id": "snap-env-1",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        self.assertEqual(payload["error"]["request_id"], "snap-env-1")

    def test_get_live_snapshot_rejects_envelope_with_mismatched_body_command(self) -> None:
        # Body command differs from the URL command: must be rejected before the
        # snapshot command runs, with HTTP 400 and structured invalid_request.
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-env-2",
                "command": "retry_live",
                "session_id": "live-1",
                "payload": {},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")

    def test_get_live_snapshot_rejects_envelope_missing_request_id(self) -> None:
        # Reviewer's exact invalid request: wrong schema_version, mismatched
        # command, non-empty payload, and a missing request_id.  Must return
        # HTTP 400 and never execute the snapshot command.
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "wrong",
                "command": "retry_live",
                "session_id": "live-1",
                "payload": {"unexpected": True},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        self.assertEqual(payload["error"]["affected_capability"], "live")

    def test_get_live_snapshot_rejects_envelope_with_non_empty_payload(self) -> None:
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-env-3",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {"unexpected": True},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        # No snapshot command was executed: request_id is echoed back but no
        # authoritative state was read.
        self.assertEqual(payload["error"]["request_id"], "snap-env-3")

    def test_get_live_snapshot_rejects_envelope_with_extra_top_level_field(self) -> None:
        # The frozen command_request schema forbids additional top-level fields.
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-env-4",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
                "unexpected": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        self.assertEqual(payload["error"]["request_id"], "snap-env-4")

    def test_get_live_snapshot_wrong_session_is_rejected(self) -> None:
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-2",
                "command": "get_live_snapshot",
                "session_id": "live-other",
                "payload": {},
            },
        )
        self.assertEqual(status, 404)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "session_not_found")

    def test_get_live_snapshot_retired_session_is_rejected(self) -> None:
        self.coordinator.clear()
        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-3",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            },
        )
        self.assertEqual(status, 404)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "session_not_found")

    def test_get_live_snapshot_no_store_returns_service_unavailable(self) -> None:
        """Without a live_snapshot_api injected, the command is unavailable."""

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        status, payload = self._post(
            "get_live_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "snap-4",
                "command": "get_live_snapshot",
                "session_id": "live-1",
                "payload": {},
            },
        )
        self.assertEqual(status, 503)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "service_unavailable")

    def test_live_api_generation_must_match_server_generation(self) -> None:
        bad_api = LiveSnapshotApi(self.store, service_generation=6)
        with self.assertRaisesRegex(ValueError, "service_generation"):
            create_server(
                "127.0.0.1",
                0,
                "token",
                5,
                live_snapshot_api=bad_api,
            )


class _FakeHistoricalSnapshotApi:
    """In-memory historical snapshot API for service dispatch tests."""

    def __init__(self, service_generation: int) -> None:
        self.service_generation = service_generation

    def get_historical_snapshot(
        self,
        *,
        request_id: str,
        symbol: str,
        trade_date: str,
    ) -> dict:
        return {
            "schema_version": "t0_app_v1",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": {
                "timezone": "Asia/Shanghai",
                "session": {
                    "session_id": f"historical:{symbol}:{trade_date}",
                    "session_type": "historical",
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "state": "ready",
                    "revision": 0,
                },
                "market": {
                    "bars_1m": [],
                    "bars_5m": [],
                    "daily_bars": [],
                    "quote": None,
                },
                "indicators": {
                    "five_minute": {
                        "ma": {
                            "ma5": [],
                            "ma10": [],
                            "ma20": [],
                            "ma30": [],
                            "ma60": [],
                        },
                        "volume": {"values": [], "ma5": [], "ma10": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                    "one_minute": {
                        "vwap": [],
                        "volume": {"values": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                },
                "chan_analysis": {
                    "strokes": [],
                    "pivot_zones": [],
                },
                "warnings": [],
            },
            "error": None,
        }


class HistoricalSnapshotServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = _FakeHistoricalSnapshotApi(service_generation=5)
        self.server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            historical_snapshot_api=self.api,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _post(self, command: str, body: dict) -> tuple[int, dict]:
        return self._post_with_base_url(self.base_url, command, body)

    @staticmethod
    def _post_with_base_url(base_url: str, command: str, body: dict) -> tuple[int, dict]:
        request = Request(
            f"{base_url}/api/commands/{command}",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer formal-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = None
        try:
            response = urlopen(request, timeout=1)
            return response.status, json.load(response)
        except HTTPError as exc:
            response = exc
            return exc.code, json.load(exc)
        finally:
            if response is not None:
                response.close()

    def test_get_historical_snapshot_returns_static_snapshot(self) -> None:
        status, payload = self._post(
            "get_historical_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "hist-1",
                "command": "get_historical_snapshot",
                "session_id": None,
                "payload": {"symbol": "sh.600000", "trade_date": "2026-07-22"},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        self.assertIsNone(payload["operation_id"])
        self.assertIsNotNone(payload["data"])
        self.assertEqual(payload["data"]["session"]["session_type"], "historical")
        self.assertEqual(payload["data"]["session"]["trade_date"], "2026-07-22")
        self.assertEqual(payload["data"]["session"]["revision"], 0)

    def test_get_historical_snapshot_rejects_invalid_payload(self) -> None:
        status, payload = self._post(
            "get_historical_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "hist-bad",
                "command": "get_historical_snapshot",
                "session_id": None,
                "payload": {"symbol": "invalid", "trade_date": "not-a-date"},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        self.assertEqual(payload["error"]["affected_capability"], "historical_chart")

    def test_get_historical_snapshot_rejects_non_calendar_date(self) -> None:
        """A format-valid but calendar-invalid date returns 400 via the real API."""
        api = HistoricalSnapshotApi(
            service_generation=5,
            store=MagicMock(),
            provider=MagicMock(),
            market_context=MarketContextService(
                trading_days=["2026-02-20"],
                coverage_start="2026-02-20",
                coverage_end="2026-02-20",
            ),
        )
        server = create_server(
            "127.0.0.1",
            0,
            "formal-token",
            5,
            historical_snapshot_api=api,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            status, payload = self._post_with_base_url(
                base_url,
                "get_historical_snapshot",
                {
                    "schema_version": "t0_app_v1",
                    "request_id": "hist-non-calendar",
                    "command": "get_historical_snapshot",
                    "session_id": None,
                    "payload": {"symbol": "sh.600000", "trade_date": "2026-02-30"},
                },
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")
        self.assertEqual(payload["error"]["category"], "validation")
        self.assertFalse(payload["error"]["retryable"])

    def test_get_historical_snapshot_requires_session_id_null(self) -> None:
        status, payload = self._post(
            "get_historical_snapshot",
            {
                "schema_version": "t0_app_v1",
                "request_id": "hist-session",
                "command": "get_historical_snapshot",
                "session_id": "live-1",
                "payload": {"symbol": "sh.600000", "trade_date": "2026-07-22"},
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["accepted"])
        self.assertEqual(payload["error"]["error_code"], "invalid_request")

    def test_get_historical_snapshot_no_api_returns_service_unavailable(self) -> None:
        server = create_server("127.0.0.1", 0, "formal-token", 5)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(
                f"{base_url}/api/commands/get_historical_snapshot",
                data=json.dumps(
                    {
                        "schema_version": "t0_app_v1",
                        "request_id": "hist-noapi",
                        "command": "get_historical_snapshot",
                        "session_id": None,
                        "payload": {"symbol": "sh.600000", "trade_date": "2026-07-22"},
                    }
                ).encode(),
                headers={
                    "Authorization": "Bearer formal-token",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=1)
            self.assertEqual(rejected.exception.code, 503)
            payload = json.load(rejected.exception)
            rejected.exception.close()
            self.assertEqual(payload["error"]["error_code"], "service_unavailable")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_historical_api_generation_must_match_server_generation(self) -> None:
        bad_api = _FakeHistoricalSnapshotApi(service_generation=6)
        with self.assertRaisesRegex(ValueError, "service_generation"):
            create_server(
                "127.0.0.1",
                0,
                "token",
                5,
                historical_snapshot_api=bad_api,
            )


if __name__ == "__main__":
    unittest.main()
