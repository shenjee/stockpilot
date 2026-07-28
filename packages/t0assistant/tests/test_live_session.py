from __future__ import annotations

from datetime import date, datetime
from threading import Event
import unittest
from typing import Any

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.live_session import PreparedLiveWarmup
from packages.t0assistant.runtime import (
    LiveSession,
    LiveSessionValidationError,
    PipelineMarketInput,
    SessionSpec,
    SessionType,
)


def _bar(
    timestamp: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    amount: float,
    *,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def _chan(symbol: str) -> dict[str, Any]:
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


class _PreparedPort:
    def __init__(self, prepared: PreparedLiveWarmup) -> None:
        self.prepared = prepared
        self.requests: list[tuple[SessionSpec, int]] = []

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
    ) -> PreparedLiveWarmup:
        self.requests.append((spec, minimum_preheat_5m))
        return self.prepared


class _FailingPort:
    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
    ) -> PreparedLiveWarmup:
        raise RuntimeError("boom")


class _BlockingPort:
    def __init__(self, prepared: PreparedLiveWarmup) -> None:
        self.prepared = prepared
        self.entered = Event()
        self.release = Event()

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
    ) -> PreparedLiveWarmup:
        self.entered.set()
        self.release.wait(timeout=1)
        return self.prepared


class _SingleInputPort:
    def __init__(self, target_time: datetime, market_input: PipelineMarketInput) -> None:
        self.target_time = target_time
        self.market_input = market_input

    def read(self, target_time: datetime) -> PipelineMarketInput:
        if target_time != self.target_time:
            raise AssertionError(f"unexpected target_time: {target_time!r}")
        return self.market_input


class LiveSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        calendar = MarketContextService(["2026-07-24", "2026-07-23"])
        self.market_session = calendar.require_session("2026-07-24", "sh")
        self.target_time = datetime(2026, 7, 24, 9, 31, 0)
        market_input = PipelineMarketInput(
            symbol="sh.600000",
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 1000, 10020),
                _bar("2026-07-23 15:00:00", 10.02, 10.08, 10.0, 10.05, 1200, 12060),
            ],
            bars_1m=[
                _bar("2026-07-24 09:31:00", 10.05, 10.08, 10.0, 10.06, 800, 8048),
            ],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )
        self.prepared = PreparedLiveWarmup(
            market_session=self.market_session,
            target_time=self.target_time,
            market_input_port=_SingleInputPort(self.target_time, market_input),
        )
        self.spec = SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def test_initial_load_requests_500_preheat_bars_and_emits_only_ready_candidate(self) -> None:
        port = _PreparedPort(self.prepared)
        states: list[tuple[str, str]] = []
        candidates = []
        session = LiveSession(
            self.spec,
            port,
            on_snapshot_candidate=candidates.append,
            on_state_change=lambda state, reason: states.append((state, reason)),
            analyzer=lambda bars, symbol: _chan(symbol),
        )

        self.assertTrue(session.wait_for_completion(timeout=1))

        self.assertEqual(port.requests, [(self.spec, 500)])
        self.assertEqual(
            states,
            [
                ("created", "session_created"),
                ("loading", "load_started"),
                ("ready", "load_completed"),
            ],
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].session_id, "live-1")
        self.assertEqual(candidates[0].generation, 1)
        self.assertEqual(candidates[0].pipeline_result.target_time, self.target_time)
        self.assertEqual(session.state, "ready")
        self.assertIsNone(session.failure)

    def test_failure_does_not_publish_a_partial_candidate(self) -> None:
        states: list[tuple[str, str]] = []
        candidates = []
        session = LiveSession(
            self.spec,
            _FailingPort(),
            on_snapshot_candidate=candidates.append,
            on_state_change=lambda state, reason: states.append((state, reason)),
            analyzer=lambda bars, symbol: _chan(symbol),
        )

        self.assertTrue(session.wait_for_completion(timeout=1))

        self.assertEqual(candidates, [])
        self.assertEqual(states[-1], ("failed", "operation_failed"))
        self.assertEqual(session.state, "failed")
        self.assertIsInstance(session.failure, RuntimeError)

    def test_retired_session_drops_late_initial_result(self) -> None:
        port = _BlockingPort(self.prepared)
        states: list[tuple[str, str]] = []
        candidates = []
        session = LiveSession(
            self.spec,
            port,
            on_snapshot_candidate=candidates.append,
            on_state_change=lambda state, reason: states.append((state, reason)),
            analyzer=lambda bars, symbol: _chan(symbol),
        )
        self.assertTrue(port.entered.wait(timeout=1))

        session.retire()
        port.release.set()
        self.assertTrue(session.wait_for_completion(timeout=1))

        self.assertEqual(candidates, [])
        self.assertEqual(session.state, "retired")
        self.assertEqual(states[-1], ("retired", "session_retired"))

    def test_retire_returning_prevents_candidate_callback_from_starting(self) -> None:
        port = _PreparedPort(self.prepared)
        entered = Event()
        release = Event()
        candidates = []

        session = LiveSession(
            self.spec,
            port,
            on_snapshot_candidate=candidates.append,
            analyzer=lambda bars, symbol: _chan(symbol),
            auto_start=False,
        )

        def hook() -> None:
            entered.set()
            release.wait(timeout=1)

        setattr(session, "_before_candidate_hook", hook)
        session.activate()

        self.assertTrue(entered.wait(timeout=1))
        session.retire()
        release.set()
        self.assertTrue(session.wait_for_completion(timeout=1))
        self.assertEqual(candidates, [])

    def test_candidate_is_not_published_until_data_preparation_finishes(self) -> None:
        port = _BlockingPort(self.prepared)
        candidates = []
        session = LiveSession(
            self.spec,
            port,
            on_snapshot_candidate=candidates.append,
            analyzer=lambda bars, symbol: _chan(symbol),
        )
        self.assertTrue(port.entered.wait(timeout=1))

        self.assertEqual(candidates, [])
        self.assertEqual(session.state, "loading")

        port.release.set()
        self.assertTrue(session.wait_for_completion(timeout=1))
        self.assertEqual(len(candidates), 1)

    def test_manual_activate_defers_initial_load_until_called(self) -> None:
        port = _PreparedPort(self.prepared)
        candidates = []
        session = LiveSession(
            self.spec,
            port,
            on_snapshot_candidate=candidates.append,
            analyzer=lambda bars, symbol: _chan(symbol),
            auto_start=False,
        )

        self.assertFalse(session.wait_for_completion(timeout=0.05))
        self.assertEqual(port.requests, [])

        session.activate()
        self.assertTrue(session.wait_for_completion(timeout=1))
        self.assertEqual(port.requests, [(self.spec, 500)])
        self.assertEqual(len(candidates), 1)

    def test_rejects_non_live_session_specs(self) -> None:
        bad = SessionSpec(
            session_id="replay-1",
            session_type=SessionType.REPLAY,
            symbol="sh.600000",
            generation=1,
            trade_date="2026-07-24",
        )

        with self.assertRaises(LiveSessionValidationError):
            LiveSession(
                bad,
                _PreparedPort(self.prepared),
                on_snapshot_candidate=lambda candidate: None,
            )


if __name__ == "__main__":
    unittest.main()
