import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.market_data import TencentStockDataProvider, get_market_prefix, normalize_hk_code
from marketdata.provider_result import MarketDataResult


def realtime_payload(code: str, name: str, timestamp: str = "20260716160843") -> str:
    parts = [""] * 50
    values = {
        0: "100",
        1: name,
        2: code,
        3: "5.500",
        4: "5.200",
        5: "5.280",
        6: "1000",
        30: timestamp,
        33: "5.600",
        34: "5.100",
        37: "550.000",
        38: "0.10",
        49: "1.20",
    }
    for index, value in values.items():
        parts[index] = value
    return f'v_hk{code}="{"~".join(parts)}";'


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

    def test_hk_requires_explicit_market(self):
        # 港股不做代码推断，必须显式传入 market="hk"
        self.assertEqual(get_market_prefix("0175", market="hk"), "hk")
        self.assertEqual(get_market_prefix("3896", market="hk"), "hk")
        self.assertEqual(get_market_prefix("00700", market="hk"), "hk")


class NormalizeHkCodeTests(unittest.TestCase):
    def test_pads_short_numeric_codes_to_5_digits(self):
        # 4 位 -> 5 位补零
        self.assertEqual(normalize_hk_code("0175"), "00175")
        self.assertEqual(normalize_hk_code("3896"), "03896")

    def test_keeps_5_digit_codes_unchanged(self):
        self.assertEqual(normalize_hk_code("00700"), "00700")
        self.assertEqual(normalize_hk_code("00175"), "00175")

    def test_keeps_longer_or_non_numeric_codes_unchanged(self):
        # 6 位及以上或非数字原样返回，交由上游处理
        self.assertEqual(normalize_hk_code("600519"), "600519")
        self.assertEqual(normalize_hk_code("0700.HK"), "0700.HK")
        self.assertEqual(normalize_hk_code(""), "")


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
        amount_payload = {
            "code": 0,
            "data": {"sh600519": {"data": []}},
        }

        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=[json.dumps(payload), json.dumps(amount_payload)],
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="600519",
                market="sh",
                start_date="2026-06-12",
                end_date="2026-06-12",
                ktype="5m",
            )

        self.assertIn("m5", fetch.call_args_list[0].args[0])
        self.assertIn("/day/query", fetch.call_args_list[1].args[0])
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-06-12 14:55:00",
                    "timestamp": "2026-06-12 14:55:00",
                    "open": 1289.53,
                    "close": 1291.40,
                    "high": 1291.50,
                    "low": 1289.48,
                    "volume": 1040,
                    "amount": None,
                    "closed": True,
                },
                {
                    "date": "2026-06-12 15:00:00",
                    "timestamp": "2026-06-12 15:00:00",
                    "open": 1291.40,
                    "close": 1291.91,
                    "high": 1292.65,
                    "low": 1291.40,
                    "volume": 1556,
                    "amount": None,
                    "closed": True,
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

    def test_get_minute_kline_uses_reported_cumulative_amounts_when_bar_omits_them(self):
        kline_payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m5": [
                        ["202606120935", "10.00", "10.10", "10.20", "9.90", "100"],
                        ["202606120940", "10.10", "10.20", "10.30", "10.00", "120"],
                    ]
                }
            },
        }
        amount_payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "data": [
                        {
                            "date": "20260612",
                            "data": [
                                "0930 10.00 10 10000.00",
                                "0935 10.10 110 25000.00",
                                "0940 10.20 230 40000.00",
                            ],
                        }
                    ]
                }
            },
        }
        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=[json.dumps(kline_payload), json.dumps(amount_payload)],
        ) as fetch:
            result = TencentStockDataProvider.get_minute_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-12",
                end_date="2026-06-12",
                ktype="5m",
            )

        self.assertTrue(result.success)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("/day/query", fetch.call_args_list[1].args[0])
        self.assertEqual([row["amount"] for row in result.data], [1.5, 1.5])
        self.assertTrue(all(row["closed"] for row in result.data))

    def test_get_minute_kline_keeps_historical_bars_without_amount_coverage(self):
        kline_payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m5": [
                        ["202606150935", "10.00", "10.10", "10.20", "9.90", "100", {}, "0.1"],
                        ["202606150940", "10.10", "10.20", "10.30", "10.00", "120", {}, "0.1"],
                    ]
                }
            },
        }
        recent_amount_payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "data": [
                        {
                            "date": "20260723",
                            "data": ["0935 11.00 100 11000.00"],
                        }
                    ]
                }
            },
        }
        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=[json.dumps(kline_payload), json.dumps(recent_amount_payload)],
        ):
            result = TencentStockDataProvider.get_minute_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-15",
                end_date="2026-06-15",
                ktype="5m",
            )

        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 2)
        self.assertEqual([row["amount"] for row in result.data], [None, None])
        self.assertTrue(
            any(
                issue.reason_code == "minute_amount_missing"
                and issue.context["missing_count"] == 2
                for issue in result.warnings()
            )
        )

    def test_get_minute_kline_paginates_to_cover_start_date(self):
        page1 = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m60": [
                        ["202603101500", "1790.00", "1791.00", "1792.00", "1789.00", "900.00", {}, "0.1"],
                        ["202603131500", "1800.00", "1801.00", "1802.00", "1799.00", "1000.00", {}, "0.1"],
                        ["202603161030", "1801.00", "1802.00", "1803.00", "1800.00", "1100.00", {}, "0.1"],
                    ],
                }
            },
        }
        page2 = {
            "code": 0,
            "data": {
                "sh600519": {
                    "m60": [
                        ["202510211030", "1700.00", "1701.00", "1702.00", "1699.00", "500.00", {}, "0.1"],
                        ["202510211130", "1701.00", "1702.00", "1703.00", "1700.00", "600.00", {}, "0.1"],
                    ],
                }
            },
        }
        amount_payload = {
            "code": 0,
            "data": {"sh600519": {"data": []}},
        }

        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=[
                json.dumps(page1),
                json.dumps(page2),
                json.dumps(amount_payload),
            ],
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="600519",
                market="sh",
                start_date="2025-10-21",
                end_date="2026-03-16",
                ktype="60m",
            )

        # Two K-line pages plus one optional amount enrichment request.
        self.assertEqual(fetch.call_count, 3)
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
                        ["2026-06-11", "3000.00", "3010.00", "3015.00", "2995.00", "100", {}, "0.1", "30000.00"],
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

    def test_get_kline_qfq_negative_prices_are_normalized(self):
        payload = {
            "code": 0,
            "data": {
                "sz000858": {
                    "qfqday": [
                        ["2006-10-09", "-23.03", "-22.71", "-22.53", "-23.06", "519459.000", {}, "0.1", "118000.00"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="000858",
                market="sz",
                start_date="2006-10-01",
                end_date="2006-12-31",
                ktype="day",
                autype="qfq",
            )
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertEqual(result.data[0]["open"], 23.03)
        self.assertEqual(result.data[0]["close"], 22.71)
        self.assertEqual(result.data[0]["high"], 22.53)
        self.assertEqual(result.data[0]["low"], 23.06)

    def test_realtime_result_request_failed(self):
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", side_effect=RuntimeError("boom")
        ):
            result = TencentStockDataProvider.realtime_result("600519", markets=["sh"])
        self.assertFalse(result.success)
        self.assertEqual(result.data, [])
        self.assertTrue(result.errors())
        self.assertEqual(result.errors()[0].reason_code, "request_failed")

    def test_realtime_hk_normalizes_code_in_url(self):
        payload = (
            'v_hk00175="100~吉利汽车~00175~19.390~18.400~18.640~57727595.0~0~0~'
            '19.390~0~0~0~0~0~0~0~0~0~19.390~0~0~0~0~0~0~0~0~0~57727595.0~'
            '2026/07/16 16:08:43~0.990~5.38~19.680~18.640~19.390~57727595.0~'
            '1114393993.750~0~11.21~~0~0~5.65~2091.2364~2091.2364~GEELY AUTO~2.58~'
            '25.120~14.460~1.69~-58.97~0~0~0~0~0~12.03~1.94~0.54~1000~11.44~6.95~'
            'GP~15.97~5.36~11.95~-0.97~-19.94~10785128597.00~10785128597.00~11.08~0.500~19";'
        )
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=payload
        ) as fetch:
            result = TencentStockDataProvider.realtime_result("0175", markets=["hk"])
        self.assertTrue(result.success)
        url = fetch.call_args.args[0]
        # 4 位 0175 必须补零成 5 位 hk00175
        self.assertIn("hk00175", url)
        self.assertNotIn("hk0175,", url)
        self.assertEqual(result.data["name"], "吉利汽车")
        self.assertAlmostEqual(result.data["price"], 19.39)
        self.assertEqual(result.data["timestamp"], "2026-07-16 16:08:43")
        self.assertEqual(result.data["latest_price"], 19.39)
        self.assertEqual(result.data["previous_close"], 18.4)
        self.assertEqual(result.data["change_percent"], result.data["change_pct"])
        self.assertIn("volume_ratio", result.data)
        self.assertIn("turnover_rate", result.data)

    def test_realtime_hk_tuple_form_normalizes_code(self):
        payload = realtime_payload("03896", "金山云")
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=payload
        ) as fetch:
            result = TencentStockDataProvider.realtime_result([("3896", "hk")])
        url = fetch.call_args.args[0]
        self.assertIn("hk03896", url)
        self.assertTrue(result.success)

    def test_realtime_hk_prefixed_input_normalizes_code(self):
        payload = realtime_payload("00175", "吉利汽车")
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=payload
        ) as fetch:
            result = TencentStockDataProvider.realtime_result("hk0175")
        url = fetch.call_args.args[0]
        self.assertIn("hk00175", url)
        self.assertNotIn("hk0175", url)
        self.assertTrue(result.success)

    def test_get_kline_hk_normalizes_code_in_url_and_payload(self):
        # 4 位港股代码 0175 应补零为 hk00175，用于 URL 和响应 payload 查找
        payload = {
            "code": 0,
            "data": {
                "hk00175": {
                    "qfqday": [
                        ["2026-07-16", "18.500", "18.260", "18.760", "18.240", "87042911.000", {}, "0.1", "160000.00"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="0175",
                market="hk",
                start_date="2026-07-01",
                end_date="2026-07-16",
                ktype="day",
            )
        url = fetch.call_args.args[0]
        self.assertIn("hk00175", url)
        self.assertNotIn("hk0175,", url)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 18.26)
        self.assertEqual(rows[0]["timestamp"], "2026-07-16")
        self.assertEqual(rows[0]["amount"], 160000.0)
        self.assertTrue(rows[0]["closed"])

    def test_get_kline_hk_5_digit_code_works_unchanged(self):
        payload = {
            "code": 0,
            "data": {
                "hk03896": {
                    "day": [
                        ["2026-07-16", "8.010", "7.600", "8.200", "7.560", "290029392.000", {}, "0.1", "220000.00"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ) as fetch:
            rows = TencentStockDataProvider.get_kline(
                code="03896",
                market="hk",
                start_date="2026-07-16",
                end_date="2026-07-16",
                ktype="day",
                autype="",
            )
        url = fetch.call_args.args[0]
        self.assertIn("hk03896", url)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 7.60)

    def test_get_kline_hk_qfq_falls_back_to_base_day_key(self):
        # 港股响应不论 autype 均返回 "day" 键，qfqday 缺失时应回退到 day
        payload = {
            "code": 0,
            "data": {
                "hk00175": {
                    "day": [
                        ["2026-07-16", "18.500", "18.260", "18.760", "18.240", "87042911.000", {}, "0.1", "160000.00"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            rows = TencentStockDataProvider.get_kline(
                code="0175",
                market="hk",
                start_date="2026-07-16",
                end_date="2026-07-16",
                ktype="day",
                autype="qfq",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 18.26)

    def test_get_kline_result_hk_qfq_fallback_emits_warning(self):
        payload = {
            "code": 0,
            "data": {
                "hk00175": {
                    "day": [
                        ["2026-07-16", "18.500", "18.260", "18.760", "18.240", "87042911.000", {}, "0.1", "160000.00"],
                    ],
                }
            },
        }
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=json.dumps(payload)
        ):
            result = TencentStockDataProvider.get_kline_result(
                code="0175",
                market="hk",
                start_date="2026-07-16",
                end_date="2026-07-16",
                ktype="day",
                autype="qfq",
            )
        self.assertTrue(result.success)
        self.assertEqual(len(result.data), 1)
        self.assertTrue(any(issue.reason_code == "adjustment_unavailable" for issue in result.warnings()))

    def test_get_kline_result_a_share_qfq_does_not_fall_back_to_day(self):
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "day": [
                        ["2026-06-11", "100.00", "101.00", "102.00", "99.00", "100"],
                    ],
                    "qfqday": [],
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
                end_date="2026-06-11",
                ktype="day",
                autype="qfq",
            )
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])
        self.assertFalse(any(issue.reason_code == "adjustment_unavailable" for issue in result.warnings()))
        self.assertEqual(result.warnings()[0].reason_code, "no_data")

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

    def test_get_kline_result_top_level_list_is_error(self):
        with patch.object(TencentStockDataProvider, "_fetch_with_retry", return_value="[]"):
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

    def test_get_minute_kline_result_top_level_list_is_error(self):
        with patch.object(TencentStockDataProvider, "_fetch_with_retry", return_value="[]"):
            result = TencentStockDataProvider.get_minute_kline_result(
                code="600519",
                market="sh",
                start_date="2026-06-11",
                end_date="2026-06-11",
                ktype="1m",
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
                        ["2026-06-11", "100.00", "101.00", "102.00", "99.00", "100", {}, "0.1", "10000.00"],
                        ["2026-06-12", "bad", "101.00", "102.00", "99.00", "100", {}, "0.1", "10000.00"],
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

    def test_get_kline_result_does_not_fabricate_missing_amount(self):
        payload = {
            "code": 0,
            "data": {
                "sh600519": {
                    "qfqday": [
                        ["2026-06-11", "100.00", "101.00", "102.00", "99.00", "100"]
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
                end_date="2026-06-11",
                ktype="day",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.data, [])
        self.assertEqual(result.errors()[0].reason_code, "parse_failed")

    def test_realtime_result_rejects_missing_provider_market_timestamp(self):
        payload = realtime_payload("00175", "吉利汽车", timestamp="")
        with patch.object(
            TencentStockDataProvider, "_fetch_with_retry", return_value=payload
        ):
            result = TencentStockDataProvider.realtime_result("hk0175")

        self.assertTrue(result.success)
        self.assertEqual(result.data, [])
        self.assertEqual(
            {issue.reason_code for issue in result.warnings()},
            {"parse_failed", "no_data"},
        )

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
