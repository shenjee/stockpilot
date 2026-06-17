import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parents[1]
SCRIPTS_DIR = ROOT / "skills" / "china-stock-analysis" / "scripts"
MODULE_PATH = APP_DIR / "services" / "market_service.py"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("chan_market_service", MODULE_PATH)
market_service = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = market_service
SPEC.loader.exec_module(market_service)


class FakeKLineDataService:
    def __init__(self):
        self.calls = []

    def get_klines(self, **kwargs):
        self.calls.append(kwargs)
        return [{"date": "2026-06-12", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100}]


class MarketServiceTests(unittest.TestCase):
    def test_day_timeframe_uses_shared_kline_data_service(self):
        fake_service = FakeKLineDataService()
        with patch.object(market_service, "_get_kline_data_service", return_value=fake_service):
            rows = market_service.fetch_rows(
                symbol="000001",
                market="sz",
                timeframe="day",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 12),
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(fake_service.calls), 1)
        self.assertEqual(fake_service.calls[0]["timeframe"], "day")
        self.assertEqual(fake_service.calls[0]["start_date"], "2026-06-01")

    def test_minute_timeframe_also_uses_shared_kline_data_service(self):
        fake_service = FakeKLineDataService()
        with patch.object(market_service, "_get_kline_data_service", return_value=fake_service):
            rows = market_service.fetch_rows(
                symbol="000001",
                market="sz",
                timeframe="5m",
                start_date=date(2026, 6, 12),
                end_date=date(2026, 6, 12),
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(fake_service.calls), 1)
        self.assertEqual(fake_service.calls[0]["timeframe"], "5m")
        self.assertEqual(fake_service.calls[0]["end_date"], "2026-06-12")

    def test_fetch_rows_for_timeframes_groups_non_empty_payloads(self):
        fake_service = FakeKLineDataService()

        def fake_get_klines(**kwargs):
            fake_service.calls.append(kwargs)
            if kwargs["timeframe"] == "week":
                return []
            return [{"date": "2026-06-12", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100}]

        fake_service.get_klines = fake_get_klines
        with patch.object(market_service, "_get_kline_data_service", return_value=fake_service):
            rows_by_timeframe = market_service.fetch_rows_for_timeframes(
                symbol="000001",
                market="sz",
                timeframes=["day", "week", "day", "month"],
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 12),
            )

        self.assertEqual(list(rows_by_timeframe.keys()), ["day", "month"])
        self.assertEqual([call["timeframe"] for call in fake_service.calls], ["day", "week", "month"])

    def test_fetch_stock_name_returns_name_from_realtime(self):
        with patch.object(
            market_service.TencentStockDataProvider,
            "realtime",
            return_value={"name": "北方稀土", "code": "600111"},
        ):
            name = market_service.fetch_stock_name("600111", "sh")

        self.assertEqual(name, "北方稀土")

    def test_fetch_stock_name_returns_empty_on_failure(self):
        with patch.object(
            market_service.TencentStockDataProvider,
            "realtime",
            side_effect=RuntimeError("network error"),
        ):
            name = market_service.fetch_stock_name("600111", "sh")

        self.assertEqual(name, "")


if __name__ == "__main__":
    unittest.main()
