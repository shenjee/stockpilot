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
from marketdata.t0_schema import MarketDataSchemaError  # noqa: E402


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
                self.assertEqual(
                    self.service.search(query),
                    [
                        {
                            "symbol": "sh.600519",
                            "code": "600519",
                            "market": "sh",
                            "name": "贵州茅台",
                            "security_type": "a_share",
                        }
                    ],
                )

    def test_etf_searches_by_code_name_and_pinyin(self):
        expected = {
            "symbol": "sh.510300",
            "code": "510300",
            "market": "sh",
            "name": "沪深300ETF",
            "security_type": "etf",
        }
        for query in ("510300", "沪深300", "hs300"):
            with self.subTest(query=query):
                self.assertEqual(self.service.search(query), [expected])

    def test_only_supported_stock_and_etf_records_are_returned(self):
        self.assertEqual(self.service.search("上证指数"), [])
        self.assertEqual(self.service.search("艾融软件"), [])
        self.assertEqual(self.service.search("腾讯控股"), [])

    def test_filtering_happens_before_limit(self):
        # SecuritiesStore 对 000001 的稳定排序是 sh 指数在 sz 股票之前。
        # 服务必须先排除指数，再应用 limit，不能错误返回空列表。
        self.assertEqual(
            self.service.search("000001", limit=1),
            [
                {
                    "symbol": "sz.000001",
                    "code": "000001",
                    "market": "sz",
                    "name": "平安银行",
                    "security_type": "a_share",
                }
            ],
        )

    def test_result_only_contains_frozen_security_identity_fields(self):
        result = self.service.search("创业板ETF")[0]
        self.assertEqual(
            set(result),
            {"symbol", "code", "market", "name", "security_type"},
        )
        self.assertNotIn("pinyin", result)
        self.assertNotIn("type", result)
        self.assertNotIn("timezone", result)

    def test_get_uses_standard_market_inference(self):
        # 仓储若不带 market 会优先返回同代码的 sh 指数；服务按代码规则取 sz 股票。
        self.assertEqual(
            self.service.get("000001"),
            {
                "symbol": "sz.000001",
                "code": "000001",
                "market": "sz",
                "name": "平安银行",
                "security_type": "a_share",
            },
        )
        self.assertEqual(self.service.get("sh.510300")["security_type"], "etf")

    def test_get_returns_none_for_missing_or_unsupported_security(self):
        self.assertIsNone(self.service.get("999999", "sh"))
        self.assertIsNone(self.service.get("sh.000001"))

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

        self.assertEqual(service.search("gzmt")[0]["symbol"], "sh.600519")
        etf = service.search("沪深300ETF华泰柏瑞")[0]
        self.assertEqual(etf["symbol"], "sh.510300")
        self.assertEqual(etf["security_type"], "etf")


if __name__ == "__main__":
    unittest.main()
