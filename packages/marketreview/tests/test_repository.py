"""Repository integration tests for daily market review."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from packages.marketreview.errors import (
    LadderSnapshotRequiredError,
    LadderStNotAllowedError,
)
from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.schema import (
    LadderItemPatch,
    LadderResetMissing,
    LadderSnapshotReplace,
    LadderStockInput,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_2026_08_21.json"


class TestMarketReviewRepository(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.repo = MarketReviewRepository(self.conn)

    def tearDown(self) -> None:
        self.repo.close()

    def _seed_previous_day(self) -> None:
        self.repo.patch_review(
            self.fixture["previous_trade_date"],
            fields=self.fixture["previous_atoms"],
        )

    def _ladder_inputs(self) -> list[LadderStockInput]:
        return [
            LadderStockInput(
                market=stock["market"],
                code=stock["code"],
                name=stock["name"],
                streak_height=stock["streak_height"],
                is_st=stock["is_st"],
            )
            for stock in self.fixture["ladder_stocks"]
        ]

    def test_golden_fixture_end_to_end(self) -> None:
        self._seed_previous_day()
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields=self.fixture["atoms"],
            ladder_operation=LadderSnapshotReplace(stocks=self._ladder_inputs()),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.ladder_status, "complete")
        for key, value in self.fixture["expected_computed"].items():
            with self.subTest(metric=key):
                self.assertEqual(view.computed[key], value)

    def test_st_record_rolls_back_atomic_patch(self) -> None:
        self._seed_previous_day()
        with self.assertRaises(LadderStNotAllowedError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput(
                            market="sh",
                            code="600519",
                            name="ST测试",
                            streak_height=2,
                            is_st=True,
                        )
                    ]
                ),
            )
        self.assertIn("sh.600519", ctx.exception.problem_codes)
        view = self.repo.get_review(self.fixture["trade_date"])
        self.assertIsNone(view)

    def test_item_patch_requires_complete_snapshot(self) -> None:
        with self.assertRaises(LadderSnapshotRequiredError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderItemPatch(
                    upserts=[
                        LadderStockInput(
                            market="sh",
                            code="600519",
                            name="贵州茅台",
                            streak_height=2,
                            is_st=False,
                        )
                    ]
                ),
            )

    def test_snapshot_replace_then_item_patch_and_reset(self) -> None:
        self._seed_previous_day()
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields=self.fixture["atoms"],
            ladder_operation=LadderSnapshotReplace(stocks=self._ladder_inputs()),
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            ladder_operation=LadderItemPatch(
                deletes=[("sh", "600036")],
            ),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.computed["streak_count"], 3)
        self.repo.patch_review(
            self.fixture["trade_date"],
            ladder_operation=LadderResetMissing(),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.ladder_status, "missing")
        self.assertIsNone(view.computed["streak_count"])
        self.assertEqual(view.ladder_stocks, [])

    def test_field_patch_preserves_unmentioned_fields(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"closed_limit_down": 5},
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)
        self.assertEqual(view.atoms.closed_limit_down, 5)

    def test_delete_review_cascades(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields=self.fixture["atoms"],
            ladder_operation=LadderSnapshotReplace(stocks=self._ladder_inputs()),
        )
        self.assertTrue(self.repo.delete_review(self.fixture["trade_date"]))
        self.assertIsNone(self.repo.get_review(self.fixture["trade_date"]))


if __name__ == "__main__":
    unittest.main()
