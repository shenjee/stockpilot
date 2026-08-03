"""Deterministic tests for Live effective trade-date resolution (#130 PR-A)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta

from packages.marketdata.calendar_query import FixtureCalendarQuery
from packages.marketdata.services.market_context_service import MarketSession
from packages.t0assistant.runtime.live_market_view import (
    LiveMarketViewError,
    assess_data_quality,
    assess_symbol_availability,
    build_live_market_view,
    resolve_live_market_context,
    resolve_market_closed_reason,
    resolve_security_data_trade_date,
)


def _bar(timestamp: str, *, closed: bool = True, price: float = 10.0) -> dict:
    return {
        "timestamp": timestamp,
        "open": price,
        "high": price + 0.1,
        "low": price - 0.1,
        "close": price,
        "volume": 100.0,
        "amount": price * 100.0,
        "closed": closed,
    }


def _closed_session_bars(session: MarketSession, *, minutes: int) -> list[dict]:
    return [
        _bar(moment.strftime("%Y-%m-%d %H:%M:%S"))
        for moment in session.bar_close_times(minutes)
    ]


def _preheat_bars(count: int, *, start: datetime) -> list[dict]:
    bars = []
    moment = start
    for _ in range(count):
        bars.append(_bar(moment.strftime("%Y-%m-%d %H:%M:%S")))
        moment -= timedelta(minutes=5)
    return list(reversed(bars))


class ResolveLiveMarketContextTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fri 2026-07-24 trading; Sat/Sun covered as closed; Mon holiday fixture.
        self.calendar = FixtureCalendarQuery(
            ["2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"],
            coverage_start="2026-07-20",
            coverage_end="2026-07-28",
        )

    def test_weekend_uses_previous_trading_day(self) -> None:
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 25, 10, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.market_phase, "market_closed")
        self.assertEqual(resolved.calendar_status, "available")

    def test_weekday_holiday_uses_previous_trading_day(self) -> None:
        # 2026-07-28 is in coverage but not a trading day (fixture holiday).
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 28, 11, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-27")
        self.assertEqual(resolved.market_phase, "market_closed")
        self.assertEqual(resolved.calendar_status, "available")

    def test_pre_open_uses_previous_trading_day(self) -> None:
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 27, 8, 30, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.market_phase, "pre_open")
        self.assertEqual(resolved.calendar_status, "available")

    def test_morning_uses_current_trading_day(self) -> None:
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 24, 10, 15, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.market_phase, "morning")
        self.assertEqual(resolved.calendar_status, "available")

    def test_lunch_break_keeps_current_trading_day(self) -> None:
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 24, 12, 30, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.market_phase, "lunch_break")

    def test_after_close_keeps_current_trading_day(self) -> None:
        resolved = resolve_live_market_context(
            self.calendar,
            observed_now=datetime(2026, 7, 24, 15, 45, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.market_phase, "closed")

    def test_outside_coverage_marks_calendar_unavailable_and_phase_unknown(self) -> None:
        short = FixtureCalendarQuery(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-24",
        )
        resolved = resolve_live_market_context(
            short,
            observed_now=datetime(2026, 7, 25, 10, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-24")
        self.assertEqual(resolved.calendar_status, "unavailable")
        self.assertEqual(resolved.market_phase, "unknown")

    def test_rejects_timezone_aware_clock(self) -> None:
        from datetime import timezone

        with self.assertRaises(LiveMarketViewError):
            resolve_live_market_context(
                self.calendar,
                observed_now=datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc),
                market="sh",
            )

    def test_unknown_weekday_after_authoritative_through(self) -> None:
        from packages.marketdata.calendar_query import MarketContextCalendarAdapter
        from packages.marketdata.services.market_context_service import (
            MarketContextService,
        )

        context = MarketContextService(
            ["2026-09-29", "2026-09-30"],
            coverage_start="2026-09-29",
            coverage_end="2026-10-02",
        )
        calendar = MarketContextCalendarAdapter(
            context,
            authoritative_through="2026-09-30",
        )
        resolved = resolve_live_market_context(
            calendar,
            observed_now=datetime(2026, 10, 2, 10, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-09-30")
        self.assertEqual(resolved.calendar_status, "unavailable")
        self.assertEqual(resolved.market_phase, "unknown")

    def test_weekend_with_unknown_gap_is_not_authoritative(self) -> None:
        """Last evidence Wednesday + unknown Thu/Fri + Saturday must be unavailable."""

        from packages.marketdata.calendar_query import MarketContextCalendarAdapter
        from packages.marketdata.services.market_context_service import (
            MarketContextService,
        )

        context = MarketContextService(
            ["2026-07-22"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-25",
        )
        calendar = MarketContextCalendarAdapter(
            context,
            authoritative_through="2026-07-22",
        )
        resolved = resolve_live_market_context(
            calendar,
            observed_now=datetime(2026, 7, 25, 11, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.effective_trade_date.isoformat(), "2026-07-22")
        self.assertEqual(resolved.calendar_status, "unavailable")
        self.assertEqual(resolved.market_phase, "unknown")

    def test_non_authoritative_scaffold_never_reports_available(self) -> None:
        from packages.marketdata.calendar_query import MarketContextCalendarAdapter
        from packages.marketdata.services.market_context_service import (
            MarketContextService,
        )

        context = MarketContextService(
            ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"],
            coverage_start="2026-09-29",
            coverage_end="2026-10-02",
        )
        calendar = MarketContextCalendarAdapter(
            context,
            evidence_authoritative=False,
        )
        resolved = resolve_live_market_context(
            calendar,
            observed_now=datetime(2026, 10, 2, 10, 0, 0),
            market="sh",
        )
        self.assertEqual(resolved.calendar_status, "unavailable")
        self.assertEqual(resolved.market_phase, "unknown")


class LiveMarketViewProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trade_date = date(2026, 7, 24)
        self.session = MarketSession(market="sh", trade_date=self.trade_date)
        self.target_time = datetime.combine(self.trade_date, time(15, 0))

    def test_market_closed_reason_uses_observed_now_not_effective_day(self) -> None:
        self.assertEqual(
            resolve_market_closed_reason(
                observed_now=datetime(2026, 7, 25, 10, 0),
                market_phase="market_closed",
                calendar_status="available",
            ),
            "weekend",
        )

    def test_symbol_availability_marks_fallback_as_no_current_data(self) -> None:
        self.assertEqual(
            assess_symbol_availability(
                market_candidate_trade_date=date(2026, 7, 24),
                security_data_trade_date=date(2026, 7, 23),
            ),
            "no_current_data",
        )
        self.assertEqual(
            assess_symbol_availability(
                market_candidate_trade_date=date(2026, 7, 24),
                security_data_trade_date=date(2026, 7, 24),
            ),
            "available",
        )

    def test_resolve_security_data_trade_date_uses_latest_intraday_day(self) -> None:
        self.assertEqual(
            resolve_security_data_trade_date(
                [_bar("2026-07-23 15:00:00")],
                [_bar("2026-07-24 09:35:00")],
            ),
            date(2026, 7, 24),
        )

    def test_resolve_security_data_trade_date_uses_target_day_quote(self) -> None:
        self.assertEqual(
            resolve_security_data_trade_date(
                [],
                [],
                [{"timestamp": "2026-07-24 09:31:30"}],
            ),
            date(2026, 7, 24),
        )

    def test_assess_data_quality_full_when_no_closed_5m_expected_yet(self) -> None:
        session = MarketSession(market="sh", trade_date=date(2026, 7, 27))
        target_time = datetime(2026, 7, 27, 9, 31)
        bars_1m = [_bar("2026-07-27 09:31:00")]
        self.assertEqual(
            assess_data_quality(
                closed_5m_prefix_count=500,
                bars_1m=bars_1m,
                bars_5m=[],
                daily_rows=[],
                trade_date=date(2026, 7, 27),
                market_session=session,
                target_time=target_time,
                market_phase="morning",
            ),
            "full",
        )

    def test_assess_data_quality_requires_complete_intraday_not_single_bar(self) -> None:
        session = self.session
        bars_1m = [_bar("2026-07-24 09:31:00")]
        bars_5m = [_bar("2026-07-24 09:35:00")]
        daily = [_bar("2026-07-24 15:00:00")]
        self.assertEqual(
            assess_data_quality(
                closed_5m_prefix_count=500,
                bars_1m=bars_1m,
                bars_5m=bars_5m,
                daily_rows=daily,
                trade_date=self.trade_date,
                market_session=session,
                target_time=self.target_time,
                market_phase="market_closed",
            ),
            "partial",
        )

    def test_assess_data_quality_full_requires_complete_closed_session(self) -> None:
        session = self.session
        bars_1m = _closed_session_bars(session, minutes=1)
        bars_5m = _closed_session_bars(session, minutes=5)
        daily = [_bar("2026-07-24 15:00:00")]
        self.assertEqual(
            assess_data_quality(
                closed_5m_prefix_count=500,
                bars_1m=bars_1m,
                bars_5m=bars_5m,
                daily_rows=daily,
                trade_date=self.trade_date,
                market_session=session,
                target_time=self.target_time,
                market_phase="market_closed",
            ),
            "full",
        )

    def test_build_live_market_view_projects_branch_as_of(self) -> None:
        session = self.session
        bars_1m = _closed_session_bars(session, minutes=1)[:1]
        bars_5m = _closed_session_bars(session, minutes=5)[:1]
        closed_prefix = _preheat_bars(500, start=datetime(2026, 7, 23, 15, 0))
        payload = build_live_market_view(
            effective_trade_date="2026-07-24",
            calendar_status="available",
            market_phase="morning",
            polling_profile="active",
            market={
                "bars_1m": bars_1m,
                "bars_5m": bars_5m,
                "daily_bars": [_bar("2026-07-24 15:00:00")],
                "quote": {
                    "timestamp": "2026-07-24 09:31:03",
                    "latest_price": 10.0,
                    "change_percent": 0.0,
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "previous_close": 9.9,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "volume_ratio": None,
                    "order_imbalance": None,
                    "turnover_rate": None,
                },
            },
            indicators={"one_minute": {"vwap": []}, "five_minute": {"ma": {}}},
            chan_analysis={"fractals": []},
            closed_5m_prefix=closed_prefix,
            closed_5m_prefix_count=len(closed_prefix),
            target_time=datetime(2026, 7, 24, 9, 31),
            market_session=session,
        )
        self.assertEqual(payload["effective_trade_date"], "2026-07-24")
        self.assertEqual(payload["data_quality"], "full")
        self.assertEqual(payload["quote_as_of"], "2026-07-24 09:31:03")
        self.assertEqual(payload["bars_1m_as_of"], "2026-07-24 09:31:00")
        self.assertEqual(payload["one_minute_indicators_as_of"], "2026-07-24 09:31:00")
        self.assertEqual(payload["czsc_as_of"], closed_prefix[-1]["timestamp"])
        self.assertEqual(payload["polling_profile"], "active")


if __name__ == "__main__":
    unittest.main()
