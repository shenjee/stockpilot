"""Tests for CalendarQueryPort adapters (#130 / #133 boundary)."""

from __future__ import annotations

import unittest
from datetime import date

from packages.marketdata.calendar_query import (
    FixtureCalendarQuery,
    MarketContextCalendarAdapter,
    last_trading_day_on_or_before,
)
from packages.marketdata.services.market_context_service import (
    MarketContextError,
    MarketContextService,
)


class CalendarQueryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-20",
            coverage_end="2026-07-25",
        )
        self.calendar = MarketContextCalendarAdapter(self.context)

    def test_day_status_open_closed_unknown(self) -> None:
        self.assertEqual(self.calendar.day_status("2026-07-24", "sh"), "open")
        self.assertEqual(self.calendar.day_status("2026-07-25", "sh"), "closed")
        self.assertEqual(self.calendar.day_status("2026-07-26", "sh"), "unknown")

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

    def test_previous_trading_day_clamps_confirmed_day_past_coverage(self) -> None:
        """Runtime-confirmed day past coverage_end must not raise (#140 P1).

        ``confirm_open_day`` extends the adapter's reported ``coverage_end``,
        but the immutable base context's coverage is unchanged.  Querying the
        base context with the confirmed (later) day used to raise
        ``MarketContextError`` and break the atomic day switch even though the
        calendar was confirmed.
        """
        # coverage_end is itself a trading day (Friday 2026-07-24).
        context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-24",
        )
        calendar = MarketContextCalendarAdapter(context)
        self.assertEqual(calendar.coverage_end, date(2026, 7, 24))

        # Runtime-confirm Monday (beyond base coverage) via benchmark probe.
        calendar.confirm_open_day("2026-07-27")
        self.assertEqual(calendar.coverage_end, date(2026, 7, 27))
        self.assertEqual(calendar.day_status("2026-07-27", "sh"), "open")

        # Must not raise; returns the latest authoritative trading day (the
        # coverage bound itself, since it is an open day).
        self.assertEqual(
            calendar.previous_trading_day("2026-07-27", "sh"),
            date(2026, 7, 24),
        )

    def test_previous_trading_day_clamps_when_coverage_end_not_trading_day(
        self,
    ) -> None:
        """Non-trading coverage bound walks back to the last open day (#140 P1)."""
        # coverage_end is Saturday 2026-07-25 (not a trading day).
        context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-25",
        )
        calendar = MarketContextCalendarAdapter(context)
        calendar.confirm_open_day("2026-07-27")

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

    def test_authoritative_through_marks_later_weekdays_unknown(self) -> None:
        context = MarketContextService(
            ["2026-09-29", "2026-09-30"],
            coverage_start="2026-09-29",
            coverage_end="2026-10-02",
        )
        calendar = MarketContextCalendarAdapter(
            context,
            authoritative_through="2026-09-30",
        )
        self.assertEqual(calendar.day_status("2026-09-30", "sh"), "open")
        # Weekday holiday-like gap after last evidenced open day.
        self.assertEqual(calendar.day_status("2026-10-01", "sh"), "unknown")
        self.assertEqual(calendar.day_status("2026-10-02", "sh"), "unknown")

    def test_non_authoritative_scaffold_marks_every_day_unknown(self) -> None:
        context = MarketContextService(
            ["2026-09-30", "2026-10-01", "2026-10-02"],
            coverage_start="2026-09-30",
            coverage_end="2026-10-02",
        )
        calendar = MarketContextCalendarAdapter(
            context,
            evidence_authoritative=False,
        )
        self.assertEqual(calendar.day_status("2026-10-01", "sh"), "unknown")
        self.assertEqual(calendar.day_status("2026-10-02", "sh"), "unknown")
        self.assertFalse(calendar.evidence_authoritative)


if __name__ == "__main__":
    unittest.main()
