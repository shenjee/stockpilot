import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.repositories.kline_store import KLineStore


class KLineStoreTests(unittest.TestCase):
    def test_upsert_and_get_klines_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            store.upsert_many(
                code="600519",
                market="sh",
                klines=[
                    {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100, "amount": 1025.0},
                    {"date": "2026-06-11", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4, "volume": 120, "amount": 1290.0},
                ],
                source="test",
            )

            rows = store.get_klines("600519", "2026-06-11", market="sh", limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["date"], "2026-06-10")
            self.assertEqual(rows[-1]["close"], 11.0)
            self.assertEqual(rows[-1]["amount"], 1290.0)
            self.assertEqual(store.latest_date("600519", "sh"), "2026-06-11")
            self.assertEqual(store.count_since("600519", "2026-06-10", "sh"), 2)

    def test_earliest_timestamp_and_bounded_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            store.upsert_many(
                code="600519",
                market="sh",
                klines=[
                    {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
                    {"date": "2026-06-11", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4, "volume": 120},
                    {"date": "2026-06-12", "open": 11.0, "close": 11.5, "high": 11.6, "low": 10.9, "volume": 130},
                ],
                source="test",
            )

            self.assertEqual(store.earliest_timestamp("600519", "sh"), "2026-06-10")
            # count_since without end_date counts everything from start onward
            self.assertEqual(store.count_since("600519", "2026-06-10", "sh"), 3)
            # count_since with end_date bounds the range
            self.assertEqual(store.count_since("600519", "2026-06-10", "sh", end_date="2026-06-11"), 2)
            self.assertEqual(store.count_since("600519", "2026-06-11", "sh", end_date="2026-06-11"), 1)

    def test_upsert_and_get_minute_klines_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            store.upsert_many(
                code="600519",
                market="sh",
                timeframe="5m",
                klines=[
                    {"date": "2026-06-12 09:30:00", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 100, "amount": 1010.0},
                    {"date": "2026-06-12 09:35:00", "open": 10.2, "close": 10.4, "high": 10.5, "low": 10.1, "volume": 120, "amount": 1236.0},
                ],
                source="test",
            )

            rows = store.get_klines(
                "600519",
                "2026-06-12 23:59:59",
                market="sh",
                timeframe="5m",
                start_date="2026-06-12",
                limit=10,
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["date"], "2026-06-12 09:30:00")
            self.assertEqual(rows[-1]["close"], 10.4)
            self.assertEqual(rows[-1]["amount"], 1236.0)
            self.assertEqual(store.latest_date("600519", "sh", timeframe="5m"), "2026-06-12 09:35:00")
            self.assertEqual(store.count_since("600519", "2026-06-12", "sh", timeframe="5m"), 2)

    def test_migrates_both_legacy_tables_idempotently_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE daily_klines (
                        symbol TEXT NOT NULL,
                        code TEXT NOT NULL,
                        market TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        open REAL NOT NULL,
                        close REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        volume INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT 'tencent',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (symbol, trade_date)
                    );
                    CREATE TABLE klines (
                        symbol TEXT NOT NULL,
                        code TEXT NOT NULL,
                        market TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        open REAL NOT NULL,
                        close REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        volume INTEGER NOT NULL,
                        source TEXT NOT NULL DEFAULT 'tencent',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (symbol, timeframe, timestamp)
                    );
                    INSERT INTO daily_klines VALUES (
                        'sh600519', '600519', 'sh', '2026-06-10',
                        10.0, 10.5, 10.6, 9.9, 100, 'legacy', '2026-06-10 16:00:00'
                    );
                    """
                )

            KLineStore(db_path)
            KLineStore(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                daily_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(daily_klines)")
                }
                kline_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(klines)")
                }
                legacy = conn.execute(
                    "SELECT trade_date, volume, amount FROM daily_klines"
                ).fetchone()
                copied = conn.execute(
                    "SELECT timestamp, volume, amount FROM klines"
                ).fetchone()

            self.assertIn("amount", daily_columns)
            self.assertIn("amount", kline_columns)
            self.assertEqual(legacy, ("2026-06-10", 100, None))
            self.assertEqual(copied, ("2026-06-10", 100, None))

    def test_upsert_updates_amount_in_both_daily_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            store = KLineStore(db_path)
            base = {
                "date": "2026-06-10",
                "open": 10.0,
                "close": 10.5,
                "high": 10.6,
                "low": 9.9,
                "volume": 100,
            }
            store.upsert_many(
                "600519", "sh", [{**base, "amount": 1025.0}], source="first"
            )
            store.upsert_many(
                "600519", "sh", [{**base, "amount": 1030.0}], source="second"
            )

            with closing(sqlite3.connect(db_path)) as conn:
                current = conn.execute(
                    "SELECT amount, source FROM klines"
                ).fetchone()
                daily = conn.execute(
                    "SELECT amount, source FROM daily_klines"
                ).fetchone()

            self.assertEqual(current, (1030.0, "second"))
            self.assertEqual(daily, (1030.0, "second"))


if __name__ == "__main__":
    unittest.main()
