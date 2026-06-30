import importlib.util
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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
