import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.normalize import (
    NormalizationError, build_symbol, normalize_ohlcv_rows, normalize_tracker_klines,
)


class NormalizeTests(unittest.TestCase):
    def row(self, date="2026-09-23", **values):
        return dict(date=date, open=10, close=10.5, high=11, low=9, volume=100, **values)

    def normalize(self, rows, strict=True):
        return normalize_ohlcv_rows(
            rows, symbol="000001.SZ", timeframe="day", source="test", strict=strict,
        )

    def assert_invalid_row(self, row, message):
        valid = self.row("2026-09-24")
        with self.assertRaisesRegex(NormalizationError, message):
            self.normalize([row, valid])
        result = self.normalize([row, valid], strict=False)
        self.assertEqual([bar.timestamp for bar in result.bars], ["2026-09-24"])
        warnings = [w for w in result.warnings if w.warning_code == "INVALID_BAR"]
        self.assertEqual(len(warnings), 1)
        self.assertRegex(warnings[0].message, message)

    def test_nonfinite_numeric_fields_and_aliases(self):
        for field in ("open", "close", "high", "low", "volume", "vol", "amount", "turnover"):
            for value in (float("nan"), float("inf"), -float("inf"), "NaN", "inf", "-inf"):
                with self.subTest(field=field, value=value):
                    row = self.row()
                    if field == "vol":
                        del row["volume"]
                    row[field] = value
                    self.assert_invalid_row(row, f"{field} must be finite")

    def test_numeric_conversion_overflow_is_record_error(self):
        row = self.row()
        row["volume"] = 10 ** 400
        self.assert_invalid_row(row, "Invalid numeric value for volume")

    def test_derived_amount_overflow(self):
        row = self.row()
        row.update(open=1e308, close=1e308, high=1e308, low=1e308, volume=2)
        self.assert_invalid_row(row, "Derived amount must be finite")

    def test_invalid_or_missing_timestamps(self):
        for value in (None, "", "not-a-date", "2026-02-30", 123):
            with self.subTest(value=value):
                self.assert_invalid_row(self.row(value), "timestamp")
        row = self.row()
        del row["date"]
        self.assert_invalid_row(row, "timestamp")

    def test_all_invalid_and_empty_inputs(self):
        for rows in ([], [self.row(None)], [dict(self.row(), high="inf")],
                     [self.row(None), dict(self.row(), amount="NaN")]):
            with self.subTest(rows=rows):
                with self.assertRaises(NormalizationError):
                    self.normalize(rows)
                result = self.normalize(rows, strict=False)
                self.assertEqual(result.bars, [])
                self.assertEqual(len(result.warnings), len(rows))

    def test_lenient_sorting_indices_and_distinct_warning_ids(self):
        result = self.normalize([
            self.row(None), self.row("2026-09-25"),
            dict(self.row(), high="inf"), self.row("2026-09-24"),
        ], strict=False)
        self.assertEqual([b.timestamp for b in result.bars], ["2026-09-24", "2026-09-25"])
        # Preserve the sorted/deduplicated positions, including numeric-invalid rows.
        self.assertEqual([b.bar_index for b in result.bars], [1, 2])
        ids = [w.id for w in result.warnings]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(w.warning_code == "AMOUNT_DERIVED" for w in result.warnings), 1)

    def test_duplicate_last_row_wins_before_numeric_validation(self):
        valid = self.row()
        invalid = dict(valid, high="inf")
        for strict in (True, False):
            with self.subTest(strict=strict):
                result = self.normalize([invalid, valid], strict=strict)
                self.assertEqual(len(result.bars), 1)
                self.assertEqual([w.warning_code for w in result.warnings],
                                 ["DUPLICATE_TIMESTAMP", "AMOUNT_DERIVED"])
        with self.assertRaises(NormalizationError):
            self.normalize([valid, invalid])
        result = self.normalize([valid, invalid], strict=False)
        self.assertEqual(result.bars, [])
        self.assertEqual([w.warning_code for w in result.warnings],
                         ["DUPLICATE_TIMESTAMP", "INVALID_BAR"])

    def test_timestamp_aliases_are_normalized_before_deduplication(self):
        first = dict(self.row(), timestamp="2026-09-23T10:00:00")
        last = dict(self.row(), datetime="2026-09-23 10:00:00", amount="1050")
        result = self.normalize([first, last])
        self.assertEqual(len(result.bars), 1)
        self.assertEqual(result.bars[0].timestamp, "2026-09-23 10:00:00")
        self.assertEqual(result.bars[0].amount, 1050)
        self.assertFalse(result.bars[0].meta["amount_derived"])

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
