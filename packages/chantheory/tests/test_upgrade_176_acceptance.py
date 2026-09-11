"""#176 acceptance-focused regressions (does not update formal fixtures)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from packages.chantheory import analyze, analyze_multi_timeframe
from packages.chantheory.config import DEFAULT_SIGNALS_CONFIG

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "spikes" / "0009-czsc-1.0.1-upgrade" / "baseline" / "inputs"


def _load(name: str):
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


class PlotPrimitivesContractTests(unittest.TestCase):
    def test_5m_plot_primitives_match_structure_endpoints(self):
        rows = _load("5m_real_600584_548_rows.json")
        result = analyze(
            rows=rows,
            symbol="600584.SH",
            timeframe="5m",
            source="tencent",
            signals_config=list(DEFAULT_SIGNALS_CONFIG),
            strict=True,
        )
        fractals = {item.id: item for item in result.fractals}
        strokes = {item.id: item for item in result.strokes}
        pending = result.meta.get("pending_stroke")
        if pending is not None:
            strokes[pending.id] = pending

        checked = 0
        for primitive in result.plot_primitives:
            meta = primitive.meta or {}
            ref_type = meta.get("reference_type")
            ref_id = meta.get("reference_id")
            if ref_type == "fractal" and ref_id in fractals:
                target = fractals[ref_id]
                self.assertEqual(primitive.x, target.timestamp)
                self.assertEqual(primitive.y, target.price)
                checked += 1
            elif ref_type == "stroke" and ref_id in strokes:
                target = strokes[ref_id]
                self.assertEqual(primitive.x1, target.start_timestamp)
                self.assertEqual(primitive.y1, target.start_price)
                self.assertEqual(primitive.x2, target.end_timestamp)
                self.assertEqual(primitive.y2, target.end_price)
                checked += 1
        self.assertGreater(checked, 0)

    def test_multi_timeframe_levels_match_independent_analyzes(self):
        rows_5m = _load("5m_real_600584_548_rows.json")
        rows_30m = _load("30m_600584_sh_rows.json")
        multi = analyze_multi_timeframe(
            rows_by_timeframe={"5m": rows_5m, "30m": rows_30m},
            symbol="600584.SH",
            base_timeframe="5m",
            source="tencent",
            signals_config=list(DEFAULT_SIGNALS_CONFIG),
            strict=True,
        )
        single_5m = analyze(
            rows=rows_5m,
            symbol="600584.SH",
            timeframe="5m",
            source="tencent",
            signals_config=list(DEFAULT_SIGNALS_CONFIG),
            strict=True,
        )
        single_30m = analyze(
            rows=rows_30m,
            symbol="600584.SH",
            timeframe="30m",
            source="tencent",
            signals_config=list(DEFAULT_SIGNALS_CONFIG),
            strict=True,
        )
        by_tf = {level.timeframe: level.analysis for level in multi.levels}
        self.assertEqual(by_tf["5m"].to_dict(), single_5m.to_dict())
        self.assertEqual(by_tf["30m"].to_dict(), single_30m.to_dict())


class BaselineCountSmokeTests(unittest.TestCase):
    def test_frozen_scenarios_produce_expected_structure_counts(self):
        # Counts must match #173/#175 observed equal counts under 1.0.1.
        cases = (
            ("daily_synthetic_120_rows.json", "000001.SZ", "day", "synthetic", 30, 9),
            ("5m_real_600584_548_rows.json", "600584.SH", "5m", "tencent", 38, 24),
            ("30m_600584_sh_rows.json", "600584.SH", "30m", "tencent", 417, 95),
        )
        for filename, symbol, timeframe, source, fractals, strokes in cases:
            with self.subTest(filename=filename):
                result = analyze(
                    rows=_load(filename),
                    symbol=symbol,
                    timeframe=timeframe,
                    source=source,
                    signals_config=list(DEFAULT_SIGNALS_CONFIG),
                    strict=True,
                )
                self.assertEqual(len(result.fractals), fractals)
                self.assertEqual(len(result.strokes), strokes)
                self.assertEqual(result.engine_version, "1.0.1")


if __name__ == "__main__":
    unittest.main()
