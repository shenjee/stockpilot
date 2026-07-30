from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys
import tempfile
import unittest


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.event_publisher import EventPublisher  # noqa: E402
from backend.live_application import (  # noqa: E402
    LiveApplicationApi,
    LiveSessionFactory,
)
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)
from packages.t0assistant.preferences import PreferenceService, PreferenceValues  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqlitePreferenceRepository,
    open_app_database,
)
from packages.t0assistant.runtime import (  # noqa: E402
    AppMode,
    PipelineMarketInput,
)
from packages.t0assistant.runtime.live_session import PreparedLiveWarmup  # noqa: E402


def _bar(timestamp: str, price: float) -> dict:
    return {
        "timestamp": timestamp,
        "open": price,
        "high": price + 0.1,
        "low": price - 0.1,
        "close": price,
        "volume": 1000.0,
        "amount": price * 1000.0,
        "closed": True,
    }


def _chan(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "timeframe": "5m",
        "source": "fixture",
        "engine": "czsc",
        "engine_version": "0.10.12",
        "parameters": {},
        "fractals": [],
        "strokes": [],
        "segments": [],
        "pivot_zones": [],
        "divergences": [],
        "structure_alerts": [],
        "signal_series": [],
        "signal_events": [],
        "signal_snapshots": [],
        "candidate_point_events": [],
        "candidate_buy_points": [],
        "candidate_sell_points": [],
        "plot_primitives": [],
        "summary": [],
        "warnings": [],
        "meta": {},
    }


class _MarketInput:
    def __init__(self, target_time: datetime, value: PipelineMarketInput) -> None:
        self._target_time = target_time
        self._value = value

    def read(self, target_time: datetime) -> PipelineMarketInput:
        if target_time != self._target_time:
            raise AssertionError(target_time)
        return self._value


class _DeterministicLiveInput:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.requests = []
        self.context = MarketContextService(["2026-07-23", "2026-07-24"])

    def prepare(self, spec, *, minimum_preheat_5m):
        self.requests.append((spec, minimum_preheat_5m))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        target = datetime(2026, 7, 24, 9, 31)
        market_input = PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0),
                _bar("2026-07-23 15:00:00", 10.1),
            ],
            bars_1m=[_bar("2026-07-24 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )
        return PreparedLiveWarmup(
            market_session=self.context.require_session("2026-07-24", "sh"),
            target_time=target,
            market_input_port=_MarketInput(target, market_input),
        )


class LiveApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = open_app_database(Path(self.tempdir.name) / "app.sqlite")
        self.preferences = PreferenceService(
            SqlitePreferenceRepository(self.database)
        )
        self.publisher = EventPublisher(service_generation=7)
        self.events = self.publisher.subscribe()

    def tearDown(self) -> None:
        self.publisher.unsubscribe(self.events)
        self.database.close()
        self.tempdir.cleanup()

    def _app(
        self,
        input_port: _DeterministicLiveInput,
        *,
        restore_on_startup: bool = False,
    ) -> LiveApplicationApi:
        return LiveApplicationApi(
            service_generation=7,
            session_factory=LiveSessionFactory(
                input_port,
                analyzer=lambda bars, symbol: _chan(symbol),
            ),
            preference_service=self.preferences,
            event_publisher=self.publisher,
            restore_on_startup=restore_on_startup,
        )

    def test_first_run_is_empty_then_selection_publishes_complete_snapshot(self) -> None:
        input_port = _DeterministicLiveInput()
        app = self._app(input_port)
        self.assertIsNone(app.coordinator.snapshot.current_symbol)

        response = app.select_security(
            request_id="select-1",
            symbol="sh.600000",
        )
        event = self.events.get(timeout=1)

        self.assertTrue(response["accepted"])
        self.assertEqual(response["data"]["session_id"], event["session_id"])
        self.assertEqual(event["event_type"], "workbench_snapshot")
        self.assertEqual(event["payload"]["session"]["symbol"], "sh.600000")
        self.assertEqual(input_port.requests[0][1], 500)
        self.assertEqual(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol,
            "sh.600000",
        )

    def test_startup_restores_last_symbol_and_repeated_selection_republishes(self) -> None:
        self.preferences.save(
            PreferenceValues(last_symbol="sh.600000")
        )
        app = self._app(_DeterministicLiveInput(), restore_on_startup=True)
        first = self.events.get(timeout=1)
        self.assertEqual(app.coordinator.snapshot.current_symbol, "sh.600000")
        self.assertEqual(first["event_type"], "workbench_snapshot")

        response = app.select_security(
            request_id="select-restored",
            symbol="sh.600000",
        )
        republished = self.events.get(timeout=1)
        self.assertEqual(
            republished["session_id"],
            response["data"]["session_id"],
        )
        self.assertEqual(republished["payload"], first["payload"])

    def test_switch_retires_old_session_and_live_remains_active_in_replay_mode(self) -> None:
        app = self._app(_DeterministicLiveInput())
        first = app.select_security(request_id="first", symbol="sh.600000")
        self.events.get(timeout=1)
        old_id = first["data"]["session_id"]

        second = app.select_security(request_id="second", symbol="sz.000001")
        second_event = self.events.get(timeout=1)
        current = app.coordinator.snapshot.live_session

        self.assertNotEqual(second["data"]["session_id"], old_id)
        self.assertEqual(second_event["payload"]["session"]["symbol"], "sz.000001")
        self.assertFalse(
            app.coordinator.accepts_result(
                session_type="live",
                session_id=old_id,
                generation=1,
            )
        )
        app.coordinator.set_mode(AppMode.REPLAY)
        self.assertEqual(app.coordinator.snapshot.live_session, current)

    def test_failed_rebuild_keeps_last_snapshot_and_manual_retry_recovers_cleanly(self) -> None:
        input_port = _DeterministicLiveInput(
            [object(), RuntimeError("injected provider failure"), object()]
        )
        app = self._app(input_port)
        selected = app.select_security(request_id="select", symbol="sh.600000")
        baseline = self.events.get(timeout=1)
        old_revision = baseline["revision"]

        failed_retry = app.retry_live(
            request_id="retry-1",
            session_id=selected["data"]["session_id"],
        )
        failure = self.events.get(timeout=1)

        self.assertTrue(failed_retry["accepted"])
        self.assertEqual(failure["event_type"], "operation_failed")
        self.assertTrue(app.store.has_snapshot)
        self.assertEqual(
            app.store.current_session,
            (baseline["session_id"], 1),
        )
        self.assertEqual(app.store.current_revision, old_revision)

        recovered = app.retry_live(
            request_id="retry-2",
            session_id=failed_retry["data"]["session_id"],
        )
        recovered_event = self.events.get(timeout=1)
        self.assertTrue(recovered["accepted"])
        self.assertEqual(recovered_event["event_type"], "workbench_snapshot")
        self.assertNotEqual(recovered_event["session_id"], baseline["session_id"])

if __name__ == "__main__":
    unittest.main()
