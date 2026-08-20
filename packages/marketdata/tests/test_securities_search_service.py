import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.repositories.securities_store import SecuritiesStore  # noqa: E402
from marketdata.services import SecuritiesSearchService  # noqa: E402
from marketdata.t0_schema import InstrumentType, MarketDataSchemaError  # noqa: E402


def _fixture_records() -> list[dict[str, str]]:
    return [
        {
            "code": "000001",
            "market": "sh",
            "type": "index",
            "name": "上证指数",
            "pinyin": "SZZS",
        },
        {
            "code": "000001",
            "market": "sz",
            "type": "stock",
            "name": "平安银行",
            "pinyin": "PAYH",
        },
        {
            "code": "600519",
            "market": "sh",
            "type": "stock",
            "name": "贵州茅台",
            "pinyin": "GZMT",
        },
        {
            "code": "510300",
            "market": "sh",
            "type": "etf",
            "name": "沪深300ETF",
            "pinyin": "HS300ETF",
        },
        {
            "code": "159915",
            "market": "sz",
            "type": "etf",
            "name": "创业板ETF",
            "pinyin": "CYBETF",
        },
        {
            "code": "830799",
            "market": "bj",
            "type": "stock",
            "name": "艾融软件",
            "pinyin": "ARRJ",
        },
        {
            "code": "00700",
            "market": "hk",
            "type": "stock",
            "name": "腾讯控股",
            "pinyin": "TXKG",
        },
    ]


class SecuritiesSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        json_path = root / "securities_master.json"
        json_path.write_text(
            json.dumps(_fixture_records(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.store = SecuritiesStore(root / "market_data.sqlite", json_path=json_path)
        self.service = SecuritiesSearchService(self.store)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_stock_searches_by_code_name_and_pinyin(self):
        for query in ("600519", "贵州", "gzmt"):
            with self.subTest(query=query):
                results = [r.to_dict() for r in self.service.search(query)]
                self.assertEqual(
                    results,
                    [
                        {
                            "symbol": "sh.600519",
                            "code": "600519",
                            "market": "sh",
                            "name": "贵州茅台",
                            "instrument_type": "stock",
                        }
                    ],
                )

    def test_etf_searches_by_code_name_and_pinyin(self):
        expected = {
            "symbol": "sh.510300",
            "code": "510300",
            "market": "sh",
            "name": "沪深300ETF",
            "instrument_type": "etf",
        }
        for query in ("510300", "沪深300", "hs300"):
            with self.subTest(query=query):
                results = [r.to_dict() for r in self.service.search(query)]
                self.assertEqual(results, [expected])

    def test_index_searches_are_returned_not_filtered(self):
        """Issue #151: indices are no longer filtered from search results.

        Whether an instrument can be viewed (search/K-line) and whether it can
        be traded (fee layer) are independent eligibility boundaries.
        """
        results = [r.to_dict() for r in self.service.search("上证指数")]
        self.assertEqual(
            results,
            [
                {
                    "symbol": "sh.000001",
                    "code": "000001",
                    "market": "sh",
                    "name": "上证指数",
                    "instrument_type": "index",
                }
            ],
        )

    def test_non_t0_markets_are_filtered_from_search(self):
        """Beijing and Hong Kong securities are not T0-eligible markets."""
        self.assertEqual(self.service.search("艾融软件"), [])
        self.assertEqual(self.service.search("腾讯控股"), [])

    def test_filtering_happens_before_limit(self):
        # SecuritiesStore 对 000001 的稳定排序是 sh 指数在 sz 股票之前。
        # 指数不再被排除；limit=1 应返回排序在前的 sh 指数。
        results = [r.to_dict() for r in self.service.search("000001", limit=1)]
        self.assertEqual(
            results,
            [
                {
                    "symbol": "sh.000001",
                    "code": "000001",
                    "market": "sh",
                    "name": "上证指数",
                    "instrument_type": "index",
                }
            ],
        )

    def test_result_only_contains_frozen_security_identity_fields(self):
        result = self.service.search("创业板ETF")[0].to_dict()
        self.assertEqual(
            set(result),
            {"symbol", "code", "market", "name", "instrument_type"},
        )
        self.assertNotIn("pinyin", result)
        self.assertNotIn("type", result)
        self.assertNotIn("timezone", result)

    def test_get_uses_standard_market_inference(self):
        # 仓储若不带 market 会优先返回同代码的 sh 指数；服务按代码规则取 sz 股票。
        result = self.service.get("000001")
        self.assertIsNotNone(result)
        self.assertEqual(
            result.to_dict(),
            {
                "symbol": "sz.000001",
                "code": "000001",
                "market": "sz",
                "name": "平安银行",
                "instrument_type": "stock",
            },
        )
        etf = self.service.get("sh.510300")
        self.assertIsNotNone(etf)
        self.assertEqual(etf.instrument_type, InstrumentType.ETF)

    def test_get_returns_none_for_missing_security(self):
        self.assertIsNone(self.service.get("999999", "sh"))

    def test_get_returns_index_identity_for_index_symbol(self):
        """Issue #151: indices are no longer filtered; get returns the index."""
        result = self.service.get("sh.000001")
        self.assertIsNotNone(result)
        self.assertEqual(result.instrument_type, InstrumentType.INDEX)
        self.assertEqual(result.name, "上证指数")

    def test_get_rejects_non_t0_market(self):
        with self.assertRaises(MarketDataSchemaError):
            self.service.get("830799", "bj")

    def test_empty_search_and_invalid_limit(self):
        self.assertEqual(self.service.search(""), [])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    self.service.search("600519", limit=limit)

    def test_default_store_reuses_bundled_stock_and_etf_master(self):
        bundled_store = SecuritiesStore(
            Path(self.tmpdir.name) / "bundled_market_data.sqlite"
        )
        service = SecuritiesSearchService(bundled_store)

        self.assertEqual(service.search("gzmt")[0].symbol, "sh.600519")
        self.assertEqual(service.search("sh.600519"), [])
        self.assertEqual(service.get("sh.600519").name, "贵州茅台")
        etf = service.search("沪深300ETF华泰柏瑞")[0]
        self.assertEqual(etf.symbol, "sh.510300")
        self.assertEqual(etf.instrument_type, InstrumentType.ETF)


if __name__ == "__main__":
    unittest.main()
