import json
import os
import subprocess
import sys
import sysconfig
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


def _run_without_site_packages(module: str, argv: list[str], pythonpath: list[str]) -> subprocess.CompletedProcess:
    """Run ``python -S -m module argv`` from the repository root with site-packages
    disabled, as in a checkout that has no editable install: ``-S`` skips ``.pth``
    files, so ``packages`` is importable only through the ``sys.path[0]`` entry
    that ``-m`` sets up from the current directory."""

    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env["PYTHONPATH"] = os.pathsep.join([sysconfig.get_paths()["purelib"], *pythonpath])
    return subprocess.run(
        [sys.executable, "-S", "-m", module, *argv],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


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

    def test_documented_command_imports_without_editable_install(self):
        """The documented ``python -m`` entry point must work in a checkout that has
        no editable install, so ``packages`` cannot be resolved through a ``.pth`` file.

        A shim module is run with the real ``-m`` mechanism; it stubs the network
        collectors and redirects the output, then calls the real ``_build()``. This
        exercises the real import path without touching AkShare or the committed JSON.
        """

        shim = (
            "import sys\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "from packages.marketdata import build_securities_master as m\n"
            "with patch.object(m, 'OUTPUT_PATH', Path(sys.argv[1])), \\\n"
            "        patch.object(m, '_collect_stocks', lambda records: None), \\\n"
            "        patch.object(m, '_collect_etfs', lambda records: None), \\\n"
            "        patch.object(m, '_collect_indices', lambda records: None):\n"
            "    m._build()\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            shim_dir = Path(tmpdir) / "shim"
            shim_dir.mkdir()
            (shim_dir / "_offline_build.py").write_text(shim, encoding="utf-8")
            output = Path(tmpdir) / "securities_master.json"

            result = _run_without_site_packages("_offline_build", [str(output)], [str(shim_dir)])

            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(_hk_records(records), _expected_hk_records())

    def test_direct_script_path_fails_without_editable_install(self):
        """Guards the reason the documented command uses ``-m``: running the file
        directly puts only ``packages/marketdata/`` on ``sys.path``, so the
        top-level ``from packages.marketdata...`` import fails in an uninstalled
        checkout. If this starts passing, the ``-m`` requirement can be relaxed."""

        with tempfile.TemporaryDirectory() as tmpdir:
            env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
            env["PYTHONPATH"] = sysconfig.get_paths()["purelib"]
            result = subprocess.run(
                [sys.executable, "-S", str(ROOT / "packages" / "marketdata" / "build_securities_master.py")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No module named 'packages'", result.stderr)


if __name__ == "__main__":
    unittest.main()
