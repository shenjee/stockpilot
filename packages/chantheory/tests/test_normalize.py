import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.normalize import NormalizationError, build_symbol, normalize_tracker_klines


class NormalizeTests(unittest.TestCase):
    def test_build_symbol(self):
        self.assertEqual(build_symbol("000001", "sz"), "000001.SZ")
        self.assertEqual(build_symbol("600000", "sh"), "600000.SH")

    def test_tracker_normalization_sorts_and_derives_amount(self):
        rows = [
            {"date": "2025-01-03", "open": 10, "close": 11, "high": 11.5, "low": 9.8, "volume": 1200},
            {"date": "2025-01-02", "open": 9, "close": 10, "high": 10.1, "low": 8.9, "volume": 1000},
            {"date": "2025-01-03", "open": 10, "close": 11.2, "high": 11.6, "low": 9.7, "volume": 1300},
        ]

        result = normalize_tracker_klines(rows=rows, code="000001", market="sz")

        self.assertEqual(len(result.bars), 2)
        self.assertEqual(result.bars[0].timestamp, "2025-01-02")
        self.assertEqual(result.bars[1].close, 11.2)
        self.assertTrue(any(item.warning_code == "DUPLICATE_TIMESTAMP" for item in result.warnings))
        self.assertTrue(any(item.warning_code == "AMOUNT_DERIVED" for item in result.warnings))

    def test_invalid_price_geometry_raises(self):
        rows = [
            {"date": "2025-01-02", "open": 10, "close": 12, "high": 11, "low": 9, "volume": 1000},
        ]

        with self.assertRaises(NormalizationError):
            normalize_tracker_klines(rows=rows, code="000001", market="sz")


if __name__ == "__main__":
    unittest.main()
