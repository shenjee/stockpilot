import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.repositories.kline_store import KLineStore
from marketdata.provider_result import MarketDataResult, ProviderIssue
from marketdata.services.kline_data_service import KLineDataService


class FakeProvider:
    provider_id = "fake"

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_kline(self, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None, security_type: str | None = None):
        self.calls.append((code, start_date, end_date, ktype, market, security_type))
        return list(self.rows)


class FakeResultProvider:
    provider_id = "fake-result"

    def __init__(self, result: MarketDataResult[list]):
        self.result = result
        self.calls = []

    def get_kline_result(self, *, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None, security_type: str | None = None):
        self.calls.append((code, start_date, end_date, ktype, market, security_type))
        return self.result

    def get_kline(self, *args, **kwargs):
        raise AssertionError("get_kline should not be called when get_kline_result exists")


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
                start_date="2026-05-01",
                min_local_count=60,
                limit=120,
            )

            self.assertEqual(len(provider.calls), 0)
            self.assertEqual(len(result), 60)

    def test_refetches_remote_rows_when_local_rows_have_negative_prices(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            local_rows = [
                {
                    "date": f"2026-05-{day:02d}",
                    "open": -10.0,
                    "close": -10.5,
                    "high": -10.6,
                    "low": -9.9,
                    "volume": 100 + day,
                }
                for day in range(1, 31)
            ] + [
                {
                    "date": f"2026-06-{day:02d}",
                    "open": -10.0,
                    "close": -10.5,
                    "high": -10.6,
                    "low": -9.9,
                    "volume": 200 + day,
                }
                for day in range(1, 31)
            ]
            store.upsert_many("600519", "sh", local_rows, source="local")

            remote_rows = [
                dict(row, open=abs(row["open"]), close=abs(row["close"]), high=abs(row["high"]), low=abs(row["low"]))
                for row in local_rows
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-30",
                market="sh",
                start_date="2026-05-01",
                min_local_count=60,
                limit=120,
            )

            self.assertEqual(len(provider.calls), 1)
            self.assertTrue(result)
            self.assertGreater(min(row["low"] for row in result), 0)

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

    def test_minute_timeframe_refetches_when_local_count_below_range_estimate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            # Seed the store with only 3 bars covering 2026-06-17 ~ 2026-06-18 (60m).
            # The requested range (2026-03-01 ~ 2026-06-18) needs far more than 3 bars,
            # so the cache check must trigger a refetch even though latest == end_date.
            seed_rows = [
                {"date": f"2026-06-1{d} 15:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
                for d in [6, 7, 8]
            ]
            store.upsert_many("600519", "sh", seed_rows, source="local", timeframe="60m")

            remote_rows = seed_rows + [
                {"date": f"2026-03-{d:02d} 15:00:00", "open": 9.0, "close": 9.5, "high": 9.6, "low": 8.9, "volume": 200}
                for d in range(1, 31)
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-18",
                market="sh",
                timeframe="60m",
                start_date="2026-03-01",
                limit=500,
            )

            # Provider must be called because local 3 bars < estimated required count.
            self.assertEqual(len(provider.calls), 1)
            self.assertGreater(len(result), 3)

    def test_minute_timeframe_refetches_when_earliest_does_not_cover_start_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            # Seed 5 bars on 2026-06-16 ~ 2026-06-18 (60m). The requested range
            # starts on 2026-03-01, so earliest (2026-06-16) is far later than
            # start_date — the cache must miss even though latest == end_date.
            seed_rows = [
                {"date": f"2026-06-{d} 15:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
                for d in [16, 17, 18]
            ]
            store.upsert_many("600519", "sh", seed_rows, source="local", timeframe="60m")

            remote_rows = seed_rows + [
                {"date": f"2026-03-{d:02d} 15:00:00", "open": 9.0, "close": 9.5, "high": 9.6, "low": 8.9, "volume": 200}
                for d in range(1, 31)
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            result = service.get_klines(
                code="600519",
                end_date="2026-06-18",
                market="sh",
                timeframe="60m",
                start_date="2026-03-01",
                limit=500,
            )

            # Provider must be called because earliest 2026-06-16 > start_date 2026-03-01.
            self.assertEqual(len(provider.calls), 1)
            self.assertGreater(len(result), 3)

    def test_security_type_forwarded_to_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            remote_rows = [
                {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
                {"date": "2026-06-11", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4, "volume": 120},
            ]
            provider = FakeProvider(rows=remote_rows)
            service = KLineDataService(provider, store)

            service.get_klines(code="000001", end_date="2026-06-11", market="sh", security_type="index", limit=10)

            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(provider.calls[0][5], "index")

    def test_prefers_provider_get_kline_result_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            remote_rows = [
                {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
                {"date": "2026-06-11", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4, "volume": 120},
            ]
            provider = FakeResultProvider(
                MarketDataResult(
                    success=False,
                    data=remote_rows,
                    issues=[ProviderIssue(level="error", reason_code="request_failed", message="network error")],
                )
            )
            service = KLineDataService(provider, store)

            result = service.get_klines_result(code="600519", end_date="2026-06-11", market="sh", limit=10)

            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(result.data, remote_rows)
            self.assertEqual(result.issues[0].reason_code, "request_failed")
            self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
