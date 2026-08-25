"""Tests for market review helpers."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from packages.marketdata.trading_calendar import TradingCalendar

from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.service import (
    missing_atomic_fields,
    resolve_review_trade_date,
)
from packages.marketreview.validation import resolve_trade_date

_CHINA = ZoneInfo("Asia/Shanghai")


class TestMarketReviewService(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = TradingCalendar()

    def test_resolve_review_trade_date_defaults_to_latest_closed(self) -> None:
        resolved = resolve_review_trade_date(
            self.calendar,
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        self.assertEqual(resolved, "2026-08-24")

    def test_resolve_review_trade_date_rejects_weekend(self) -> None:
        with self.assertRaises(Exception):
            resolve_trade_date(
                self.calendar,
                requested="2026-08-22",
                now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
            )

    def test_missing_atomic_fields_includes_index_fields(self) -> None:
        repo = MarketReviewRepository(":memory:")
        missing = missing_atomic_fields(repo, "2026-08-21")
        self.assertIn("sh_index_close", missing)
        self.assertIn("cy_index_prev_close", missing)
        self.assertNotIn("ladder_snapshot", missing)
        repo.close()

    def test_missing_atomic_fields_ignores_ladder_table(self) -> None:
        repo = MarketReviewRepository(":memory:")
        repo.patch_review("2026-08-21", fields={"effective_limit_up": 10})
        missing = missing_atomic_fields(repo, "2026-08-21")
        self.assertIn("closed_limit_down", missing)
        self.assertNotIn("effective_limit_up", missing)
        self.assertNotIn("ladder_snapshot", missing)
        repo.close()


if __name__ == "__main__":
    unittest.main()
