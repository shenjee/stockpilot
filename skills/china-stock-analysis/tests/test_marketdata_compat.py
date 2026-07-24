import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "skills" / "china-stock-analysis" / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(SCRIPTS_DIR))

from marketdata.market_data import TencentStockDataProvider as SharedTencentStockDataProvider
from marketdata.repositories.kline_store import KLineStore as SharedKLineStore
from marketdata.repositories.kline_store import resolve_market_data_db_path as shared_resolve_market_data_db_path
from marketdata.repositories.securities_store import SecuritiesStore as SharedSecuritiesStore
from marketdata.runtime_paths import RuntimePaths as SharedRuntimePaths
from marketdata.services.kline_data_service import KLineDataService as SharedKLineDataService
from market_data import TencentStockDataProvider
from repositories.kline_store import KLineStore, resolve_market_data_db_path
from repositories.securities_store import SecuritiesStore
from runtime_paths import RuntimePaths
from services.kline_data_service import KLineDataService


class MarketdataCompatibilityTests(unittest.TestCase):
    def test_wrappers_re_export_shared_symbols(self):
        self.assertIs(TencentStockDataProvider, SharedTencentStockDataProvider)
        self.assertIs(KLineStore, SharedKLineStore)
        self.assertIs(SecuritiesStore, SharedSecuritiesStore)
        self.assertIs(RuntimePaths, SharedRuntimePaths)
        self.assertIs(KLineDataService, SharedKLineDataService)
        self.assertIs(resolve_market_data_db_path, shared_resolve_market_data_db_path)

    def test_wrappers_fall_back_in_standalone_skill_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                market_module = _load_module(
                    "standalone_market_data_wrapper",
                    standalone_scripts / "market_data.py",
                )
                securities_module = _load_module(
                    "standalone_securities_store_wrapper",
                    standalone_scripts / "repositories" / "securities_store.py",
                )

                self.assertTrue(hasattr(market_module, "TencentStockDataProvider"))
                self.assertTrue(
                    market_module.TencentStockDataProvider.__module__.startswith("_standalone_marketdata")
                )

                with tempfile.TemporaryDirectory() as db_tmpdir:
                    store = securities_module.SecuritiesStore(Path(db_tmpdir) / "market_data.sqlite")
                    self.assertTrue(store.search("000001"))
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_result_contract_smoke(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.market_data import TencentStockDataProvider as StandaloneTencent
                from _standalone_marketdata.provider_result import MarketDataResult as StandaloneResult

                with patch.object(StandaloneTencent, "_fetch_with_retry", return_value=""):
                    result = StandaloneTencent.realtime_result("600519", markets=["sh"])

                self.assertIsInstance(result, StandaloneResult)
                self.assertTrue(result.success)
                self.assertEqual(result.data, [])
                self.assertTrue(result.warnings())
                self.assertEqual(result.warnings()[0].reason_code, "no_data")
                self.assertTrue(hasattr(StandaloneTencent, "get_kline_result"))
                self.assertTrue(hasattr(StandaloneTencent, "get_minute_kline_result"))
                self.assertTrue(hasattr(StandaloneTencent, "get_daily_quote_result"))
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_kline_service_uses_gap_fill_and_provider_queue(self):
        from _standalone_marketdata.repositories.kline_store import (
            KLineStore as StandaloneKLineStore,
        )
        from _standalone_marketdata.services.kline_data_service import (
            KLineDataService as StandaloneKLineDataService,
        )

        class FakeProvider:
            provider_id = "standalone-fake"

            def __init__(self):
                self.calls = []

            def get_kline(
                self,
                code,
                start_date,
                end_date,
                ktype="day",
                autype="qfq",
                market=None,
                security_type=None,
            ):
                self.calls.append(
                    (code, start_date, end_date, ktype, market, security_type)
                )
                return [
                    {
                        "date": "2026-01-06",
                        "open": 10.0,
                        "close": 10.1,
                        "high": 10.2,
                        "low": 9.9,
                        "volume": 100,
                    }
                ]

        def row(day):
            return {
                "date": day,
                "open": 10.0,
                "close": 10.1,
                "high": 10.2,
                "low": 9.9,
                "volume": 100,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = StandaloneKLineStore(Path(tmpdir) / "market_data.sqlite")
            store.upsert_many(
                "600519",
                "sh",
                [row("2026-01-05"), row("2026-01-07")],
                source="local",
            )
            provider = FakeProvider()
            service = StandaloneKLineDataService(provider, store)

            service.ensure_local_klines(
                code="600519",
                market="sh",
                start_date="2026-01-05",
                end_date="2026-01-07",
            )

            self.assertIsNotNone(service.provider_queue)
            self.assertEqual(
                provider.calls,
                [("600519", "2026-01-06", "2026-01-06", "day", "sh", None)],
            )

    def test_standalone_kline_top_level_list_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.market_data import TencentStockDataProvider as StandaloneTencent

                with patch.object(StandaloneTencent, "_fetch_with_retry", return_value="[]"):
                    day_result = StandaloneTencent.get_kline_result(
                        code="600519",
                        market="sh",
                        start_date="2026-06-11",
                        end_date="2026-06-11",
                        ktype="day",
                    )
                    minute_result = StandaloneTencent.get_minute_kline_result(
                        code="600519",
                        market="sh",
                        start_date="2026-06-11",
                        end_date="2026-06-11",
                        ktype="1m",
                    )

                self.assertFalse(day_result.success)
                self.assertEqual(day_result.errors()[0].reason_code, "unexpected_response_shape")
                self.assertFalse(minute_result.success)
                self.assertEqual(minute_result.errors()[0].reason_code, "unexpected_response_shape")
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_hk_realtime_prefixed_input_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.market_data import TencentStockDataProvider as StandaloneTencent

                payload = 'v_hk00175="100~吉利汽车~00175~19.390~18.400~18.640~57727595.0~0~0";'
                with patch.object(StandaloneTencent, "_fetch_with_retry", return_value=payload) as fetch:
                    result = StandaloneTencent.realtime_result("hk0175")

                self.assertTrue(result.success)
                self.assertIn("hk00175", fetch.call_args.args[0])
                self.assertNotIn("hk0175", fetch.call_args.args[0])
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_hk_kline_and_minute_use_normalized_codes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.market_data import TencentStockDataProvider as StandaloneTencent

                kline_payload = {
                    "code": 0,
                    "data": {
                        "hk00175": {
                            "day": [
                                ["2026-07-16", "18.500", "18.260", "18.760", "18.240", "87042911.000"],
                            ],
                        }
                    },
                }
                with patch.object(
                    StandaloneTencent, "_fetch_with_retry", return_value=json.dumps(kline_payload)
                ) as fetch:
                    result = StandaloneTencent.get_kline_result(
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
                self.assertIn("hk00175", fetch.call_args.args[0])

                minute_payload = {
                    "code": 0,
                    "data": {
                        "hk03896": {
                            "m1": [
                                ["202607161500", "5.10", "5.20", "5.25", "5.05", "1000.00"],
                            ],
                        }
                    },
                }
                with patch.object(
                    StandaloneTencent, "_fetch_with_retry", return_value=json.dumps(minute_payload)
                ) as fetch:
                    minute_result = StandaloneTencent.get_minute_kline_result(
                        code="3896",
                        market="hk",
                        start_date="2026-07-16",
                        end_date="2026-07-16",
                        ktype="1m",
                    )
                self.assertTrue(minute_result.success)
                self.assertEqual(len(minute_result.data), 1)
                self.assertEqual(minute_result.data[0]["close"], 5.20)
                self.assertIn("hk03896", fetch.call_args.args[0])
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_securities_store_can_search_hk_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.repositories.securities_store import SecuritiesStore as StandaloneStore

                with tempfile.TemporaryDirectory() as db_tmpdir:
                    store = StandaloneStore(Path(db_tmpdir) / "market_data.sqlite")
                    code_hits = store.search("00700")
                    name_hits = store.search("腾讯控股")

                self.assertTrue(any(item["market"] == "hk" and item["code"] == "00700" for item in code_hits))
                self.assertTrue(any(item["market"] == "hk" and item["name"] == "腾讯控股" for item in name_hits))
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)

    def test_standalone_securities_store_syncs_bundled_json_into_populated_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_root = Path(tmpdir) / "china-stock-analysis"
            standalone_scripts = standalone_root / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)

            saved_sys_path = list(sys.path)
            saved_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "marketdata"
                or name.startswith("marketdata.")
                or name == "_standalone_marketdata"
                or name.startswith("_standalone_marketdata.")
            }
            original_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                sys.path = [str(standalone_scripts)] + [
                    path
                    for path in sys.path
                    if path
                    not in {"", str(ROOT), str(ROOT / "packages"), str(SCRIPTS_DIR), str(original_cwd)}
                ]
                for name in list(saved_modules):
                    sys.modules.pop(name, None)

                from _standalone_marketdata.repositories.securities_store import SecuritiesStore as StandaloneStore

                with tempfile.TemporaryDirectory() as db_tmpdir:
                    db_path = Path(db_tmpdir) / "market_data.sqlite"
                    legacy_json = Path(db_tmpdir) / "legacy_securities_master.json"
                    legacy_json.write_text(
                        json.dumps(
                            [
                                {
                                    "code": "600519",
                                    "market": "sh",
                                    "type": "stock",
                                    "name": "贵州茅台",
                                    "pinyin": "GZMT",
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    legacy_store = StandaloneStore(db_path, json_path=legacy_json)
                    self.assertIsNotNone(legacy_store.get("600519", "sh"))
                    self.assertIsNone(legacy_store.get("00700", "hk"))

                    upgraded_store = StandaloneStore(db_path)
                    self.assertIsNotNone(upgraded_store.get("600519", "sh"))
                    hk_hit = upgraded_store.get("00700", "hk")
                    self.assertIsNotNone(hk_hit)
                    self.assertEqual(hk_hit["name"], "腾讯控股")
                    self.assertTrue(any(item["market"] == "hk" for item in upgraded_store.search("腾讯控股")))
            finally:
                os.chdir(original_cwd)
                sys.path = saved_sys_path
                for name in list(sys.modules):
                    if name == "_standalone_marketdata" or name.startswith("_standalone_marketdata."):
                        sys.modules.pop(name, None)
                sys.modules.update(saved_modules)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
