import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.t0_schema import (  # noqa: E402
    MarketDataSchemaError,
    standardize_bar,
    standardize_kline_series,
    standardize_quote_snapshot,
    standardize_security_identity,
)


class T0MarketSchemaTests(unittest.TestCase):
    def test_security_identity_reuses_market_rules(self):
        self.assertEqual(
            standardize_security_identity("600519"),
            {
                "symbol": "sh.600519",
                "code": "600519",
                "market": "sh",
                "timezone": "Asia/Shanghai",
            },
        )
        self.assertEqual(standardize_security_identity("sz.000001")["market"], "sz")

    def test_kline_series_maps_provider_rows_without_parallel_model(self):
        payload = standardize_kline_series(
            "600519",
            [
                {
                    "date": "2026-07-22 09:35:00",
                    "open": 1500.0,
                    "high": 1502.0,
                    "low": 1499.0,
                    "close": 1501.0,
                    "volume": 1000,
                    "amount": 1_500_500.0,
                    "closed": True,
                }
            ],
            timeframe="5m",
        )

        self.assertEqual(payload["schema_version"], "t0_market_v1")
        self.assertEqual(payload["symbol"], "sh.600519")
        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertEqual(
            set(payload["bars"][0]),
            {"timestamp", "open", "high", "low", "close", "volume", "amount", "closed"},
        )

    def test_amount_and_closed_cannot_be_fabricated(self):
        incomplete = {
            "date": "2026-07-22 09:35:00",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 100,
        }
        with self.assertRaisesRegex(MarketDataSchemaError, "amount"):
            standardize_bar(incomplete, closed=True)
        with self.assertRaisesRegex(MarketDataSchemaError, "closed"):
            standardize_bar({**incomplete, "amount": 1005.0})

    def test_quote_snapshot_maps_legacy_names_and_keeps_market_time(self):
        payload = standardize_quote_snapshot(
            "000001",
            {
                "timestamp": "2026-07-22 10:15:03",
                "price": 12.3,
                "change_pct": 1.2,
                "open": 12.1,
                "high": 12.4,
                "low": 12.0,
                "pre_close": 12.15,
                "volume": 10000,
                "amount": 123000.0,
                "volume_ratio": 1.1,
                "order_imbalance": None,
                "turnover_rate": 0.8,
            },
            market="sz",
        )

        self.assertEqual(payload["symbol"], "sz.000001")
        self.assertEqual(payload["quote"]["timestamp"], "2026-07-22 10:15:03")
        self.assertEqual(payload["quote"]["latest_price"], 12.3)
        self.assertEqual(payload["quote"]["previous_close"], 12.15)

    def test_quote_rejects_request_completion_time_substitution(self):
        with self.assertRaisesRegex(MarketDataSchemaError, "market timestamp"):
            standardize_quote_snapshot(
                "600519",
                {
                    "price": 1500.0,
                    "change_pct": 0.1,
                    "open": 1498.0,
                    "high": 1501.0,
                    "low": 1497.0,
                    "pre_close": 1499.0,
                    "volume": 100,
                    "amount": 150000.0,
                },
            )

    def test_invalid_market_and_ohlc_are_rejected(self):
        with self.assertRaises(MarketDataSchemaError):
            standardize_security_identity("830799")
        with self.assertRaisesRegex(MarketDataSchemaError, "high"):
            standardize_bar(
                {
                    "date": "2026-07-22",
                    "open": 10.0,
                    "high": 9.9,
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 100,
                    "amount": 1000.0,
                    "closed": True,
                }
            )
        with self.assertRaisesRegex(MarketDataSchemaError, "finite"):
            standardize_bar(
                {
                    "date": "2026-07-22",
                    "open": 10.0,
                    "high": float("nan"),
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 100,
                    "amount": 1000.0,
                    "closed": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
