import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from services.report_data_service import ReportDataService


class FakeMarketData:
    def __init__(self):
        self.realtime_calls = []
        self.daily_quote_calls = []

    def realtime(self, codes):
        self.realtime_calls.append(codes)
        return [{"code": "000001", "price": 3000.0, "pre_close": 2990.0, "change": 10.0, "change_pct": 0.33}]

    def get_daily_quote(self, code: str, trade_date: str, market: str = None):
        self.daily_quote_calls.append((code, trade_date, market))
        return {
            "close": 3000.0,
            "pre_close": 2990.0,
            "open": 2995.0,
            "high": 3010.0,
            "low": 2980.0,
            "volume": 100000,
            "change": 10.0,
            "change_pct": 0.33,
        }


class ReportDataServiceTests(unittest.TestCase):
    def _service(self, market_data, is_historical: bool) -> ReportDataService:
        return ReportDataService(
            paths=SimpleNamespace(config_dir=Path("/tmp")),
            market_data=market_data,
            kline_data_service=None,
            target_date=datetime(2026, 6, 12),
            is_historical=is_historical,
        )

    def test_historical_index_data_uses_daily_quotes_for_target_date(self):
        market_data = FakeMarketData()
        result = self._service(market_data, is_historical=True).get_index_data()

        self.assertGreater(len(result), 0)
        self.assertEqual(len(market_data.realtime_calls), 0)
        self.assertTrue(all(call[1] == "2026-06-12" for call in market_data.daily_quote_calls))

    def test_realtime_index_data_uses_realtime_provider(self):
        market_data = FakeMarketData()
        self._service(market_data, is_historical=False).get_index_data()

        self.assertEqual(len(market_data.realtime_calls), 1)
        self.assertEqual(len(market_data.daily_quote_calls), 0)


if __name__ == "__main__":
    unittest.main()
