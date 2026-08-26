"""Repository tests for daily market review persistence."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from datetime import date
from pathlib import Path
from unittest.mock import patch

from packages.marketreview.errors import InvalidFieldValueError
from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.schema import PriceLimitEventInput, PriceLimitEventRecord
from packages.marketreview.sqlite_schema import init_db

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_2026_08_21.json"


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


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

    def _event_inputs(self) -> list[PriceLimitEventInput]:
        return [PriceLimitEventInput(**event) for event in self.fixture["price_limit_events"]]

    def test_golden_fixture_round_trip(self) -> None:
        self.repo.save_review(self.fixture["trade_date"], fields=self.fixture["atoms"])
        self.repo.save_price_limit_events(self.fixture["trade_date"], self._event_inputs())

        review = self.repo.get_review(self.fixture["trade_date"])
        assert review is not None
        for key, value in self.fixture["atoms"].items():
            with self.subTest(field=key):
                self.assertEqual(getattr(review, key), value)

        events = self.repo.get_price_limit_events(self.fixture["trade_date"])
        self.assertEqual(len(events), len(self.fixture["price_limit_events"]))
        actual = {
            (item.market, item.code, item.direction): item
            for item in events
        }
        for event in self.fixture["price_limit_events"]:
            saved = actual[(event["market"], event["code"], event["direction"])]
            self.assertEqual(asdict(saved), {"trade_date": self.fixture["trade_date"], **event})

    def test_save_review_rejects_bool_for_integer_field(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_review("2026-08-21", fields={"advancing_count": True})
        self.assertIsNone(self.repo.get_review("2026-08-21"))

    def test_save_review_does_not_round_money_fields(self) -> None:
        self.repo.save_review("2026-08-21", fields={"avg_stock_price": 1.005})
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertEqual(review.avg_stock_price, 1.005)

    def test_get_review_ignores_unknown_table_columns(self) -> None:
        self.conn.execute("ALTER TABLE daily_market_review ADD COLUMN effective_limit_up INTEGER")
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        self.conn.execute(
            "UPDATE daily_market_review SET effective_limit_up = 52 WHERE trade_date = '2026-08-21'"
        )
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertEqual(review.pe_sh, 17.0)
        self.assertFalse(hasattr(review, "effective_limit_up"))

    def test_save_review_patches_provided_fields_only(self) -> None:
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0, "advancing_count": 10})
        self.repo.save_review("2026-08-21", fields={"pe_sh": 18.5})
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertEqual(review.pe_sh, 18.5)
        self.assertEqual(review.advancing_count, 10)
        self.assertIsNone(review.pe_sz)

    def test_save_review_explicit_null_clears_field(self) -> None:
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        self.repo.save_review("2026-08-21", fields={"pe_sh": None})
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertIsNone(review.pe_sh)

    def test_empty_save_review_is_noop(self) -> None:
        self.repo.save_review("2026-08-21", fields={})
        self.assertIsNone(self.repo.get_review("2026-08-21"))

    def test_weekend_date_is_stored(self) -> None:
        self.repo.save_review("2026-08-22", fields={"pe_sh": 1.0})
        review = self.repo.get_review("2026-08-22")
        assert review is not None
        self.assertEqual(review.pe_sh, 1.0)

    def test_date_object_is_accepted(self) -> None:
        self.repo.save_review(date(2026, 8, 21), fields={"pe_sh": 2.0})
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertEqual(review.pe_sh, 2.0)

    def test_invalid_trade_date_format_is_rejected(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_review("2026/08/21", fields={"pe_sh": 1.0})
        self.assertIsNone(self.repo.get_review("2026-08-21"))

    def test_unknown_review_field_rolls_back(self) -> None:
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_review("2026-08-21", fields={"pe_sh": 1.0, "effective_limit_up": 52})
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertEqual(review.pe_sh, 17.0)

    def test_get_review_does_not_return_events(self) -> None:
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs())
        review = self.repo.get_review("2026-08-21")
        assert review is not None
        self.assertFalse(hasattr(review, "price_limit_events"))
        self.assertEqual(len(self.repo.get_price_limit_events("2026-08-21")), 5)

    def test_events_can_exist_without_review(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        self.assertIsNone(self.repo.get_review("2026-08-21"))
        self.assertEqual(len(self.repo.get_price_limit_events("2026-08-21")), 1)

    def test_delete_review_keeps_events(self) -> None:
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        self.repo.delete_review("2026-08-21")
        self.assertIsNone(self.repo.get_review("2026-08-21"))
        self.assertEqual(len(self.repo.get_price_limit_events("2026-08-21")), 1)

    def test_delete_missing_review_succeeds(self) -> None:
        self.repo.delete_review("2026-08-21")
        self.assertIsNone(self.repo.get_review("2026-08-21"))

    def test_empty_event_save_is_noop(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        self.repo.save_price_limit_events("2026-08-21", [])
        self.assertEqual(len(self.repo.get_price_limit_events("2026-08-21")), 1)

    def test_save_overwrites_same_identity(self) -> None:
        self.repo.save_price_limit_events(
            "2026-08-21",
            [PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 4)],
        )
        self.repo.save_price_limit_events(
            "2026-08-21",
            [PriceLimitEventInput("sh", "600519", "茅台", "up", True, 1000, 40)],
        )
        events = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "茅台")
        self.assertEqual(events[0].streak_height, 40)

    def test_get_then_save_records_round_trips(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        loaded = self.repo.get_price_limit_events("2026-08-21")
        self.repo.save_price_limit_events("2026-08-21", loaded)
        updated = PriceLimitEventRecord(
            trade_date=loaded[0].trade_date,
            market=loaded[0].market,
            code=loaded[0].code,
            name=loaded[0].name,
            direction=loaded[0].direction,
            closed_at_limit=loaded[0].closed_at_limit,
            limit_rate_bp=loaded[0].limit_rate_bp,
            streak_height=5,
        )
        self.repo.save_price_limit_events("2026-08-21", [updated])
        saved = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].streak_height, 5)
        self.assertEqual(saved[0].name, "贵州茅台")

    def test_get_then_save_mapping_with_trade_date_round_trips(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        payload = asdict(self.repo.get_price_limit_events("2026-08-21")[0])
        payload["streak_height"] = 6
        self.repo.save_price_limit_events("2026-08-21", [payload])
        saved = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(saved[0].streak_height, 6)

    def test_duplicate_identity_in_same_batch_is_rejected(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_price_limit_events(
                "2026-08-21",
                [
                    PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 4),
                    PriceLimitEventInput("sh", "600519", "茅台", "up", True, 1000, 40),
                ],
            )
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])

    def test_overwrite_preserves_created_at(self) -> None:
        with patch("packages.marketreview.repository.utc_now_iso", return_value="2026-08-21T00:00:00+00:00"):
            self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        with patch("packages.marketreview.repository.utc_now_iso", return_value="2026-08-21T01:00:00+00:00"):
            self.repo.save_price_limit_events(
                "2026-08-21",
                [PriceLimitEventInput("sh", "600519", "茅台", "up", True, 1000, 5)],
            )
        row = self.conn.execute(
            """
            SELECT created_at, updated_at, name, streak_height
            FROM daily_price_limit_event
            WHERE trade_date = '2026-08-21' AND code = '600519' AND direction = 'up'
            """
        ).fetchone()
        self.assertEqual(row[0], "2026-08-21T00:00:00+00:00")
        self.assertEqual(row[1], "2026-08-21T01:00:00+00:00")
        self.assertEqual(row[2], "茅台")
        self.assertEqual(row[3], 5)

    def test_same_stock_can_have_both_directions(self) -> None:
        self.repo.save_price_limit_events(
            "2026-08-21",
            [
                PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 2),
                PriceLimitEventInput("sh", "600519", "贵州茅台", "down", False, 1000, 0),
            ],
        )
        events = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(events), 2)

    def test_structural_types_are_stored_without_business_checks(self) -> None:
        self.repo.save_price_limit_events(
            "2026-08-21",
            [
                PriceLimitEventInput("SZ", "1", "示例", "UP", True, 1234, 40),
                PriceLimitEventInput("hk", "000001", "示例2", "down", False, 0, 0),
            ],
        )
        events = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(events[0].market, "SZ")
        self.assertEqual(events[0].limit_rate_bp, 1234)
        self.assertEqual(events[0].streak_height, 40)
        self.assertEqual(events[1].market, "hk")

    def test_rejects_non_bool_closed_at_limit(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_price_limit_events(
                "2026-08-21",
                [{"market": "sh", "code": "600519", "name": "茅台", "direction": "up",
                  "closed_at_limit": 1, "limit_rate_bp": 1000, "streak_height": 1}],
            )
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])

    def test_rejects_unknown_event_field(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_price_limit_events(
                "2026-08-21",
                [{"market": "sh", "code": "600519", "name": "茅台", "direction": "up",
                  "closed_at_limit": True, "limit_rate_bp": 1000, "streak_height": 1,
                  "is_st": False}],
            )
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])

    def test_invalid_event_in_batch_writes_nothing(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.save_price_limit_events(
                "2026-08-21",
                [
                    PriceLimitEventInput("sh", "600519", "茅台", "up", True, 1000, 1),
                    {"market": "sz", "code": "000001", "name": "平安", "direction": "up",
                     "closed_at_limit": True, "limit_rate_bp": 1000, "streak_height": True},
                ],
            )
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])

    def test_runtime_failure_rolls_back_partial_event_batch(self) -> None:
        original = self.repo._upsert_event
        calls = {"n": 0}

        def boom(record, *, now):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            original(record, now=now)

        self.repo._upsert_event = boom  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:2])
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])

    def test_delete_price_limit_events_clears_the_day(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs())
        self.repo.save_price_limit_events(
            "2026-08-20",
            [PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 3)],
        )
        self.repo.delete_price_limit_events("2026-08-21")
        self.assertEqual(self.repo.get_price_limit_events("2026-08-21"), [])
        self.assertEqual(len(self.repo.get_price_limit_events("2026-08-20")), 1)

    def test_delete_single_event_and_missing_identity_succeeds(self) -> None:
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:2])
        self.repo.delete_price_limit_event("2026-08-21", "sh", "600519", "up")
        remaining = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].code, "300001")
        self.repo.delete_price_limit_event("2026-08-21", "sh", "600519", "up")

    def test_replace_price_limit_event_direction_is_atomic(self) -> None:
        self.repo.save_price_limit_events(
            "2026-08-21",
            [PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 4)],
        )
        self.repo.replace_price_limit_event_direction(
            "2026-08-21",
            "sh",
            "600519",
            "up",
            PriceLimitEventInput("sh", "600519", "贵州茅台", "down", True, 1000, 0),
        )
        events = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].direction, "down")
        self.assertEqual(events[0].closed_at_limit, True)
        self.assertEqual(events[0].streak_height, 0)

    def test_replace_direction_rolls_back_when_save_fails(self) -> None:
        self.repo.save_price_limit_events(
            "2026-08-21",
            [PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 4)],
        )
        original = self.repo._upsert_event

        def boom(record, *, now):
            raise RuntimeError("boom")

        self.repo._upsert_event = boom  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.repo.replace_price_limit_event_direction(
                "2026-08-21",
                "sh",
                "600519",
                "up",
                PriceLimitEventInput("sh", "600519", "贵州茅台", "down", True, 1000, 0),
            )
        self.repo._upsert_event = original  # type: ignore[method-assign]
        events = self.repo.get_price_limit_events("2026-08-21")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].direction, "up")
        self.assertEqual(events[0].streak_height, 4)

    def test_replace_direction_rejects_same_direction(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.replace_price_limit_event_direction(
                "2026-08-21",
                "sh",
                "600519",
                "up",
                PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 4),
            )

    def test_replace_direction_rejects_market_code_mismatch(self) -> None:
        with self.assertRaises(InvalidFieldValueError):
            self.repo.replace_price_limit_event_direction(
                "2026-08-21",
                "sh",
                "600519",
                "up",
                PriceLimitEventInput("sz", "000001", "平安银行", "down", True, 1000, 0),
            )

    def test_repository_accepts_path_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market_review.sqlite3"
            with MarketReviewRepository(path) as repo:
                repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
                review = repo.get_review("2026-08-21")
                assert review is not None
                self.assertEqual(review.pe_sh, 17.0)

    def test_list_reviews_and_events_by_range(self) -> None:
        self.repo.save_review("2026-08-20", fields={"pe_sh": 16.0})
        self.repo.save_review("2026-08-21", fields={"pe_sh": 17.0})
        self.repo.save_price_limit_events(
            "2026-08-20",
            [PriceLimitEventInput("sh", "600519", "贵州茅台", "up", True, 1000, 3)],
        )
        self.repo.save_price_limit_events("2026-08-21", self._event_inputs()[:1])
        reviews = self.repo.list_reviews("2026-08-21", "2026-08-21")
        self.assertEqual([item.trade_date for item in reviews], ["2026-08-21"])
        events = self.repo.list_price_limit_events("2026-08-20", "2026-08-21")
        self.assertEqual([item.trade_date for item in events], ["2026-08-20", "2026-08-21"])


class TestSchemaInit(unittest.TestCase):
    def test_init_db_creates_review_and_event_tables(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            init_db(conn)
            self.assertEqual(_user_tables(conn), {"daily_market_review", "daily_price_limit_event"})
            columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_market_review)")}
            self.assertNotIn("effective_limit_up", columns)
            self.assertNotIn("ladder_status", columns)
            event_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'daily_price_limit_event'"
            ).fetchone()[0]
            review_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'daily_market_review'"
            ).fetchone()[0]
            self.assertIn("STRICT", event_sql)
            self.assertIn("STRICT", review_sql)
            self.assertNotIn("CHECK", event_sql)
            self.assertNotIn("CHECK", review_sql)
        finally:
            conn.close()

    def test_strict_tables_reject_text_in_integer_columns(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            init_db(conn)
            now = "2026-08-21T00:00:00+00:00"
            cases = (
                "closed_at_limit",
                "limit_rate_bp",
                "streak_height",
            )
            values = {
                "closed_at_limit": 1,
                "limit_rate_bp": 1000,
                "streak_height": 1,
            }
            for field in cases:
                payload = dict(values)
                payload[field] = "true"
                with self.subTest(field=field):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            """
                            INSERT INTO daily_price_limit_event (
                                trade_date, market, code, name, direction,
                                closed_at_limit, limit_rate_bp, streak_height,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "2026-08-21",
                                "sh",
                                "600519",
                                "茅台",
                                "up",
                                payload["closed_at_limit"],
                                payload["limit_rate_bp"],
                                payload["streak_height"],
                                now,
                                now,
                            ),
                        )
                    conn.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO daily_market_review (
                        trade_date, advancing_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("2026-08-21", "ten", now, now),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
