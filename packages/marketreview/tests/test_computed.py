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

    def test_empty_ladder_aggregates_to_zero(self) -> None:
        atoms = DailyMarketReviewAtoms(
            trade_date=self.fixture["trade_date"],
            effective_limit_up=52,
        )
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=40)
        self.assertEqual(computed["streak_count"], 0)
        self.assertEqual(computed["highest_board"], 0)
        self.assertEqual(computed["highest_board_representatives"], [])
        self.assertEqual(computed["streak_rate"], 0.0)
        for height in range(2, 11):
            self.assertEqual(computed[f"board_{height}"], 0)
        self.assertEqual(computed["board_11_plus"], 0)
        self.assertEqual(computed["board_counts"], {})

    def test_highest_board_representatives_includes_all_tied_stocks(self) -> None:
        atoms = DailyMarketReviewAtoms(trade_date="2026-08-21")
        ladder_stocks = [
            LadderStockRecord("2026-08-21", "sh", "600000", "浦发银行", 5),
            LadderStockRecord("2026-08-21", "sz", "000001", "平安银行", 3),
            LadderStockRecord("2026-08-21", "sz", "000002", "万科A", 5),
        ]
        computed = compute_review_metrics(
            atoms,
            ladder_stocks,
            previous_effective_limit_up=10,
        )
        self.assertEqual(computed["highest_board"], 5)
        representatives = computed["highest_board_representatives"]
        self.assertEqual(len(representatives), 2)
        self.assertEqual(
            {(item["market"], item["code"], item["name"], item["streak_height"]) for item in representatives},
            {("sh", "600000", "浦发银行", 5), ("sz", "000002", "万科A", 5)},
        )

    def test_streak_rate_empty_when_previous_denominator_missing(self) -> None:
        atoms = DailyMarketReviewAtoms(trade_date=self.fixture["trade_date"])
        computed = compute_review_metrics(atoms, [], previous_effective_limit_up=None)
        self.assertEqual(computed["streak_count"], 0)
        self.assertIsNone(computed["streak_rate"])
        computed_zero_denom = compute_review_metrics(atoms, [], previous_effective_limit_up=0)
        self.assertIsNone(computed_zero_denom["streak_rate"])


if __name__ == "__main__":
    unittest.main()
