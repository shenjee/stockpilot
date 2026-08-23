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
