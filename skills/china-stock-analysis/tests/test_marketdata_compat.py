import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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

    def _run_standalone(self, name):
        with tempfile.TemporaryDirectory() as tmpdir:
            standalone_scripts = Path(tmpdir) / "china-stock-analysis" / "scripts"
            shutil.copytree(SCRIPTS_DIR, standalone_scripts)
            checks = Path(tmpdir) / "standalone_marketdata_checks.py"
            shutil.copyfile(Path(__file__).with_name(checks.name), checks)
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(checks), str(standalone_scripts),
                 f"StandaloneChecks.{name}"],
                cwd=tmpdir, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrappers_fall_back_in_standalone_skill_layout(self):
        self._run_standalone("test_wrappers_fall_back_in_standalone_skill_layout")

    def test_standalone_result_contract_smoke(self):
        self._run_standalone("test_standalone_result_contract_smoke")

    def test_standalone_kline_service_uses_gap_fill_and_provider_queue(self):
        self._run_standalone("test_standalone_kline_service_uses_gap_fill_and_provider_queue")

    def test_standalone_kline_top_level_list_does_not_raise(self):
        self._run_standalone("test_standalone_kline_top_level_list_does_not_raise")

    def test_standalone_hk_realtime_prefixed_input_is_normalized(self):
        self._run_standalone("test_standalone_hk_realtime_prefixed_input_is_normalized")

    def test_standalone_hk_kline_and_minute_use_normalized_codes(self):
        self._run_standalone("test_standalone_hk_kline_and_minute_use_normalized_codes")

    def test_standalone_securities_store_can_search_hk_records(self):
        self._run_standalone("test_standalone_securities_store_can_search_hk_records")

    def test_standalone_securities_store_syncs_bundled_json_into_populated_db(self):
        self._run_standalone("test_standalone_securities_store_syncs_bundled_json_into_populated_db")


if __name__ == "__main__":
    unittest.main()
