"""Deterministic tests for Live effective trade-date resolution (#130 PR-A)."""

from __future__ import annotations

import unittest
from datetime import datetime

from packages.marketdata.calendar_query import FixtureCalendarQuery
from packages.t0assistant.runtime.live_market_view import (
    LiveMarketViewError,
    resolve_live_market_context,
)


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


if __name__ == "__main__":
    unittest.main()
