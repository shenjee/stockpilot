"""Tests for CalendarQueryPort adapters (#130 / #133 boundary)."""

from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
