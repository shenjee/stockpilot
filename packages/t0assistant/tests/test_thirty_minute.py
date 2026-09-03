"""Tests for the dynamic thirty-minute aggregator.

The critical difference from the 5m aggregator is the merge semantics
(§8.3): an official 30m bar replaces only the temporary bar with the same
end-time and must **never** delete the next bucket's still-forming temporary
bar.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from packages.marketdata.services.market_context_service import MarketSession  # noqa: E402
from packages.t0assistant.runtime.thirty_minute import (  # noqa: E402
    DynamicThirtyMinuteAggregator,
)
from packages.t0assistant.runtime._market_bars import RuntimeMarketDataError  # noqa: E402


def _1m_bar(timestamp: str, *, close: float, volume: int = 100,
            amount: float = 1000.0) -> dict:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": True,
    }


def _official_30m(timestamp: str, *, close: float) -> dict:
    return {
        "timestamp": timestamp,
        "open": close - 1.0,
        "high": close + 0.5,
        "low": close - 1.5,
        "close": close,
        "volume": 3000,
        "amount": 30000.0,
        "closed": True,
    }


class DynamicThirtyMinuteAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MarketSession(market="sh", trade_date=date(2026, 9, 1))
        self.agg = DynamicThirtyMinuteAggregator(self.session)

    def test_first_temp_bar_uses_0931_to_1000_one_minute_bars(self) -> None:
        # 09:31 through 10:00 = 30 one-minute bars → end-time 10:00
        for minute in range(31, 60):
            ts = f"2026-09-01 09:{minute:02d}:00"
            self.agg.update_one_minute(_1m_bar(ts, close=10.0 + minute))
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:00:00", close=10.6))

        display = self.agg.display_bars
        self.assertEqual(len(display), 1)
        self.assertEqual(display[0]["timestamp"], "2026-09-01 10:00:00")
        self.assertFalse(display[0]["closed"])
        # analysis_bars is empty until an official bar arrives
        self.assertEqual(self.agg.analysis_bars, ())

    def test_official_replaces_same_end_time_temp_only(self) -> None:
        # Build temp 10:00 bar (09:31–10:00 = 30 one-minute bars)
        for minute in range(31, 60):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 09:{minute:02d}:00", close=10.0))
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:00:00", close=10.0))
        # Start building temp 10:30 bar (one 1m bar at 10:01)
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:01:00", close=11.0))

        display_before = self.agg.display_bars
        # Only the current forming 10:30 temp bar exists (10:00 temp was cleared)
        self.assertEqual(len(display_before), 1)
        self.assertEqual(display_before[0]["timestamp"], "2026-09-01 10:30:00")
        self.assertFalse(display_before[0]["closed"])

        # Official 10:00 bar arrives
        self.agg.accept_official(_official_30m("2026-09-01 10:00:00", close=10.5))

        display_after = self.agg.display_bars
        # CRITICAL: the 10:30 temp bar must still exist (not deleted by official)
        self.assertEqual(len(display_after), 2)
        self.assertEqual(display_after[0]["timestamp"], "2026-09-01 10:00:00")
        self.assertTrue(display_after[0]["closed"])
        self.assertEqual(display_after[1]["timestamp"], "2026-09-01 10:30:00")
        self.assertFalse(display_after[1]["closed"], "next temp bar must not be deleted")

        # analysis_bars now has the official 10:00 bar
        analysis = self.agg.analysis_bars
        self.assertEqual(len(analysis), 1)
        self.assertEqual(analysis[0]["timestamp"], "2026-09-01 10:00:00")
        self.assertTrue(analysis[0]["closed"])

    def test_late_one_minute_cannot_overwrite_official(self) -> None:
        self.agg.accept_official(_official_30m("2026-09-01 10:00:00", close=10.5))
        # A late 1m bar for the 10:00 bucket should not overwrite the official bar
        result = self.agg.update_one_minute(
            _1m_bar("2026-09-01 09:45:00", close=99.0)
        )
        self.assertEqual(result["close"], 10.5)
        self.assertTrue(result["closed"])

    def test_expired_bucket_rejected(self) -> None:
        # Build 10:00 bucket (09:31–10:00)
        for minute in range(31, 60):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 09:{minute:02d}:00", close=10.0))
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:00:00", close=10.0))
        # Build 10:30 bucket
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:01:00", close=11.0))
        # A 1m bar belonging to the 10:00 bucket is now expired
        with self.assertRaisesRegex(RuntimeMarketDataError, "expired 30m bucket"):
            self.agg.update_one_minute(_1m_bar("2026-09-01 09:45:00", close=99.0))

    def test_official_must_be_on_boundary(self) -> None:
        with self.assertRaisesRegex(RuntimeMarketDataError, "thirty-minute close boundary"):
            self.agg.accept_official(_official_30m("2026-09-01 10:15:00", close=10.5))

    def test_lunch_break_not_merged(self) -> None:
        # Build 10:00 bucket (09:31–10:00) and accept official
        for minute in range(31, 60):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 09:{minute:02d}:00", close=10.0))
        self.agg.update_one_minute(_1m_bar("2026-09-01 10:00:00", close=10.0))
        self.agg.accept_official(_official_30m("2026-09-01 10:00:00", close=10.0))
        # Build 11:30 bucket (11:01–11:30) and accept official
        for minute in range(1, 31):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 11:{minute:02d}:00", close=10.0))
        self.agg.accept_official(_official_30m("2026-09-01 11:30:00", close=10.0))
        # 13:01 → 13:30 bucket (afternoon, not merged with 11:30)
        self.agg.update_one_minute(_1m_bar("2026-09-01 13:01:00", close=11.0))

        display = self.agg.display_bars
        timestamps = [b["timestamp"] for b in display]
        self.assertIn("2026-09-01 11:30:00", timestamps)
        self.assertIn("2026-09-01 13:30:00", timestamps)
        # 11:30 and 13:30 must be separate bars, not merged
        self.assertEqual(len(timestamps), 3)
        self.assertNotEqual(timestamps[-1], timestamps[-2])

    def test_nullable_volume_propagation(self) -> None:
        # If any 1m volume is None, the 30m volume must be None
        for minute in range(31, 60):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 09:{minute:02d}:00", close=10.0))
        self.agg.update_one_minute({
            "timestamp": "2026-09-01 10:00:00",
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
            "volume": None, "amount": 1000.0, "closed": True,
        })
        display = self.agg.display_bars
        self.assertIsNone(display[0]["volume"])

    def test_nullable_amount_propagation(self) -> None:
        for minute in range(31, 60):
            self.agg.update_one_minute(_1m_bar(f"2026-09-01 09:{minute:02d}:00", close=10.0))
        self.agg.update_one_minute({
            "timestamp": "2026-09-01 10:00:00",
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
            "volume": 100, "amount": None, "closed": True,
        })
        display = self.agg.display_bars
        self.assertIsNone(display[0]["amount"])


if __name__ == "__main__":
    unittest.main()
