"""Repository integration tests for daily market review."""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from packages.marketreview.errors import (
    ForeignKeysUnavailableError,
    InvalidFieldValueError,
    InvalidTradeDateError,
    LadderInvalidTransitionError,
    LadderSnapshotRequiredError,
    LadderStNotAllowedError,
)
from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.schema import (
    LadderItemPatch,
    LadderResetMissing,
    LadderSnapshotReplace,
    LadderStockInput,
    MetricProvenance,
)
from packages.marketreview.sqlite_schema import (
    LEGACY_LADDER_DDL,
    LEGACY_PROVENANCE_DDL,
    configure_connection,
    init_db,
    migrate_legacy_schema,
)
from packages.marketreview.validation import resolve_trade_date

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_2026_08_21.json"
_CHINA = ZoneInfo("Asia/Shanghai")


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
        for key, value in self.fixture["atoms"].items():
            with self.subTest(atom=key):
                self.assertEqual(getattr(view.atoms, key), value)
        for key, value in self.fixture["expected_computed"].items():
            with self.subTest(metric=key):
                self.assertEqual(view.computed[key], value)
        self.assertEqual(len(view.ladder_stocks), len(self.fixture["ladder_stocks"]))

    def test_rejects_non_trading_day(self) -> None:
        with self.assertRaises(InvalidTradeDateError) as ctx:
            self.repo.patch_review("2026-08-22", fields={"effective_limit_up": 1})
        self.assertIn("2026-08-22", str(ctx.exception))
        self.assertIsNone(self.repo.get_review("2026-08-22"))

    def test_rejects_invalid_date_format(self) -> None:
        with self.assertRaises(InvalidTradeDateError):
            self.repo.patch_review("2026/08/21", fields={"effective_limit_up": 1})

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

    def test_multiple_st_records_reported_together(self) -> None:
        self._seed_previous_day()
        with self.assertRaises(LadderStNotAllowedError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", "A", 2, True),
                        LadderStockInput("sz", "000001", "B", 3, True),
                    ]
                ),
            )
        self.assertEqual(ctx.exception.problem_codes, ("sh.600519", "sz.000001"))

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
        self.assertNotIn("ladder_snapshot", view.provenance)

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

    def test_explicit_null_clears_field_and_provenance(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": 17.0},
            provenance={
                "pe_sh": MetricProvenance(
                    source="manual",
                    source_as_of=self.fixture["trade_date"],
                    retrieved_at="2026-08-21T08:00:00+00:00",
                    acquisition_mode="manual",
                )
            },
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": None},
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertIsNone(view.atoms.pe_sh)
        self.assertNotIn("pe_sh", view.provenance)

    def test_field_update_without_provenance_clears_stale_lineage(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": 17.0},
            provenance={
                "pe_sh": MetricProvenance(
                    source="manual",
                    source_as_of=self.fixture["trade_date"],
                    retrieved_at="2026-08-21T08:00:00+00:00",
                    acquisition_mode="manual",
                )
            },
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": 18.0},
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.pe_sh, 18.0)
        self.assertNotIn("pe_sh", view.provenance)

    def test_rejects_today_before_close_with_injected_now(self) -> None:
        with self.assertRaises(InvalidTradeDateError) as ctx:
            self.repo.patch_review(
                "2026-08-24",
                fields={"effective_limit_up": 1},
                now=datetime(2026, 8, 24, 10, 0, tzinfo=_CHINA),
            )
        self.assertIn("尚未收盘", str(ctx.exception))

    def test_rejects_future_date_with_distinct_message(self) -> None:
        with self.assertRaises(InvalidTradeDateError) as ctx:
            resolve_trade_date(
                self.repo._calendar,
                requested="2027-01-04",
                now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
            )
        self.assertIn("未来日期", str(ctx.exception))

    def test_rejects_weekend_before_close_as_non_trading_day(self) -> None:
        with self.assertRaises(InvalidTradeDateError) as ctx:
            resolve_trade_date(
                self.repo._calendar,
                requested="2026-08-23",
                now=datetime(2026, 8, 23, 10, 0, tzinfo=_CHINA),
            )
        self.assertIn("不是交易日", str(ctx.exception))
        self.assertNotIn("尚未收盘", str(ctx.exception))

    def test_null_field_with_provenance_rejected(self) -> None:
        with self.assertRaises(InvalidFieldValueError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"pe_sh": None},
                provenance={
                    "pe_sh": MetricProvenance(
                        source="manual",
                        source_as_of=self.fixture["trade_date"],
                        retrieved_at="2026-08-21T08:00:00+00:00",
                        acquisition_mode="manual",
                    )
                },
            )
        self.assertIn("清空字段不得提交血缘", str(ctx.exception))

    def test_rejects_non_string_ladder_identity(self) -> None:
        with self.assertRaises(LadderInvalidTransitionError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", 600519, "测试", 2, False),  # type: ignore[arg-type]
                    ]
                ),
            )
        with self.assertRaises(LadderInvalidTransitionError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput(1, "600519", "测试", 2, False),  # type: ignore[arg-type]
                    ]
                ),
            )
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", None, 2, False),  # type: ignore[arg-type]
                    ]
                ),
            )

    def test_rejects_repository_on_external_transaction_without_foreign_keys(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        with self.assertRaises(ForeignKeysUnavailableError):
            MarketReviewRepository(conn)
        conn.rollback()

    def test_provenance_without_field_patch_rejected(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                provenance={
                    "pe_sh": MetricProvenance(
                        source="manual",
                        source_as_of=self.fixture["trade_date"],
                        retrieved_at="2026-08-21T08:00:00+00:00",
                        acquisition_mode="manual",
                    )
                },
            )

    def test_rejects_invalid_runtime_types(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": True},
            )
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"margin_balance_sh": "950000000000"},
            )
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", "测试", 2, None),  # type: ignore[arg-type]
                    ]
                ),
            )

    def test_nested_transaction_does_not_rollback_outer_work(self) -> None:
        self.conn.execute("CREATE TABLE outer_data (value INTEGER)")
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO outer_data VALUES (1)")
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": True},
            )
        row = self.conn.execute("SELECT value FROM outer_data").fetchone()
        self.assertEqual(row[0], 1)

    def test_init_db_does_not_commit_outer_transaction(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute("CREATE TABLE outer_data (value INTEGER)")
        conn.execute("INSERT INTO outer_data VALUES (7)")
        conn.commit()
        conn.execute("BEGIN")
        init_db(conn)
        row = conn.execute("SELECT value FROM outer_data").fetchone()
        self.assertEqual(row[0], 7)
        conn.rollback()
        row = conn.execute("SELECT value FROM outer_data").fetchone()
        self.assertEqual(row[0], 7)

    def test_delete_review_cascades(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields=self.fixture["atoms"],
            ladder_operation=LadderSnapshotReplace(stocks=self._ladder_inputs()),
        )
        self.assertTrue(self.repo.delete_review(self.fixture["trade_date"]))
        self.assertIsNone(self.repo.get_review(self.fixture["trade_date"]))
        ladder_count = self.conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = ?",
            (self.fixture["trade_date"],),
        ).fetchone()[0]
        provenance_count = self.conn.execute(
            "SELECT COUNT(*) FROM market_review_metric_provenance WHERE trade_date = ?",
            (self.fixture["trade_date"],),
        ).fetchone()[0]
        self.assertEqual(ladder_count, 0)
        self.assertEqual(provenance_count, 0)

    def test_list_reviews_returns_range_views(self) -> None:
        self._seed_previous_day()
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 52},
        )
        views = self.repo.list_reviews(
            self.fixture["previous_trade_date"],
            self.fixture["trade_date"],
        )
        self.assertEqual(len(views), 2)
        self.assertEqual(views[0].atoms.trade_date, self.fixture["previous_trade_date"])
        self.assertEqual(views[1].atoms.trade_date, self.fixture["trade_date"])


class TestSqliteSchemaConstraints(unittest.TestCase):
    def test_foreign_keys_enabled_and_cascade(self) -> None:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        self.assertEqual(row[0], 1)
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, ladder_status, created_at, updated_at
            ) VALUES ('2026-08-21', 'missing', 't', 't')
            """
        )
        conn.execute(
            """
            INSERT INTO daily_ladder_stock (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES ('2026-08-21', 'sh', '600519', '贵州茅台', 2, 0)
            """
        )
        conn.execute("DELETE FROM daily_market_review WHERE trade_date = '2026-08-21'")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_legacy_schema_migrates_to_foreign_keys(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute(
            """
            CREATE TABLE daily_market_review (
                trade_date TEXT NOT NULL PRIMARY KEY,
                ladder_status TEXT NOT NULL DEFAULT 'missing',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(LEGACY_LADDER_DDL)
        conn.execute(LEGACY_PROVENANCE_DDL)
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, ladder_status, created_at, updated_at
            ) VALUES ('2026-08-21', 'missing', 't', 't')
            """
        )
        conn.execute(
            """
            INSERT INTO daily_ladder_stock (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES ('2026-08-21', 'sh', '600519', '贵州茅台', 2, 0)
            """
        )
        conn.execute(
            """
            INSERT INTO market_review_metric_provenance (
                trade_date, metric_name, source, source_as_of, retrieved_at, acquisition_mode
            ) VALUES ('2026-08-21', 'pe_sh', 'manual', '2026-08-21', 't', 'manual')
            """
        )
        migrate_legacy_schema(conn)
        conn.commit()
        fk_rows = conn.execute("PRAGMA foreign_key_list(daily_ladder_stock)").fetchall()
        self.assertTrue(fk_rows)
        conn.execute("DELETE FROM daily_market_review WHERE trade_date = '2026-08-21'")
        ladder_count = conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        provenance_count = conn.execute(
            "SELECT COUNT(*) FROM market_review_metric_provenance WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        self.assertEqual(ladder_count, 0)
        self.assertEqual(provenance_count, 0)


if __name__ == "__main__":
    unittest.main()
