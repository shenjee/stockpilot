import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata import build_securities_master  # noqa: E402

SHARED_JSON_PATH = ROOT / "packages" / "marketdata" / "securities_master.json"


def _hk_records(records: list[dict]) -> set[tuple[str, str, str, str, str]]:
    return {
        (item["code"], item["market"], item["type"], item["name"], item["pinyin"])
        for item in records
        if item.get("market") == "hk"
    }


def _expected_hk_records() -> set[tuple[str, str, str, str, str]]:
    return {
        (code, "hk", "stock", name, pinyin)
        for code, name, pinyin in build_securities_master.HK_STATIC_STOCKS
    }


class BuildSecuritiesMasterTests(unittest.TestCase):
    def test_build_outputs_hk_records_to_shared_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "securities_master.json"

            with patch.object(build_securities_master, "OUTPUT_PATH", output), patch.object(
                build_securities_master, "_collect_stocks", lambda records: None
            ), patch.object(
                build_securities_master, "_collect_etfs", lambda records: None
            ), patch.object(
                build_securities_master, "_collect_indices", lambda records: None
            ):
                build_securities_master._build()

            records = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(_hk_records(records), _expected_hk_records())
            self.assertEqual(len(records), len(build_securities_master.HK_STATIC_STOCKS))

    def test_build_has_no_legacy_output_path(self):
        self.assertFalse(hasattr(build_securities_master, "LEGACY_OUTPUT_PATH"))
        self.assertEqual(
            build_securities_master.OUTPUT_PATH,
            ROOT / "packages" / "marketdata" / "securities_master.json",
        )

    def test_committed_json_hk_records_match_static_source_of_truth(self):
        records = json.loads(SHARED_JSON_PATH.read_text(encoding="utf-8"))
        expected_sorted = sorted(records, key=lambda r: (r["type"], r["code"], r["market"]))

        self.assertEqual(_hk_records(records), _expected_hk_records())
        self.assertEqual(records, expected_sorted)


if __name__ == "__main__":
    unittest.main()
