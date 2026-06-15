import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from market_data import TencentStockDataProvider


class TencentStockDataProviderTests(unittest.TestCase):
    def test_get_minute_kline_parses_and_filters_rows(self):
        payload = {
            "code": 0,
            "msg": "",
            "data": {
                "sh600519": {
                    "m5": [
                        ["202606111500", "1278.26", "1279.00", "1279.64", "1276.17", "4443.00", {}, "3.55"],
                        ["202606121455", "1289.53", "1291.40", "1291.50", "1289.48", "1040.00", {}, "0.83"],
                        ["202606121500", "1291.40", "1291.91", "1292.65", "1291.40", "1556.00", {}, "1.24"],
                    ],
                    "prec": "1279.00",
                }
            },
        }

        with patch.object(TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="600519",
                market="sh",
                start_date="2026-06-12",
                end_date="2026-06-12",
                ktype="5m",
            )

        self.assertIn("m5", fetch.call_args.args[0])
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-06-12 14:55:00",
                    "open": 1289.53,
                    "close": 1291.40,
                    "high": 1291.50,
                    "low": 1289.48,
                    "volume": 1040,
                },
                {
                    "date": "2026-06-12 15:00:00",
                    "open": 1291.40,
                    "close": 1291.91,
                    "high": 1292.65,
                    "low": 1291.40,
                    "volume": 1556,
                },
            ],
        )

    def test_get_minute_kline_rejects_unsupported_ktype(self):
        self.assertEqual(
            TencentStockDataProvider.get_minute_kline(
                code="600519",
                market="sh",
                start_date="2026-06-12",
                end_date="2026-06-12",
                ktype="15m",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
