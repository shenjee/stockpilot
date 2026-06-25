import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repositories.securities_store import SecuritiesStore  # noqa: E402


def _fixture_records() -> list[dict]:
    return [
        {"code": "000001", "market": "sz", "type": "stock", "name": "平安银行", "pinyin": "PAYH"},
        {"code": "000001", "market": "sh", "type": "index", "name": "上证指数", "pinyin": "SZZS"},
        {"code": "600519", "market": "sh", "type": "stock", "name": "贵州茅台", "pinyin": "GZMT"},
        {"code": "600549", "market": "sh", "type": "stock", "name": "厦门钨业", "pinyin": "XMWY"},
        {"code": "510300", "market": "sh", "type": "etf", "name": "沪深300ETF", "pinyin": "HS300ETF"},
    ]


def _write_json(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)


class SecuritiesStoreTests(unittest.TestCase):
    def _new_store(self, tmpdir: str, records: list[dict] | None = None) -> SecuritiesStore:
        """构造一个指向临时目录、不带 bundled JSON 的 store，避免误触发自动导入。"""

        json_path = Path(tmpdir) / "securities_master.json"
        if records is not None:
            _write_json(json_path, records)
        # 显式传 json_path，且 ensure_loaded 只在表为空且文件存在时才导入。
        return SecuritiesStore(Path(tmpdir) / "market_data.sqlite", json_path=json_path)

    def test_import_json_and_search_by_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            # 精确 code：000001 同时命中股票和指数
            hits = store.search("000001")
            codes = {(h["code"], h["market"]) for h in hits}
            self.assertIn(("000001", "sz"), codes)
            self.assertIn(("000001", "sh"), codes)
            # 前缀 code
            prefix = store.search("6005")
            self.assertTrue(any(h["code"] == "600519" for h in prefix))
            self.assertTrue(any(h["code"] == "600549" for h in prefix))

    def test_search_by_name_substring(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            hits = store.search("平安")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["name"], "平安银行")
            # 上证 指数 都能子串命中
            self.assertEqual(len(store.search("上证")), 1)
            self.assertEqual(len(store.search("ETF")), 1)

    def test_search_by_pinyin_exact_and_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            exact = store.search("PAYH")
            self.assertEqual(len(exact), 1)
            self.assertEqual(exact[0]["name"], "平安银行")
            # 前缀
            prefix = store.search("PA")
            self.assertTrue(any(h["pinyin"] == "PAYH" for h in prefix))
            # 大小写不敏感
            self.assertEqual(len(store.search("xmwy")), 1)
            self.assertEqual(store.search("xmwy")[0]["name"], "厦门钨业")

    def test_search_empty_query_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            self.assertEqual(store.search(""), [])
            self.assertEqual(store.search("   "), [])

    def test_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            # 再 upsert 同一批（含一个改名的），条数不变，字段被更新
            records = _fixture_records()
            records[0] = {**records[0], "name": "平安银行股份"}
            store.upsert_many(records)
            got = store.get("000001", "sz")
            self.assertIsNotNone(got)
            self.assertEqual(got["name"], "平安银行股份")
            # 仍是 5 条（去重 by code+market）
            all_hits = store.search("0") + store.search("6") + store.search("5")
            unique = {(h["code"], h["market"]) for h in all_hits}
            self.assertEqual(len(unique), 5)

    def test_ensure_loaded_imports_on_empty_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            # 构造时已自动 ensure_loaded，应已导入
            self.assertTrue(store.search("PAYH"))

    def test_ensure_loaded_skips_when_populated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "securities_master.json"
            _write_json(json_path, _fixture_records())
            store = SecuritiesStore(Path(tmpdir) / "market_data.sqlite", json_path=json_path)
            # 用一个内容不同的 JSON 再构造，应跳过导入（表非空）
            other = [{"code": "300750", "market": "sz", "type": "stock", "name": "宁德时代", "pinyin": "NDSD"}]
            other_path = Path(tmpdir) / "other.json"
            _write_json(other_path, other)
            store2 = SecuritiesStore(Path(tmpdir) / "market_data.sqlite", json_path=other_path)
            self.assertIsNone(store2.get("300750", "sz"))  # 未被导入
            self.assertTrue(store2.search("PAYH"))  # 旧数据仍在

    def test_get_by_code_and_market(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            got = store.get("000001", "sh")
            self.assertIsNotNone(got)
            self.assertEqual(got["type"], "index")
            self.assertEqual(got["name"], "上证指数")
            # 不传 market 时返回 code 下的某一条
            any_hit = store.get("000001")
            self.assertIsNotNone(any_hit)
            self.assertEqual(any_hit["code"], "000001")

    def test_get_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            self.assertIsNone(store.get("999999", "sh"))
            self.assertIsNone(store.get("999999"))

    def test_search_tiebreak_on_market_is_deterministic(self):
        # 同 code 不同 market（000001 既是 sz 股票又是 sh 指数）在精确匹配桶内
        # 必须有稳定的二级排序，否则默认选中项随插入顺序 / VACUUM 漂移。
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._new_store(tmpdir, _fixture_records())
            hits = store.search("000001")
            self.assertEqual(len(hits), 2)
            # ORDER BY ... code, market -> sh 在 sz 之前，结果稳定可复现
            self.assertEqual([(h["code"], h["market"]) for h in hits],
                             [("000001", "sh"), ("000001", "sz")])
            # 重复查询结果一致（不依赖 rowid 顺序）
            self.assertEqual(
                [(h["code"], h["market"]) for h in store.search("000001")],
                [(h["code"], h["market"]) for h in store.search("000001")],
            )

    def test_ensure_loaded_warns_when_bundled_json_missing(self):
        # 表为空且 JSON 不存在时，必须打印告警，而不是静默留下空表。
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "does_not_exist.json"
            err = StringIO()
            with redirect_stderr(err):
                store = SecuritiesStore(Path(tmpdir) / "market_data.sqlite", json_path=json_path)
            self.assertEqual(store.search("000001"), [])
            self.assertIn("[WARN]", err.getvalue())
            self.assertIn("does_not_exist.json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
