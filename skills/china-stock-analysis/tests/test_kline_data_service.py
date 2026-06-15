import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repositories.kline_store import KLineStore
from services.kline_data_service import KLineDataService


class FakeProvider:
    provider_id = "fake"

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_kline(self, code: str, start_date: str, end_date: str, ktype: str = "day", market: str = None):
        self.calls.append((code, start_date, end_date, ktype, market))
        return list(self.rows)


class KLineDataServiceTests(unittest.TestCase):
    def test_prefers_local_rows_when_count_is_sufficient(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            rows = [
                {
                    "date": f"2026-05-{day:02d}",
                    "open": 10.0,
                    "close": 10.5,
                    "high": 10.6,
                    "low": 9.9,
                    "volume": 100 + day,
                }
                for day in range(1, 31)
            ] + [
                {
                    "date": f"2026-06-{day:02d}",
                    "open": 10.0,
                    "close": 10.5,
                    "high": 10.6,
                    "low": 9.9,
                    "volume": 200 + day,
                }
                for day in range(1, 31)
            ]
            store.upsert_many("600519", "sh", rows, source="local")
            provider = FakeProvider(rows=[])
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-30",
                market="sh",
                min_local_count=60,
                limit=120,
            )

            self.assertEqual(len(provider.calls), 0)
            self.assertEqual(len(result), 60)

    def test_fetches_remote_rows_and_persists_then_reads_from_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            remote_rows = [
                {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
                {"date": "2026-06-11", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4, "volume": 120},
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(code="600519", end_date="2026-06-11", market="sh", limit=10)

            self.assertEqual(len(provider.calls), 1)
            self.assertEqual([row["date"] for row in result], ["2026-06-10", "2026-06-11"])
            self.assertEqual(store.latest_date("600519", "sh"), "2026-06-11")

    def test_minute_timeframe_roundtrip_uses_shared_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            remote_rows = [
                {"date": "2026-06-12 09:30:00", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 100},
                {"date": "2026-06-12 09:35:00", "open": 10.2, "close": 10.4, "high": 10.5, "low": 10.1, "volume": 120},
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-12",
                market="sh",
                timeframe="5m",
                start_date="2026-06-12",
                limit=10,
            )

            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(provider.calls[0][3], "5m")
            self.assertEqual([row["date"] for row in result], ["2026-06-12 09:30:00", "2026-06-12 09:35:00"])
            self.assertEqual(store.latest_date("600519", "sh", timeframe="5m"), "2026-06-12 09:35:00")

    def test_minute_timeframe_fetches_when_local_rows_do_not_cover_session_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            store.upsert_many(
                "600519",
                "sh",
                [
                    {"date": "2026-06-12 09:30:00", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 100},
                ],
                source="local",
                timeframe="5m",
            )
            remote_rows = [
                {"date": "2026-06-12 09:30:00", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 100},
                {"date": "2026-06-12 15:00:00", "open": 10.2, "close": 10.4, "high": 10.5, "low": 10.1, "volume": 120},
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-12",
                market="sh",
                timeframe="5m",
                start_date="2026-06-12",
                limit=100,
            )

            self.assertEqual(len(provider.calls), 1)
            self.assertEqual([row["date"] for row in result], ["2026-06-12 09:30:00", "2026-06-12 15:00:00"])


if __name__ == "__main__":
    unittest.main()
