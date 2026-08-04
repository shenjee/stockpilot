"""Benchmark probe regression tests for Calendar-unknown self-lock (#140).

These tests verify that ``BranchingLiveInput`` can break the Calendar-unknown
self-lock by probing ``sh.000001`` for intraday evidence, confirming today as
open on the shared Calendar, and then letting the normal day-switch path take
over via ``market_epoch`` atomic switching.

Scenarios covered (from Issue #140):

- D日08:30 不 probe (before 09:30)
- D日09:20 集合竞价不切日 (auction quote before 09:30)
- Calendar unknown + D日 quote -> 切日
- Calendar unknown + 闭合 1m -> 切日
- 陈旧 quote 拒绝 (stale quote from D-1 rejected)
- probe 失败 -> idle (max failures)
- Calendar closed 不 probe (weekend)
- 基准确认但股票无数据 -> no_current_data
- probe 成功后 polling profile 从 idle -> reduced -> active
"""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from packages.marketdata.calendar_query import MarketContextCalendarAdapter
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.coordinator import SessionSpec, SessionType
from packages.t0assistant.runtime.live_market_view import (
    benchmark_probe_evidence,
    is_awaiting_benchmark_probe,
)
from packages.t0assistant.runtime.live_refresh import LiveRefreshKind
from packages.t0assistant.runtime.live_runtime import BranchingLiveInput
from packages.t0assistant.runtime.live_session import (
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


class _Port:
    def __init__(self, value: PipelineMarketInput) -> None:
        self._value = value

    def read(self, target_time):
        return self._value


class _BenchmarkProbeSource:
    """Test source that serves both stock and benchmark (sh.000001) data.

    The calendar's ``coverage_end`` is set to D-1 (Friday), so ``day_status``
    for D (Monday) returns ``unknown`` — the production self-lock scenario.

    The source can be configured to return or withhold benchmark evidence for
    D, and to return or withhold stock data for D after a successful probe.
    """

    FRIDAY = date(2026, 7, 24)
    MONDAY = date(2026, 7, 27)

    def __init__(
        self,
        *,
        benchmark_quote_d: str | None = None,
        benchmark_1m_d: str | None = None,
        benchmark_5m_d: str | None = None,
        stock_has_monday_data: bool = True,
        benchmark_bars_raise: bool = False,
    ) -> None:
        self.context = MarketContextService(["2026-07-24", "2026-07-27"])
        self.friday = self.context.require_session("2026-07-24", "sh")
        self.monday = self.context.require_session("2026-07-27", "sh")
        self.prepare_calls = 0
        self.benchmark_quote_d = benchmark_quote_d
        self.benchmark_1m_d = benchmark_1m_d
        self.benchmark_5m_d = benchmark_5m_d
        self.stock_has_monday_data = stock_has_monday_data
        self.benchmark_bars_raise = benchmark_bars_raise

    def _friday_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=self.FRIDAY,
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
            trade_date=self.MONDAY,
            previous_close=10.0,
            preheat_5m_bars=[_bar("2026-07-24 15:00:00")],
            bars_1m=[_bar("2026-07-27 09:31:00", 10.2)]
            if self.stock_has_monday_data
            else [],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[_quote("2026-07-27 09:31:00", 10.2)]
            if self.stock_has_monday_data
            else [],
        )

    def prepare(self, spec, *, minimum_preheat_5m):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            market_input = self._friday_input(spec)
            return PreparedLiveWarmup(
                market_session=self.friday,
                target_time=datetime(2026, 7, 24, 15, 0),
                observed_now=datetime(2026, 7, 24, 15, 0),
                market_candidate_trade_date=self.FRIDAY,
                market_input_port=_Port(market_input),
                calendar_status="available",
                market_phase="closed",
            )
        market_input = self._monday_input(spec)
        return PreparedLiveWarmup(
            market_session=self.monday,
            target_time=datetime(2026, 7, 27, 9, 31),
            observed_now=datetime(2026, 7, 27, 9, 31),
            market_candidate_trade_date=self.MONDAY,
            market_input_port=_Port(market_input),
            calendar_status="available",
            market_phase="morning" if self.stock_has_monday_data else "unknown",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date) -> Sequence[Mapping]:
        if spec.symbol == "sh.000001":
            # Benchmark probe data
            if self.benchmark_bars_raise:
                raise RuntimeError("benchmark bar provider unavailable (#140 P2)")
            if str(trade_date) == "2026-07-27":
                if timeframe == "1m" and self.benchmark_1m_d:
                    return (_bar(self.benchmark_1m_d, 3100.0),)
                if timeframe == "5m" and self.benchmark_5m_d:
                    return (_bar(self.benchmark_5m_d, 3100.0),)
                return ()
            return ()
        # Stock data
        if str(trade_date) == "2026-07-27" and self.stock_has_monday_data:
            if timeframe == "1m":
                return (_bar("2026-07-27 09:31:00", 10.2),)
            if timeframe == "5m":
                return (_bar("2026-07-27 09:35:00", 10.2),)
            return ()
        if str(trade_date) == "2026-07-24":
            return (_bar("2026-07-24 15:00:00"),)
        return ()

    def load_refresh_quotes(self, spec, *, trade_date) -> Sequence[Mapping]:
        if spec.symbol == "sh.000001":
            # Benchmark probe data
            if str(trade_date) == "2026-07-27" and self.benchmark_quote_d:
                return (_quote(self.benchmark_quote_d, 3100.0),)
            return ()
        # Stock data
        if str(trade_date) == "2026-07-27" and self.stock_has_monday_data:
            return (_quote("2026-07-27 09:31:00", 10.2),)
        return ()


def _make_calendar() -> MarketContextCalendarAdapter:
    """Calendar authoritative through Friday only; Monday is ``unknown``.

    The ``MarketContextService`` only knows about Friday's trading day.
    Monday (2026-07-27) is outside coverage and will return ``unknown``
    until the benchmark probe confirms it.
    """
    return MarketContextCalendarAdapter(
        MarketContextService(
            ["2026-07-24"],
            coverage_start="2026-07-24",
            coverage_end="2026-07-24",
        ),
        authoritative_through=date(2026, 7, 24),
        evidence_authoritative=True,
    )


def _spec() -> SessionSpec:
    return SessionSpec(
        session_id="live-1",
        session_type=SessionType.LIVE,
        symbol="sh.600000",
        generation=1,
        trade_date=None,
    )


class BenchmarkProbeHelperTests(unittest.TestCase):
    """Unit tests for ``is_awaiting_benchmark_probe`` and ``benchmark_probe_evidence``."""

    def test_no_probe_before_0930(self) -> None:
        calendar = _make_calendar()
        self.assertFalse(
            is_awaiting_benchmark_probe(
                calendar,
                observed_at=datetime(2026, 7, 27, 8, 30),
                pinned_trade_date=date(2026, 7, 24),
                market="sh",
            )
        )

    def test_no_probe_when_calendar_known(self) -> None:
        calendar = MarketContextCalendarAdapter(
            MarketContextService(
                ["2026-07-24", "2026-07-27"],
                coverage_start="2026-07-24",
                coverage_end="2026-07-27",
            ),
        )
        self.assertFalse(
            is_awaiting_benchmark_probe(
                calendar,
                observed_at=datetime(2026, 7, 27, 10, 0),
                pinned_trade_date=date(2026, 7, 24),
                market="sh",
            )
        )

    def test_no_probe_on_weekend(self) -> None:
        calendar = _make_calendar()
        # 2026-07-25 is Saturday
        self.assertFalse(
            is_awaiting_benchmark_probe(
                calendar,
                observed_at=datetime(2026, 7, 25, 10, 0),
                pinned_trade_date=date(2026, 7, 24),
                market="sh",
            )
        )

    def test_probe_when_calendar_unknown_and_past_0930(self) -> None:
        calendar = _make_calendar()
        self.assertTrue(
            is_awaiting_benchmark_probe(
                calendar,
                observed_at=datetime(2026, 7, 27, 9, 31),
                pinned_trade_date=date(2026, 7, 24),
                market="sh",
            )
        )

    def test_no_probe_when_today_equals_pinned(self) -> None:
        calendar = _make_calendar()
        self.assertFalse(
            is_awaiting_benchmark_probe(
                calendar,
                observed_at=datetime(2026, 7, 27, 10, 0),
                pinned_trade_date=date(2026, 7, 27),
                market="sh",
            )
        )

    def test_evidence_accepts_today_quote(self) -> None:
        self.assertTrue(
            benchmark_probe_evidence(
                [_quote("2026-07-27 09:31:00", 3100.0)],
                [],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 31),
            )
        )

    def test_evidence_accepts_today_closed_1m(self) -> None:
        self.assertTrue(
            benchmark_probe_evidence(
                [],
                [_bar("2026-07-27 09:31:00", 3100.0, closed=True)],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 32),
            )
        )

    def test_evidence_rejects_stale_quote_from_yesterday(self) -> None:
        self.assertFalse(
            benchmark_probe_evidence(
                [_quote("2026-07-24 15:00:00", 3100.0)],
                [],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 31),
            )
        )

    def test_evidence_rejects_future_timestamp(self) -> None:
        self.assertFalse(
            benchmark_probe_evidence(
                [_quote("2026-07-27 09:35:00", 3100.0)],
                [],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 31),
            )
        )

    def test_evidence_rejects_open_1m_bar(self) -> None:
        self.assertFalse(
            benchmark_probe_evidence(
                [],
                [_bar("2026-07-27 09:31:00", 3100.0, closed=False)],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 32),
            )
        )

    def test_evidence_accepts_today_closed_5m(self) -> None:
        self.assertTrue(
            benchmark_probe_evidence(
                [],
                [],
                [_bar("2026-07-27 09:35:00", 3100.0, closed=True)],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 36),
            )
        )

    def test_evidence_rejects_before_0930(self) -> None:
        self.assertFalse(
            benchmark_probe_evidence(
                [_quote("2026-07-27 09:25:00", 3100.0)],
                [],
                [],
                target_trade_date=date(2026, 7, 27),
                observed_at=datetime(2026, 7, 27, 9, 25),
            )
        )


class BenchmarkProbeRuntimeTests(unittest.TestCase):
    """Integration tests for the probe inside ``BranchingLiveInput.refresh``."""

    def setUp(self) -> None:
        self._monday_morning = datetime(2026, 7, 27, 9, 31)

    def _prepare(
        self,
        source: _BenchmarkProbeSource,
        calendar: MarketContextCalendarAdapter,
    ) -> BranchingLiveInput:
        switched: list[tuple[LiveSnapshotCandidate, int]] = []
        port = BranchingLiveInput(
            source,
            calendar=calendar,
            on_day_switched=lambda candidate, epoch: switched.append(
                (candidate, epoch)
            ),
        )
        port._test_switched = switched  # type: ignore[attr-defined]
        port.prepare(_spec(), minimum_preheat_5m=1)
        return port

    def test_no_probe_before_0930(self) -> None:
        """D日08:30 不 probe — polling stays idle."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        profile = port.polling_profile(datetime(2026, 7, 27, 8, 30))
        self.assertEqual(profile, "idle")

        # Verify calendar still reports unknown for Monday
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )

    def test_auction_quote_before_0930_does_not_probe(self) -> None:
        """D日09:20 集合竞价不切日 — probe not triggered before 09:30."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:20:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=datetime(2026, 7, 27, 9, 20),
            latest_data_time=None,
        )

        self.assertEqual(result.updates, ())
        self.assertEqual(port.market_epoch, 0)
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )

    def test_calendar_unknown_with_today_quote_triggers_switch(self) -> None:
        """Calendar unknown + D日 quote -> probe confirms open -> atomic switch."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # Before probe: calendar unknown, polling idle
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )

        result = port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # Probe confirmed open day
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        # Day switch happened (epoch incremented)
        self.assertEqual(port.market_epoch, 1)
        # Switch handler was called
        switched = port._test_switched  # type: ignore[attr-defined]
        self.assertEqual(len(switched), 1)
        self.assertEqual(switched[0][1], 1)

    def test_calendar_unknown_with_closed_1m_triggers_switch(self) -> None:
        """Calendar unknown + 闭合 1m -> probe confirms open -> atomic switch."""
        source = _BenchmarkProbeSource(
            benchmark_1m_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        result = port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            _spec(),
            observed_at=datetime(2026, 7, 27, 9, 32),
            latest_data_time=None,
        )

        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        self.assertEqual(port.market_epoch, 1)

    def test_calendar_unknown_with_closed_5m_triggers_switch(self) -> None:
        """Calendar unknown + 闭合 5m -> probe confirms open -> atomic switch."""
        source = _BenchmarkProbeSource(
            benchmark_5m_d="2026-07-27 09:35:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        result = port.refresh(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            _spec(),
            observed_at=datetime(2026, 7, 27, 9, 36),
            latest_data_time=None,
        )

        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        self.assertEqual(port.market_epoch, 1)

    def test_stale_quote_rejected(self) -> None:
        """陈旧 quote 拒绝 — probe finds D-1 quote, rejects, no switch."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-24 15:00:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # Calendar still unknown (stale quote rejected)
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )
        self.assertEqual(port.market_epoch, 0)
        # One failure recorded
        self.assertEqual(port._benchmark_probe_failures, 1)

    def test_probe_failure_then_max_failures_to_idle(self) -> None:
        """probe 失败 -> idle — after max failures, probe stops and polling idle."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d=None,
            benchmark_1m_d=None,
            benchmark_5m_d=None,
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # Run max+1 refreshes; each should fail the probe
        from packages.t0assistant.runtime.live_runtime import (
            _BENCHMARK_PROBE_MAX_FAILURES,
        )

        observed_at = self._monday_morning
        for i in range(_BENCHMARK_PROBE_MAX_FAILURES):
            port.refresh(
                LiveRefreshKind.QUOTE,
                _spec(),
                observed_at=observed_at,
                latest_data_time=None,
            )
            observed_at += timedelta(seconds=31)

        # After max failures, probe is exhausted
        self.assertEqual(
            port._benchmark_probe_failures,
            _BENCHMARK_PROBE_MAX_FAILURES,
        )
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )
        self.assertEqual(port.market_epoch, 0)

        # Polling profile should be idle (probe exhausted, calendar unknown)
        profile = port.polling_profile(observed_at)
        self.assertEqual(profile, "idle")

    def test_weekend_no_probe(self) -> None:
        """Calendar closed 不 probe — Saturday is not a trading day."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-25 10:00:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # 2026-07-25 is Saturday
        result = port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=datetime(2026, 7, 25, 10, 0),
            latest_data_time=None,
        )

        # No probe, no switch
        self.assertEqual(port.market_epoch, 0)
        self.assertEqual(port._benchmark_probe_failures, 0)

    def test_probe_confirms_but_stock_has_no_data(self) -> None:
        """基准确认但股票无数据 -> probe confirms open, day switch may still fail gracefully."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
            stock_has_monday_data=False,
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # Calendar was confirmed (benchmark evidence found)
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        # Probe was confirmed
        self.assertTrue(port._benchmark_probe_confirmed)
        # Day switch may or may not succeed depending on whether the source
        # prepare() returns a valid session. The key is that the probe broke
        # the Calendar-unknown lock.

    def test_polling_transitions_idle_to_reduced_on_probe(self) -> None:
        """Polling profile goes from idle to reduced when probe is active."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # Before 09:30: idle (calendar unknown, not probe time yet)
        profile_before = port.polling_profile(datetime(2026, 7, 27, 8, 30))
        self.assertEqual(profile_before, "idle")

        # After 09:30: reduced (probe is active)
        profile_after = port.polling_profile(self._monday_morning)
        self.assertEqual(profile_after, "reduced")

    def test_polling_back_to_active_after_switch(self) -> None:
        """After successful probe + day switch, polling is active (morning)."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # After switch, session is Monday morning -> active
        profile = port.polling_profile(self._monday_morning)
        self.assertEqual(profile, "active")

    def test_probe_does_not_re_fire_after_confirmation(self) -> None:
        """Once probe is confirmed and day switches, it doesn't re-fire.

        After a successful day switch, the probe state is reset and the
        session is pinned to today (Monday).  ``is_awaiting_benchmark_probe``
        returns ``False`` because ``today == pinned_trade_date``.
        """
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # First refresh: probe fires, confirms, switches
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )
        # Day switch happened
        self.assertEqual(port.market_epoch, 1)
        # Probe state was reset after switch
        self.assertEqual(port._benchmark_probe_failures, 0)
        self.assertFalse(port._benchmark_probe_confirmed)

        # Second refresh: probe should not re-fire (today == pinned_trade_date)
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning + timedelta(seconds=3),
            latest_data_time=None,
        )
        self.assertEqual(port._benchmark_probe_failures, 0)

    def test_probe_reset_after_day_switch(self) -> None:
        """Probe state resets after a successful day switch (#140)."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # After switch, probe state is reset
        self.assertEqual(port._benchmark_probe_failures, 0)
        self.assertIsNone(port._benchmark_probe_next_retry_at)
        self.assertFalse(port._benchmark_probe_confirmed)

    def test_shared_calendar_updated_across_consumers(self) -> None:
        """All consumers sharing the calendar see the confirmed open day."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
        )
        calendar = _make_calendar()

        # Simulate another consumer checking the calendar before probe
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "unknown",
        )
        self.assertEqual(calendar.coverage_end, date(2026, 7, 24))

        port = self._prepare(source, calendar)
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # After probe, the shared calendar reflects the confirmed open day
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        self.assertEqual(calendar.coverage_end, date(2026, 7, 27))
        self.assertTrue(calendar.is_trading_day(date(2026, 7, 27), "sh"))

    def test_retry_interval_between_probe_attempts(self) -> None:
        """Probe respects retry interval — no immediate re-probe after failure."""
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-24 15:00:00",  # stale -> failure
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        # First probe: fails (stale quote)
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )
        self.assertEqual(port._benchmark_probe_failures, 1)
        self.assertIsNotNone(port._benchmark_probe_next_retry_at)

        # Second probe immediately after: should not fire (retry not due)
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning + timedelta(seconds=5),
            latest_data_time=None,
        )
        self.assertEqual(port._benchmark_probe_failures, 1)  # still 1

        # Third probe after retry interval: should fire
        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning + timedelta(seconds=31),
            latest_data_time=None,
        )
        self.assertEqual(port._benchmark_probe_failures, 2)

    def test_probe_confirms_when_bars_raise_but_quote_valid(self) -> None:
        """quote evidence survives a 1m/5m provider error (#140 P2).

        The contract is that any one of quote / closed 1m / closed 5m confirms
        the market opened.  Previously all three reads shared one try, so a
        1m/5m error discarded the valid quote and recorded a failure instead of
        confirming.
        """
        source = _BenchmarkProbeSource(
            benchmark_quote_d="2026-07-27 09:31:00",
            benchmark_bars_raise=True,
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        port.refresh(
            LiveRefreshKind.QUOTE,
            _spec(),
            observed_at=self._monday_morning,
            latest_data_time=None,
        )

        # Valid quote evidence confirmed the open day despite 1m/5m errors,
        # and the day switch proceeded (probe broke the self-lock).
        self.assertEqual(
            calendar.day_status(date(2026, 7, 27), "sh"),
            "open",
        )
        self.assertEqual(port.market_epoch, 1)
        self.assertEqual(port._benchmark_probe_failures, 0)

    def test_concurrent_workers_run_single_probe(self) -> None:
        """Only one refresh worker runs the benchmark probe per cycle (#140 P2).

        The quote/1m/5m workers all call ``_run_benchmark_probe_if_due`` within
        the same scheduler cycle.  Without atomic ownership, every worker
        passes the due-check and fires its own quote/1m/5m request set, so a
        single failing cycle increments the failure count by 3 and exhausts the
        bounded retry budget in ~2 cycles.
        """

        class _BlockingBenchmarkSource(_BenchmarkProbeSource):
            """Force concurrent probes to overlap, then count quote loads."""

            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self._release = threading.Event()
                self.benchmark_quote_loads = 0

            def load_refresh_quotes(
                self,
                spec,
                *,
                trade_date,
            ) -> Sequence[Mapping]:
                if spec.symbol == "sh.000001" and str(trade_date) == "2026-07-27":
                    self.benchmark_quote_loads += 1
                    # Block so sibling workers reach the due-check while this
                    # probe is still in flight (the race window the bug needs).
                    self._release.wait(timeout=5)
                    return ()  # no evidence -> failure
                return super().load_refresh_quotes(spec, trade_date=trade_date)

            def release(self) -> None:
                self._release.set()

        source = _BlockingBenchmarkSource(
            benchmark_quote_d=None,
            benchmark_1m_d=None,
            benchmark_5m_d=None,
        )
        calendar = _make_calendar()
        port = self._prepare(source, calendar)

        spec = _spec()
        observed_at = self._monday_morning

        done = threading.Event()
        done_count = [0]
        done_lock = threading.Lock()

        def worker() -> None:
            port._run_benchmark_probe_if_due(spec, observed_at)
            with done_lock:
                done_count[0] += 1
                if done_count[0] >= 2:
                    done.set()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()

        # With ownership, two workers skip immediately and finish while the
        # third blocks inside the probe.  Without ownership, all three block
        # and this wait times out.
        self.assertTrue(
            done.wait(timeout=2),
            "ownership did not let sibling workers skip the in-flight probe",
        )
        source.release()
        for thread in threads:
            thread.join(timeout=2)
        self.assertFalse(any(thread.is_alive() for thread in threads))

        # Exactly one probe executed; only one failure recorded.
        self.assertEqual(source.benchmark_quote_loads, 1)
        self.assertEqual(port._benchmark_probe_failures, 1)
        self.assertIsNone(port._benchmark_probe_owner)


if __name__ == "__main__":
    unittest.main()
