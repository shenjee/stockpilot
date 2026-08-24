"""Tests for read-time market review metrics."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from packages.marketreview.computed import compute_review_metrics
from packages.marketreview.schema import DailyMarketReviewAtoms, LadderStockRecord

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_2026_08_21.json"


class TestComputedMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_golden_fixture_metrics(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date=self.fixture["trade_date"],
            ladder_status="complete",
            **self.fixture["atoms"],
        )
        ladder_stocks = [
            LadderStockRecord(
                trade_date=self.fixture["trade_date"],
                market=stock["market"],
                code=stock["code"],
                name=stock["name"],
                streak_height=stock["streak_height"],
            )
            for stock in self.fixture["ladder_stocks"]
        ]
        computed = compute_review_metrics(
            atoms,
            ladder_stocks,
            previous_effective_limit_up=self.fixture["previous_atoms"]["effective_limit_up"],
        )
        expected = self.fixture["expected_computed"]
        for key, value in expected.items():
            with self.subTest(metric=key):
                self.assertEqual(computed[key], value)

    def test_index_change_pct_uses_raw_ratio_not_rounded_points(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date="2026-08-21",
            sh_index_close=100.44,
            sh_index_prev_close=100.0,
        )
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=None)
        self.assertEqual(computed["sh_index_change_points"], 0.44)
        self.assertEqual(computed["sh_index_change_pct"], 0.0044)

    def test_index_change_negative_pct(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date="2026-08-21",
            sz_index_close=99.25,
            sz_index_prev_close=100.0,
        )
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=None)
        self.assertEqual(computed["sz_index_change_points"], -0.75)
        self.assertEqual(computed["sz_index_change_pct"], -0.0075)

    def test_missing_ladder_status_leaves_streak_empty(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date=self.fixture["trade_date"],
            ladder_status="missing",
            effective_limit_up=52,
        )
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=40)
        self.assertIsNone(computed["streak_count"])
        self.assertIsNone(computed["streak_rate"])
        self.assertIsNone(computed["highest_board"])

    def test_complete_zero_ladder(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date=self.fixture["trade_date"],
            ladder_status="complete",
            effective_limit_up=52,
        )
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=40)
        self.assertEqual(computed["streak_count"], 0)
        self.assertEqual(computed["highest_board"], 0)
        self.assertEqual(computed["streak_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
