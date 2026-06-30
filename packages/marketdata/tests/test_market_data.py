import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.market_data import TencentStockDataProvider, get_market_prefix
from marketdata.provider_result import MarketDataResult


class GetMarketPrefixTests(unittest.TestCase):
    def test_explicit_market_wins(self):
        self.assertEqual(get_market_prefix("000001", market="sh"), "sh")
        self.assertEqual(get_market_prefix("159915", market="sh"), "sh")

    def test_sh_stocks_and_etfs(self):
        # 6xxxxx 沪市股票、5xxxxx 沪市 ETF
        self.assertEqual(get_market_prefix("600519"), "sh")
        self.assertEqual(get_market_prefix("510300"), "sh")
        self.assertEqual(get_market_prefix("588000"), "sh")

    def test_sz_stocks_and_etfs(self):
        # 0/3 深市股票、1 深市 ETF 159xxx / 分级 150xxx
        self.assertEqual(get_market_prefix("000001"), "sz")
        self.assertEqual(get_market_prefix("300750"), "sz")
        self.assertEqual(get_market_prefix("159915"), "sz")
        self.assertEqual(get_market_prefix("150018"), "sz")

    def test_bse_stocks(self):
        # 北交所 8/4 开头，以及 920xxx 新股以 9 开头
        self.assertEqual(get_market_prefix("830799"), "bj")
        self.assertEqual(get_market_prefix("430047"), "bj")
        self.assertEqual(get_market_prefix("920819"), "bj")


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

    def test_get_minute_kline_paginates_to_cover_start_date(self):
        page1 = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m60": [
                        ["202603101500", "1790.00", "1791.00", "1792.00", "1789.00", "900.00"],
                        ["202603131500", "1800.00", "1801.00", "1802.00", "1799.00", "1000.00"],
                        ["202603161030", "1801.00", "1802.00", "1803.00", "1800.00", "1100.00"],
                    ],
                }
            },
        }
        page2 = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m60": [
                        ["202510211030", "1700.00", "1701.00", "1702.00", "1699.00", "500.00"],
                        ["202510211130", "1701.00", "1702.00", "1703.00", "1700.00", "600.00"],
                    ],
                }
            },
        }

        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=[json.dumps(page1), json.dumps(page2)],
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="600519",
                market="sh",
                start_date="2025-10-21",
                end_date="2026-03-16",
                ktype="60m",
            )

        # Two pages fetched: first with empty ref, second using page1's oldest bar as ref
        self.assertEqual(fetch.call_count, 2)
        first_url = fetch.call_args_list[0].args[0]
        second_url = fetch.call_args_list[1].args[0]
        self.assertIn(",m60,,800", first_url)
        self.assertIn("202603101500", second_url)

        # All 5 bars from both pages, filtered to the date range, sorted ascending
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["date"], "2025-10-21 10:30:00")
        self.assertEqual(rows[-1]["date"], "2026-03-16 10:30:00")

    def test_get_kline_index_uses_no_adjustment(self):
        # 指数在 qfq 下腾讯返回空；security_type="index" 应把 autype 折成 ""，
        # 用不复权的 "day" 键取数据，且 URL 里不出现 qfq。
        payload = {
            "code": 0,
            "data": {
                "sh000001": {
                    "day": [
                        ["2026-06-11", "3000.00", "3010.00", "3015.00", "2995.00", "100"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="000001",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-11",
                ktype="day",
                security_type="index",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 3010.00)
        url = fetch.call_args.args[0]
        self.assertIn("sh000001", url)
        self.assertNotIn("qfq", url)

    def test_realtime_result_request_failed(self):
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", side_effect=RuntimeError("boom")
        ):
            result = TencentStockDataProvider.realtime_result("600519", markets=["sh"])
        self.assertFalse(result.success)
        self.assertEqual(result.data, [])
        self.assertTrue(result.errors())
        self.assertEqual(result.errors()[0].reason_code, "request_failed")

    def test_get_kline_result_nonzero_code_is_error(self):
        payload = {"code": 1, "msg": "bad"}
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-11",
                ktype="day",
            )
        self.assertFalse(result.success)
        self.assertEqual(result.data, [])
        self.assertTrue(result.errors())
        self.assertEqual(result.errors()[0].reason_code, "provider_nonzero_code")

    def test_get_kline_result_unexpected_shape_is_error(self):
        payload = {"code": 0, "data": []}
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-11",
                ktype="day",
            )
        self.assertFalse(result.success)
        self.assertEqual(result.data, [])
        self.assertTrue(result.errors())
        self.assertEqual(result.errors()[0].reason_code, "unexpected_response_shape")

    def test_get_kline_result_no_data_is_warning(self):
        payload = {"code": 0, "data": {"sh600519": {"qfqday": []}}}
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-11",
                ktype="day",
            )
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])
        self.assertTrue(result.warnings())
        self.assertEqual(result.warnings()[0].reason_code, "no_data")

    def test_get_kline_result_parse_failed_is_warning_when_partial(self):
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "qfqday": [
                        ["2026-06-11", "100.00", "101.00", "102.00", "99.00", "100"],
                        ["2026-06-12", "bad", "101.00", "102.00", "99.00", "100"],
                    ]
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-12",
                ktype="day",
            )
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertTrue(any(issue.reason_code == "parse_failed" for issue in result.warnings()))

    def test_wrappers_keep_original_shapes(self):
        with patch.object(
            TencentStockDataProvider,
            "realtime_result",
            return_value=MarketDataResult(success=True, data={"name": "x"}, issues=[]),
        ):
            self.assertIsInstance(
                TencentStockDataProvider.realtime("600519", markets=["sh"]), dict
            )

        with patch.object(
            TencentStockDataProvider,
            "get_kline_result",
            return_value=MarketDataResult(success=True, data=[{"date": "2026-06-11"}], issues=[]),
        ):
            self.assertIsInstance(
                TencentStockDataProvider.get_kline(
                    "600519", "2026-06-11", "2026-06-11", market="sh"
                ),
                list,
            )

        with patch.object(
            TencentStockDataProvider,
            "get_daily_quote_result",
            return_value=MarketDataResult(success=True, data=None, issues=[]),
        ):
            self.assertIsNone(
                TencentStockDataProvider.get_daily_quote(
                    "600519", "2026-06-11", market="sh"
                )
            )


if __name__ == "__main__":
    unittest.main()
