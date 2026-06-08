import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.adapters import analyze_tracker_klines


class AdapterTests(unittest.TestCase):
    def test_engine_failure_returns_frozen_schema(self):
        rows = [
            {"date": "2025-01-02", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000},
            {"date": "2025-01-03", "open": 11, "close": 11.4, "high": 11.5, "low": 10.9, "volume": 1200},
        ]

        with patch("chantheory.adapters.load_czsc", side_effect=ImportError("czsc not installed")):
            result = analyze_tracker_klines(rows=rows, code="000001", market="sz")

        self.assertEqual(result.symbol, "000001.SZ")
        self.assertEqual(result.engine, "czsc")
        self.assertEqual(result.fractals, [])
        self.assertEqual(result.strokes, [])
        self.assertTrue(any(item.warning_code == "ENGINE_PROBE_FAILED" for item in result.warnings))
        self.assertTrue(result.summary)


if __name__ == "__main__":
    unittest.main()
