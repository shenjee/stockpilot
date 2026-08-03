"""T0-051 deterministic Live acceptance through the HTTP delivery boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, time, timedelta
from urllib.request import Request, urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.event_publisher import EventPublisher  # noqa: E402
from backend.live_application import LiveApplicationApi, LiveSessionFactory  # noqa: E402
from backend.service import create_server  # noqa: E402
from packages.marketdata.calendar_query import FixtureCalendarQuery  # noqa: E402
from packages.marketdata.services.market_context_service import MarketSession  # noqa: E402
from packages.t0assistant.preferences import PreferenceService  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqlitePreferenceRepository,
    open_app_database,
)
from packages.t0assistant.runtime import PipelineMarketInput  # noqa: E402
from packages.t0assistant.runtime.live_market_view import resolve_live_market_context  # noqa: E402
from packages.t0assistant.runtime.live_session import PreparedLiveWarmup  # noqa: E402
from test_live_application import _DeterministicLiveInput, _MarketInput, _bar, _chan  # noqa: E402


def _closed_bars(session: MarketSession, *, minutes: int, price: float = 10.0) -> list[dict]:
    return [
        _bar(moment.strftime("%Y-%m-%d %H:%M:%S"), price)
        for moment in session.bar_close_times(minutes)
    ]


def _preheat_bars(count: int, *, start: datetime, price: float = 10.0) -> list[dict]:
    bars = []
    moment = start
    for index in range(count):
        bars.append(_bar(moment.strftime("%Y-%m-%d %H:%M:%S"), price + index * 0.001))
        moment -= timedelta(minutes=5)
    return list(reversed(bars))


class _SaturdayResolverLiveInput(_DeterministicLiveInput):
    """Resolve Friday from a Saturday wall clock through the calendar resolver."""

    observed_now = datetime(2026, 7, 25, 10, 0)

    def __init__(self) -> None:
        super().__init__()
        self.calendar = FixtureCalendarQuery(
            ["2026-07-24"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-25",
        )

    def prepare(self, spec, *, minimum_preheat_5m):
        self.requests.append((spec, minimum_preheat_5m))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=self.observed_now,
            market="sh",
        )
        session = resolved.market_session
        target = datetime.combine(session.trade_date, time(15, 0))
        bars_1m = _closed_bars(session, minutes=1, price=10.1)
        bars_5m = _closed_bars(session, minutes=5, price=10.1)
        market_input = PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=session.trade_date,
            previous_close=10.0,
            preheat_5m_bars=_preheat_bars(
                500,
                start=datetime.combine(date(2026, 7, 23), time(15, 0)),
            ),
            bars_1m=bars_1m,
            official_5m_bars=bars_5m,
            daily_bars_history=[
                {
                    "timestamp": "2026-07-23",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                    "closed": True,
                }
            ],
            quote_snapshots=[
                {
                    "timestamp": "2026-07-24 15:00:03",
                    "latest_price": 10.1,
                    "change_percent": 0.0,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "previous_close": 10.0,
                    "volume": 100.0,
                    "amount": 1010.0,
                    "volume_ratio": None,
                    "order_imbalance": None,
                    "turnover_rate": None,
                }
            ],
        )
        return PreparedLiveWarmup(
            market_session=session,
            target_time=target,
            observed_now=self.observed_now,
            market_input_port=_MarketInput(target, market_input),
            calendar_status=resolved.calendar_status,
            market_phase=resolved.market_phase,
        )


class _DaySwitchLiveInput(_DeterministicLiveInput):
    """Pin Friday first, then expose Monday evidence through refresh."""

    def __init__(self) -> None:
        super().__init__()
        self.context = self.context.__class__(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )
        self.calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )
        self.prepare_calls = 0

    def prepare(self, spec, *, minimum_preheat_5m):
        self.prepare_calls += 1
        self.requests.append((spec, minimum_preheat_5m))
        if self.prepare_calls == 1:
            target = datetime(2026, 7, 24, 15, 0)
            market_input = PipelineMarketInput(
                symbol=spec.symbol,
                trade_date=date(2026, 7, 24),
                previous_close=10.0,
                preheat_5m_bars=[_bar("2026-07-23 15:00:00", 10.0)],
                bars_1m=[_bar("2026-07-24 15:00:00", 10.1)],
                official_5m_bars=[_bar("2026-07-24 15:00:00", 10.1)],
                daily_bars_history=[],
                quote_snapshots=[],
            )
            return PreparedLiveWarmup(
                market_session=self.context.require_session("2026-07-24", "sh"),
                target_time=target,
                observed_now=target,
                market_input_port=_MarketInput(target, market_input),
                calendar_status="available",
                market_phase="closed",
            )
        target = datetime(2026, 7, 27, 9, 31)
        market_input = PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 27),
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-24 15:00:00", 10.1)],
            bars_1m=[_bar("2026-07-27 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )
        return PreparedLiveWarmup(
            market_session=self.context.require_session("2026-07-27", "sh"),
            target_time=target,
            observed_now=target,
            market_input_port=_MarketInput(target, market_input),
            calendar_status="available",
            market_phase="morning",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date):
        self.refresh_requests.append((timeframe, str(trade_date)))
        if str(trade_date) == "2026-07-27" and timeframe == "1m":
            return [_bar("2026-07-27 09:31:00", 10.2)]
        return [_bar("2026-07-24 15:00:00", 10.1)]

    def load_refresh_quotes(self, spec, *, trade_date):
        self.refresh_requests.append(("quote", str(trade_date)))
        if str(trade_date) == "2026-07-27":
            return [
                {
                    "timestamp": "2026-07-27 09:31:03",
                    "latest_price": 10.2,
                    "change_percent": 0.0,
                    "open": 10.2,
                    "high": 10.2,
                    "low": 10.2,
                    "previous_close": 10.0,
                    "volume": 100.0,
                    "amount": 1020.0,
                    "volume_ratio": None,
                    "order_imbalance": None,
                    "turnover_rate": None,
                }
            ]
        return []


class _LiveHttpHarness:
    def __init__(self, input_port, *, calendar=None) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = open_app_database(Path(self.tempdir.name) / "app.sqlite")
        self.publisher = EventPublisher(service_generation=7)
        self.events = self.publisher.subscribe()
        self.factory = LiveSessionFactory(
            input_port,
            analyzer=lambda bars, symbol: _chan(symbol),
            auto_poll=False,
            calendar=calendar,
        )
        self.app = LiveApplicationApi(
            service_generation=7,
            session_factory=self.factory,
            preference_service=PreferenceService(
                SqlitePreferenceRepository(self.database)
            ),
            event_publisher=self.publisher,
            restore_on_startup=False,
        )
        self.server = create_server(
            "127.0.0.1",
            0,
            "live-token",
            7,
            live_application_api=self.app,
            event_publisher=self.publisher,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.publisher.unsubscribe(self.events)
        self.database.close()
        self.tempdir.cleanup()

    def command(self, name: str, session_id, payload: dict) -> dict:
        body = json.dumps(
            {
                "schema_version": "t0_app_v1",
                "request_id": f"request-{name}",
                "command": name,
                "session_id": session_id,
                "payload": payload,
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/commands/{name}",
            data=body,
            headers={
                "Authorization": "Bearer live-token",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=1) as response:
            return json.load(response)


class LiveEndToEndAcceptanceTests(unittest.TestCase):
    def test_select_snapshot_failure_retention_and_clean_retry(self) -> None:
        input_port = _DeterministicLiveInput(
            [object(), RuntimeError("injected provider failure"), object()]
        )
        harness = _LiveHttpHarness(input_port)
        try:
            selected = harness.command(
                "select_security",
                None,
                {"symbol": "sh.600000"},
            )
            baseline = harness.events.get(timeout=1)
            fetched = harness.command(
                "get_live_snapshot",
                selected["data"]["session_id"],
                {},
            )
            failed = harness.command(
                "retry_live",
                selected["data"]["session_id"],
                {},
            )
            failure = harness.events.get(timeout=1)

            self.assertEqual(fetched["data"], baseline["payload"])
            self.assertEqual(failure["event_type"], "operation_failed")
            self.assertTrue(harness.app.store.has_snapshot)
            self.assertEqual(harness.app.store.current_revision, baseline["revision"])

            recovered = harness.command(
                "retry_live",
                failed["data"]["session_id"],
                {},
            )
            replacement = harness.events.get(timeout=1)
            self.assertEqual(replacement["event_type"], "workbench_snapshot")
            self.assertEqual(
                recovered["data"]["session_id"],
                replacement["session_id"],
            )
            self.assertNotEqual(replacement["session_id"], baseline["session_id"])

            input_port.queue_refresh(
                "1m",
                [
                    _bar("2026-07-24 09:31:00", 10.2),
                    _bar("2026-07-24 09:32:00", 10.3),
                ],
            )
            runtime = harness.factory.latest_session
            assert runtime is not None
            runtime.wait_for_completion(1)
            runtime.refresh_scheduler.retry(
                "one_minute",
                datetime(2026, 7, 24, 9, 32),
            )
            updates = []
            while len(updates) < 4:
                updates.append(harness.events.get(timeout=2))
            refreshed = harness.command(
                "get_live_snapshot",
                recovered["data"]["session_id"],
                {},
            )

            self.assertEqual(updates[0]["event_type"], "market_update")
            self.assertEqual(updates[0]["payload"]["target"], "bars_1m")
            self.assertEqual(updates[-1]["event_type"], "live_market_view_updated")
            self.assertEqual(
                refreshed["data"]["market"]["bars_1m"][-1]["timestamp"],
                "2026-07-24 09:32:00",
            )
            self.assertEqual(
                refreshed["data"]["live_market_view"]["bars_1m_as_of"],
                "2026-07-24 09:32:00",
            )
            self.assertEqual(
                refreshed["data"]["session"]["revision"],
                updates[-1]["revision"],
            )
        finally:
            harness.close()

    def test_closed_market_day_publishes_previous_trade_date_view(self) -> None:
        calendar = FixtureCalendarQuery(
            ["2026-07-24"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-25",
        )
        harness = _LiveHttpHarness(_SaturdayResolverLiveInput(), calendar=calendar)
        try:
            selected = harness.command(
                "select_security",
                None,
                {"symbol": "sh.600000"},
            )
            snapshot = harness.events.get(timeout=1)
            payload = snapshot["payload"]
            view = payload["live_market_view"]

            self.assertEqual(snapshot["event_type"], "workbench_snapshot")
            self.assertEqual(payload["session"]["trade_date"], "2026-07-24")
            self.assertEqual(view["effective_trade_date"], "2026-07-24")
            self.assertEqual(view["market_phase"], "market_closed")
            self.assertEqual(view["calendar_status"], "available")
            self.assertEqual(view["market_closed_reason"], "weekend")
            self.assertIn(view["data_quality"], {"full", "degraded"})
            self.assertEqual(view["polling_profile"], "idle")
            self.assertEqual(view["symbol_availability"], "available")
            self.assertTrue(
                all(
                    row["timestamp"].startswith("2026-07-24")
                    for row in payload["market"]["bars_1m"]
                )
            )
            friday_5m = [
                row
                for row in payload["market"]["bars_5m"]
                if row["timestamp"].startswith("2026-07-24")
            ]
            self.assertTrue(friday_5m)
            self.assertTrue(
                all(
                    row["timestamp"].startswith("2026-07-24")
                    for row in friday_5m
                )
            )
            self.assertEqual(
                selected["data"]["session_id"],
                snapshot["session_id"],
            )
        finally:
            harness.close()

    def test_day_switch_reprepare_publishes_monday_live_market_view(self) -> None:
        calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )
        input_port = _DaySwitchLiveInput()
        harness = _LiveHttpHarness(input_port, calendar=calendar)
        try:
            selected = harness.command(
                "select_security",
                None,
                {"symbol": "sh.600000"},
            )
            friday = harness.events.get(timeout=1)
            self.assertEqual(
                friday["payload"]["live_market_view"]["effective_trade_date"],
                "2026-07-24",
            )

            runtime = harness.factory.latest_session
            assert runtime is not None
            runtime.wait_for_completion(1)
            scheduler = runtime.refresh_scheduler
            assert scheduler is not None
            scheduler.retry("quote", datetime(2026, 7, 27, 9, 31))
            phase_event = harness.events.get(timeout=1)
            monday = harness.events.get(timeout=1)

            self.assertEqual(phase_event["event_type"], "workbench_snapshot")
            self.assertEqual(monday["event_type"], "workbench_snapshot")
            self.assertEqual(
                monday["payload"]["session"]["trade_date"],
                "2026-07-27",
            )
            self.assertEqual(
                monday["payload"]["live_market_view"]["effective_trade_date"],
                "2026-07-27",
            )
            self.assertEqual(
                monday["payload"]["live_market_view"]["market_phase"],
                "morning",
            )
            self.assertEqual(input_port.prepare_calls, 2)
            self.assertEqual(
                selected["data"]["session_id"],
                monday["session_id"],
            )
        finally:
            harness.close()


if __name__ == "__main__":
    unittest.main()
