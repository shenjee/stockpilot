import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "skills" / "china-stock-analysis" / "scripts" / "build_securities_master.py"
SHARED_JSON_PATH = ROOT / "packages" / "marketdata" / "securities_master.json"
LEGACY_JSON_PATH = ROOT / "skills" / "china-stock-analysis" / "scripts" / "securities_master.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _hk_records(records: list[dict]) -> set[tuple[str, str, str, str, str]]:
    return {
        (item["code"], item["market"], item["type"], item["name"], item["pinyin"])
        for item in records
        if item.get("market") == "hk"
    }


class BuildSecuritiesMasterTests(unittest.TestCase):
    def test_build_outputs_hk_records_to_shared_and_legacy_paths(self):
        module = _load_module("test_build_securities_master_module", SCRIPT_PATH)

        with tempfile.TemporaryDirectory() as tmpdir:
            shared_output = Path(tmpdir) / "shared_securities_master.json"
            legacy_output = Path(tmpdir) / "legacy_securities_master.json"

            with patch.object(module, "OUTPUT_PATH", shared_output), patch.object(
                module, "LEGACY_OUTPUT_PATH", legacy_output
            ), patch.object(module, "_collect_stocks", lambda records: None), patch.object(
                module, "_collect_etfs", lambda records: None
            ), patch.object(
                module, "_collect_indices", lambda records: None
            ):
                module._build()

            shared_records = json.loads(shared_output.read_text(encoding="utf-8"))
            legacy_records = json.loads(legacy_output.read_text(encoding="utf-8"))
            expected_hk_records = {
                (code, "hk", "stock", name, pinyin) for code, name, pinyin in module.HK_STATIC_STOCKS
            }

            self.assertEqual(shared_records, legacy_records)
            self.assertEqual(_hk_records(shared_records), expected_hk_records)

    def test_committed_json_hk_records_match_static_source_of_truth(self):
        module = _load_module("test_build_securities_master_committed_module", SCRIPT_PATH)
        expected_hk_records = {
            (code, "hk", "stock", name, pinyin) for code, name, pinyin in module.HK_STATIC_STOCKS
        }

        shared_records = json.loads(SHARED_JSON_PATH.read_text(encoding="utf-8"))
        legacy_records = json.loads(LEGACY_JSON_PATH.read_text(encoding="utf-8"))
        expected_shared_sorted = sorted(shared_records, key=lambda r: (r["type"], r["code"], r["market"]))
        expected_legacy_sorted = sorted(legacy_records, key=lambda r: (r["type"], r["code"], r["market"]))

        self.assertEqual(_hk_records(shared_records), expected_hk_records)
        self.assertEqual(_hk_records(legacy_records), expected_hk_records)
        self.assertEqual(shared_records, expected_shared_sorted)
        self.assertEqual(legacy_records, expected_legacy_sorted)
        self.assertEqual(shared_records, legacy_records)


if __name__ == "__main__":
    unittest.main()
