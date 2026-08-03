"""09:30 atomic day switch and phase-aware polling (#130 PR-B)."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from packages.marketdata.calendar_query import FixtureCalendarQuery
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.coordinator import SessionSpec, SessionType
from packages.t0assistant.runtime.live_market_view import (
    resolve_polling_profile,
    should_run_close_reconciliation,
)
from packages.t0assistant.runtime.live_refresh import LiveRefreshKind
from packages.t0assistant.runtime.live_runtime import BranchingLiveInput
from packages.t0assistant.runtime.live_session import (
    LiveSession,
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
)
from packages.t0assistant.runtime.pipeline import PipelineMarketInput


def _bar(timestamp: str, close: float = 10.0, *, closed: bool = True) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "amount": close * 100.0,
        "closed": closed,
    }


def _quote(timestamp: str, price: float = 10.0) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "latest_price": price,
        "previous_close": 9.9,
        "open": 10.0,
        "high": price,
        "low": price,
        "volume": 100.0,
        "amount": price * 100.0,
        "change_percent": 0.0,
        "volume_ratio": None,
        "order_imbalance": None,
        "turnover_rate": None,
    }


class _SwitchableSource:
    def __init__(self) -> None:
        self.context = MarketContextService(["2026-07-24", "2026-07-27"])
        self.friday = self.context.require_session("2026-07-24", "sh")
        self.monday = self.context.require_session("2026-07-27", "sh")
        self.prepare_calls = 0

    def _friday_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-23 15:00:00")],
            bars_1m=[_bar("2026-07-24 15:00:00")],
            official_5m_bars=[_bar("2026-07-24 15:00:00")],
            daily_bars_history=[],
            quote_snapshots=[],
        )

    def _monday_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 27),
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-24 15:00:00")],
            bars_1m=[_bar("2026-07-27 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[_quote("2026-07-27 09:31:00", 10.2)],
        )

    def prepare(self, spec, *, minimum_preheat_5m):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            market_input = self._friday_input(spec)
            return PreparedLiveWarmup(
                market_session=self.friday,
                target_time=datetime(2026, 7, 24, 15, 0),
                market_input_port=_Port(market_input),
                calendar_status="available",
                market_phase="closed",
            )
        market_input = self._monday_input(spec)
        return PreparedLiveWarmup(
            market_session=self.monday,
            target_time=datetime(2026, 7, 27, 9, 31),
            market_input_port=_Port(market_input),
            calendar_status="available",
            market_phase="morning",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date) -> Sequence[Mapping]:
        if str(trade_date) == "2026-07-27" and timeframe == "1m":
            return (_bar("2026-07-27 09:31:00", 10.2),)
        return (_bar("2026-07-24 15:00:00"),)

    def load_refresh_quotes(self, spec, *, trade_date) -> Sequence[Mapping]:
        if str(trade_date) == "2026-07-27":
            return (_quote("2026-07-27 09:31:00", 10.2),)
        return ()


class _Port:
    def __init__(self, value: PipelineMarketInput) -> None:
        self._value = value

    def read(self, target_time):
        return self._value


class PollingProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = FixtureCalendarQuery(
            ["2026-07-24", "2026-07-27"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-27",
        )

    def test_pre_open_is_idle(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="pre_open",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 24),
                observed_at=datetime(2026, 7, 27, 8, 30),
                calendar=self.calendar,
                market="sh",
            ),
            "idle",
        )

    def test_morning_is_active(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="morning",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 10, 0),
                calendar=self.calendar,
                market="sh",
            ),
            "active",
        )

    def test_awaiting_day_switch_is_reduced(self) -> None:
        self.assertEqual(
            resolve_polling_profile(
                market_phase="pre_open",
                calendar_status="available",
                pinned_trade_date=date(2026, 7, 24),
                observed_at=datetime(2026, 7, 27, 9, 35),
                calendar=self.calendar,
                market="sh",
                awaiting_day_switch=True,
            ),
            "reduced",
        )

    def test_close_reconciliation_window(self) -> None:
        self.assertTrue(
            should_run_close_reconciliation(
                market_phase="closed",
                observed_at=datetime(2026, 7, 24, 15, 6),
                close_reconciled=False,
            )
        )
        self.assertFalse(
            should_run_close_reconciliation(
                market_phase="closed",
                observed_at=datetime(2026, 7, 24, 15, 6),
                close_reconciled=True,
            )
        )


class AtomicDaySwitchTests(unittest.TestCase):
    def _spec(self) -> SessionSpec:
        return SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
            trade_date=None,
        )

    def test_auction_quote_before_0930_does_not_switch(self) -> None:
        class _AuctionSource(_SwitchableSource):
            def load_refresh_quotes(self, spec, *, trade_date):
                if str(trade_date) == "2026-07-27":
                    return (_quote("2026-07-27 09:20:00", 10.1),)
                return ()

        source = _AuctionSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 20),
            latest_data_time=None,
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(switched, [])
        self.assertEqual(port.market_epoch, 0)
        self.assertEqual(source.prepare_calls, 1)

    def test_post_open_quote_triggers_atomic_switch(self) -> None:
        source = _SwitchableSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][1], 1)
        self.assertEqual(
            switched[0][0].pipeline_result.trade_date.isoformat(),
            "2026-07-27",
        )
        self.assertEqual(switched[0][0].market_phase, "morning")
        self.assertEqual(source.prepare_calls, 2)

    def test_first_closed_one_minute_triggers_switch(self) -> None:
        source = _SwitchableSource()
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
            on_day_switched=lambda candidate, epoch: switched.append((candidate, epoch)),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=datetime(2026, 7, 24, 15, 0),
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][0].pipeline_result.trade_date.isoformat(), "2026-07-27")

    def test_refresh_after_switch_uses_new_trade_date(self) -> None:
        source = _SwitchableSource()
        port = BranchingLiveInput(
            source,
            calendar=FixtureCalendarQuery(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
        )
        port.prepare(self._spec(), minimum_preheat_5m=1)
        port.refresh(
            LiveRefreshKind.QUOTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 31),
            latest_data_time=None,
        )
        self.assertEqual(port.market_epoch, 1)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self._spec(),
            observed_at=datetime(2026, 7, 27, 9, 32),
            latest_data_time=None,
        )
        self.assertNotEqual(result.updates, ())
        self.assertEqual(
            result.updates[0].payload["bars"][0]["timestamp"],
            "2026-07-27 09:31:00",
        )


if __name__ == "__main__":
    unittest.main()
