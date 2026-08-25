"""Repository integration tests for daily market review."""

from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from packages.marketreview.errors import (
    ForeignKeysUnavailableError,
    InvalidFieldValueError,
    InvalidTradeDateError,
    LadderStNotAllowedError,
)
from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.schema import (
    LadderItemPatch,
    LadderSnapshotReplace,
    LadderStockInput,
)
from packages.marketreview.sqlite_schema import (
    LEGACY_LADDER_DDL,
    LEGACY_PROVENANCE_DDL,
    _ddl_daily_ladder_stock,
    _ddl_daily_market_review,
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
        self.conn.close()

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

    def test_st_record_rolls_back_existing_review_patch(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        with self.assertRaises(LadderStNotAllowedError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderItemPatch(
                    upserts=[
                        LadderStockInput("sh", "600519", "ST测试", 2, True),
                    ]
                ),
            )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)
        self.assertEqual(view.ladder_stocks, [])

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

    def test_st_records_reported_before_other_invalid_ladder_input(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        with self.assertRaises(LadderStNotAllowedError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", "坏高度", 1, False),
                        LadderStockInput("sz", "000001", "ST测试", 2, True),
                    ]
                ),
            )
        self.assertEqual(ctx.exception.problem_codes, ("sz.000001",))
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)

    def test_st_invalid_code_still_reported_as_st(self) -> None:
        with self.assertRaises(LadderStNotAllowedError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "ST1", "ST测试", 2, True),
                    ]
                ),
            )
        self.assertEqual(ctx.exception.problem_codes, ("sh.ST1",))
        self.assertIsNone(self.repo.get_review(self.fixture["trade_date"]))

    def test_duplicate_snapshot_identity_rolls_back(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        with self.assertRaises(InvalidFieldValueError) as ctx:
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", "贵州茅台", 2, False),
                        LadderStockInput("sh", "600519", "茅台", 3, False),
                    ]
                ),
            )
        self.assertIn("重复股票", str(ctx.exception))
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)
        self.assertEqual(view.ladder_stocks, [])

    def test_invalid_ladder_height_rolls_back_field_patch(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", "600519", "测试", 1, False),
                    ]
                ),
            )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)
        self.assertEqual(view.ladder_stocks, [])

    def test_invalid_ladder_market_rolls_back_field_patch(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 10},
        )
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                fields={"effective_limit_up": 99},
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("hk", "600519", "测试", 2, False),  # type: ignore[arg-type]
                    ]
                ),
            )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.atoms.effective_limit_up, 10)
        self.assertEqual(view.ladder_stocks, [])

    def test_empty_snapshot_replace_is_zero_ladder(self) -> None:
        self._seed_previous_day()
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 52},
            ladder_operation=LadderSnapshotReplace(stocks=self._ladder_inputs()),
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            ladder_operation=LadderSnapshotReplace(stocks=[]),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.ladder_stocks, [])
        self.assertEqual(view.computed["streak_count"], 0)
        self.assertEqual(view.computed["highest_board"], 0)
        self.assertEqual(view.computed["highest_board_representatives"], [])
        self.assertEqual(view.computed["board_2"], 0)
        self.assertEqual(view.computed["board_11_plus"], 0)
        self.assertEqual(view.computed["streak_rate"], 0.0)

    def test_new_review_without_ladder_input_aggregates_to_zero(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"effective_limit_up": 52},
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.computed["streak_count"], 0)
        self.assertEqual(view.computed["highest_board"], 0)
        self.assertEqual(view.computed["highest_board_representatives"], [])

    def test_item_patch_without_prior_snapshot(self) -> None:
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
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.computed["streak_count"], 1)
        self.assertEqual(view.ladder_stocks[0].name, "贵州茅台")
        self.assertEqual(view.ladder_stocks[0].streak_height, 2)

        self.repo.patch_review(
            self.fixture["trade_date"],
            ladder_operation=LadderItemPatch(
                upserts=[
                    LadderStockInput(
                        market="sh",
                        code="600519",
                        name="茅台",
                        streak_height=3,
                        is_st=False,
                    )
                ]
            ),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.computed["streak_count"], 1)
        self.assertEqual(view.ladder_stocks[0].name, "茅台")
        self.assertEqual(view.ladder_stocks[0].streak_height, 3)
        self.assertEqual(view.computed["highest_board"], 3)

        self.repo.patch_review(
            self.fixture["trade_date"],
            ladder_operation=LadderItemPatch(deletes=[("sh", "600519")]),
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertEqual(view.ladder_stocks, [])
        self.assertEqual(view.computed["streak_count"], 0)
        self.assertEqual(view.computed["highest_board"], 0)

    def test_snapshot_replace_then_item_patch(self) -> None:
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

    def test_explicit_null_clears_field(self) -> None:
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": 17.0},
        )
        self.repo.patch_review(
            self.fixture["trade_date"],
            fields={"pe_sh": None},
        )
        view = self.repo.get_review(self.fixture["trade_date"])
        assert view is not None
        self.assertIsNone(view.atoms.pe_sh)

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

    def test_rejects_non_string_ladder_identity(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.patch_review(
                self.fixture["trade_date"],
                ladder_operation=LadderSnapshotReplace(
                    stocks=[
                        LadderStockInput("sh", 600519, "测试", 2, False),  # type: ignore[arg-type]
                    ]
                ),
            )
        with self.assertRaises(InvalidFieldValueError):
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
        conn.close()

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
        conn.close()

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
        self.assertEqual(ladder_count, 0)

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


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _assert_ladder_delete_cascade(test: unittest.TestCase, conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA foreign_key_list(daily_ladder_stock)").fetchall()
    matches = [
        row
        for row in rows
        if row[2] == "daily_market_review"
        and row[3] == "trade_date"
        and row[4] == "trade_date"
        and str(row[6]).upper() == "CASCADE"
    ]
    test.assertTrue(matches, f"expected trade_date ON DELETE CASCADE, got {list(rows)}")


class TestSqliteSchemaConstraints(unittest.TestCase):
    def test_init_db_creates_only_review_and_ladder_tables(self) -> None:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        self.assertEqual(_user_tables(conn), {"daily_market_review", "daily_ladder_stock"})
        conn.close()

    def test_foreign_keys_enabled_and_cascade(self) -> None:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        self.assertEqual(row[0], 1)
        _assert_ladder_delete_cascade(self, conn)
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, created_at, updated_at
            ) VALUES ('2026-08-21', 't', 't')
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
        conn.close()

    def test_legacy_schema_drops_provenance_and_ladder_status(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute(
            """
            CREATE TABLE daily_market_review (
                trade_date TEXT NOT NULL PRIMARY KEY,
                ladder_status TEXT NOT NULL DEFAULT 'missing',
                effective_limit_up INTEGER,
                pe_sh REAL,
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
                trade_date, ladder_status, effective_limit_up, pe_sh, created_at, updated_at
            ) VALUES ('2026-08-21', 'missing', 52, 17.0, 't', 't')
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

        provenance_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'market_review_metric_provenance'"
        ).fetchall()
        self.assertEqual(provenance_tables, [])
        review_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_market_review)").fetchall()}
        self.assertNotIn("ladder_status", review_columns)
        review = conn.execute(
            "SELECT effective_limit_up, pe_sh FROM daily_market_review WHERE trade_date = '2026-08-21'"
        ).fetchone()
        self.assertEqual(review["effective_limit_up"], 52)
        self.assertEqual(review["pe_sh"], 17.0)
        ladder = conn.execute(
            "SELECT code, streak_height FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchall()
        self.assertEqual(len(ladder), 1)
        self.assertEqual(ladder[0]["code"], "600519")
        _assert_ladder_delete_cascade(self, conn)

        migrate_legacy_schema(conn)
        conn.commit()
        review_again = conn.execute(
            "SELECT effective_limit_up FROM daily_market_review WHERE trade_date = '2026-08-21'"
        ).fetchone()
        self.assertEqual(review_again["effective_limit_up"], 52)

        repo = MarketReviewRepository(conn)
        view = repo.get_review("2026-08-21")
        assert view is not None
        self.assertEqual(view.computed["streak_count"], 1)
        self.assertEqual(view.computed["highest_board"], 2)
        conn.execute("DELETE FROM daily_market_review WHERE trade_date = '2026-08-21'")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)
        leftover = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('daily_market_review_new', 'daily_ladder_stock_new')"
        ).fetchall()
        self.assertEqual(leftover, [])
        conn.close()

    def test_legacy_no_action_fk_is_rebuilt_to_cascade(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute(
            """
            CREATE TABLE daily_market_review (
                trade_date TEXT NOT NULL PRIMARY KEY,
                effective_limit_up INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE daily_ladder_stock (
                trade_date TEXT NOT NULL,
                market TEXT NOT NULL CHECK (market IN ('sh', 'sz', 'bj')),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                streak_height INTEGER NOT NULL CHECK (streak_height >= 2),
                is_st INTEGER NOT NULL CHECK (is_st = 0),
                PRIMARY KEY (trade_date, market, code),
                FOREIGN KEY (trade_date) REFERENCES daily_market_review(trade_date)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, effective_limit_up, created_at, updated_at
            ) VALUES ('2026-08-21', 52, 't', 't')
            """
        )
        conn.execute(
            """
            INSERT INTO daily_ladder_stock (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES ('2026-08-21', 'sh', '600519', '贵州茅台', 2, 0)
            """
        )
        conn.commit()
        fk_before = conn.execute("PRAGMA foreign_key_list(daily_ladder_stock)").fetchone()
        self.assertEqual(str(fk_before[6]).upper(), "NO ACTION")

        init_db(conn)
        _assert_ladder_delete_cascade(self, conn)
        ladder = conn.execute(
            "SELECT code FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchall()
        self.assertEqual(len(ladder), 1)
        conn.execute("DELETE FROM daily_market_review WHERE trade_date = '2026-08-21'")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)
        conn.close()

    def test_interrupted_rebuild_keeps_ladder_new_rows(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute(
            """
            CREATE TABLE daily_market_review (
                trade_date TEXT NOT NULL PRIMARY KEY,
                ladder_status TEXT NOT NULL DEFAULT 'missing',
                effective_limit_up INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, ladder_status, effective_limit_up, created_at, updated_at
            ) VALUES ('2026-08-21', 'missing', 52, 't', 't')
            """
        )
        conn.execute(_ddl_daily_market_review("daily_market_review_new"))
        conn.execute(
            """
            INSERT INTO daily_market_review_new (
                trade_date, effective_limit_up, created_at, updated_at
            ) VALUES ('2026-08-21', 52, 't', 't')
            """
        )
        conn.execute(
            _ddl_daily_ladder_stock(
                "daily_ladder_stock_new",
                parent_table="daily_market_review_new",
            )
        )
        conn.execute(
            """
            INSERT INTO daily_ladder_stock_new (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES ('2026-08-21', 'sh', '600519', '贵州茅台', 2, 0)
            """
        )
        conn.commit()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM daily_ladder_stock_new").fetchone()[0],
            1,
        )

        init_db(conn)
        self.assertEqual(_user_tables(conn), {"daily_market_review", "daily_ladder_stock"})
        _assert_ladder_delete_cascade(self, conn)
        review = conn.execute(
            "SELECT effective_limit_up FROM daily_market_review WHERE trade_date = '2026-08-21'"
        ).fetchone()
        self.assertEqual(review["effective_limit_up"], 52)
        review_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_market_review)").fetchall()}
        self.assertNotIn("ladder_status", review_columns)
        ladder = conn.execute(
            "SELECT code, streak_height FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchall()
        self.assertEqual(len(ladder), 1)
        self.assertEqual(ladder[0]["code"], "600519")
        conn.execute("DELETE FROM daily_market_review WHERE trade_date = '2026-08-21'")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM daily_ladder_stock WHERE trade_date = '2026-08-21'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)
        conn.close()

    def test_legacy_rebuild_keeps_old_data_if_copy_fails(self) -> None:
        conn = configure_connection(sqlite3.connect(":memory:"))
        conn.execute(
            """
            CREATE TABLE daily_market_review (
                trade_date TEXT NOT NULL PRIMARY KEY,
                ladder_status TEXT NOT NULL DEFAULT 'missing',
                effective_limit_up INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(LEGACY_LADDER_DDL)
        conn.execute(
            """
            INSERT INTO daily_market_review (
                trade_date, ladder_status, effective_limit_up, created_at, updated_at
            ) VALUES ('2026-08-21', 'missing', 52, 't', 't')
            """
        )
        conn.execute(
            """
            INSERT INTO daily_ladder_stock (
                trade_date, market, code, name, streak_height, is_st
            ) VALUES ('2026-08-21', 'sh', '600519', '贵州茅台', 2, 0)
            """
        )
        conn.commit()
        with patch(
            "packages.marketreview.sqlite_schema._copy_matching_columns",
            side_effect=RuntimeError("copy failed"),
        ):
            with self.assertRaises(RuntimeError):
                migrate_legacy_schema(conn)
        review = conn.execute(
            "SELECT effective_limit_up, ladder_status FROM daily_market_review WHERE trade_date = '2026-08-21'"
        ).fetchone()
        self.assertEqual(review["effective_limit_up"], 52)
        self.assertEqual(review["ladder_status"], "missing")
        ladder_count = conn.execute("SELECT COUNT(*) FROM daily_ladder_stock").fetchone()[0]
        self.assertEqual(ladder_count, 1)
        leftover = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('daily_market_review_new', 'daily_ladder_stock_new')"
        ).fetchall()
        self.assertEqual(leftover, [])
        conn.close()

    def test_init_db_is_idempotent_on_current_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.execute(
            """
            INSERT INTO daily_market_review (trade_date, effective_limit_up, created_at, updated_at)
            VALUES ('2026-08-21', 52, 't', 't')
            """
        )
        init_db(conn)
        row = conn.execute(
            "SELECT effective_limit_up FROM daily_market_review WHERE trade_date = '2026-08-21'"
        ).fetchone()
        self.assertEqual(row["effective_limit_up"], 52)
        columns = {item[1] for item in conn.execute("PRAGMA table_info(daily_market_review)").fetchall()}
        self.assertNotIn("ladder_status", columns)
        conn.close()


if __name__ == "__main__":
    unittest.main()
