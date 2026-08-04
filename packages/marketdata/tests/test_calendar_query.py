"""Tests for CalendarQueryPort adapters (#130 / #133 boundary)."""

from __future__ import annotations

import unittest
from datetime import date

from packages.marketdata.calendar_query import (
    FixtureCalendarQuery,
    MarketContextCalendarAdapter,
    build_market_context_from_trading_calendar,
    last_trading_day_on_or_before,
)
from packages.marketdata.services.market_context_service import (
    MarketContextError,
    MarketContextService,
)
from packages.marketdata.trading_calendar import (
    CalendarUnavailableError,
    TradingCalendar,
)


class CalendarQueryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-20",
            coverage_end="2026-07-25",
        )
        self.calendar = MarketContextCalendarAdapter(self.context)

    def test_day_status_open_closed_and_out_of_coverage_raises(self) -> None:
        self.assertEqual(self.calendar.day_status("2026-07-24", "sh"), "open")
        self.assertEqual(self.calendar.day_status("2026-07-25", "sh"), "closed")
        # Out-of-coverage raises instead of returning "unknown".
        with self.assertRaises(MarketContextError):
            self.calendar.day_status("2026-07-26", "sh")

    def test_is_trading_day_still_raises_outside_coverage(self) -> None:
        with self.assertRaises(MarketContextError):
            self.calendar.is_trading_day("2026-07-26", "sh")

    def test_previous_trading_day_in_coverage_excludes_given_day(self) -> None:
        # 2026-07-24 is a trading day; its previous is 2026-07-23.
        self.assertEqual(
            self.calendar.previous_trading_day("2026-07-24", "sh"),
            date(2026, 7, 23),
        )
        # A non-trading day in coverage walks back to the last open day.
        self.assertEqual(
            self.calendar.previous_trading_day("2026-07-25", "sh"),
            date(2026, 7, 24),
        )

    def test_previous_trading_day_clamps_past_coverage_end(self) -> None:
        """Querying past coverage_end clamps to the bound instead of raising.

        ``previous_trading_day`` never raises for a date beyond coverage_end;
        it walks back to the last in-coverage trading day.
        """
        # coverage_end is itself a trading day (Friday 2026-07-24).
        context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-24",
        )
        calendar = MarketContextCalendarAdapter(context)
        self.assertEqual(calendar.coverage_end, date(2026, 7, 24))

        # Monday beyond coverage -- clamps and walks back to the last open day.
        self.assertEqual(
            calendar.previous_trading_day("2026-07-27", "sh"),
            date(2026, 7, 24),
        )

    def test_previous_trading_day_clamps_when_coverage_end_not_trading_day(
        self,
    ) -> None:
        """Non-trading coverage bound walks back to the last open day."""
        # coverage_end is Saturday 2026-07-25 (not a trading day).
        context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-25",
        )
        calendar = MarketContextCalendarAdapter(context)

        self.assertEqual(
            calendar.previous_trading_day("2026-07-27", "sh"),
            date(2026, 7, 24),
        )

    def test_fixture_calendar_and_last_trading_day_helper(self) -> None:
        fixture = FixtureCalendarQuery(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-25",
        )
        self.assertEqual(
            last_trading_day_on_or_before(fixture, "2026-07-25", "sh").isoformat(),
            "2026-07-24",
        )
        self.assertEqual(
            last_trading_day_on_or_before(fixture, "2026-07-24", "sh").isoformat(),
            "2026-07-24",
        )

    def test_last_trading_day_past_coverage_clamps(self) -> None:
        """``last_trading_day_on_or_before`` clamps past coverage_end."""
        fixture = FixtureCalendarQuery(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-24",
        )
        self.assertEqual(
            last_trading_day_on_or_before(fixture, "2026-07-30", "sh").isoformat(),
            "2026-07-24",
        )


class BuildFromTradingCalendarTests(unittest.TestCase):
    """Tests for ``build_market_context_from_trading_calendar``."""

    def setUp(self) -> None:
        self.calendar = TradingCalendar()

    def test_builds_context_for_sh(self) -> None:
        context = build_market_context_from_trading_calendar(self.calendar, "sh")
        # Coverage spans full years.
        self.assertLessEqual(context.coverage_start, date(2026, 1, 1))
        self.assertGreaterEqual(context.coverage_end, date(2026, 12, 31))
        # 2026-01-05 is a Monday and not in the holiday list, so it should be open.
        self.assertTrue(context.is_trading_day(date(2026, 1, 5), "sh"))
        # 2026-01-01 (New Year) is in the closed_dates list.
        self.assertFalse(context.is_trading_day(date(2026, 1, 1), "sh"))

    def test_unsupported_market_raises(self) -> None:
        with self.assertRaises(CalendarUnavailableError):
            build_market_context_from_trading_calendar(self.calendar, "xx")


if __name__ == "__main__":
    unittest.main()
