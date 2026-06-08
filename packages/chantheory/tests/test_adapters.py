import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.adapters import _to_timestamp, analyze_tracker_klines, load_czsc


class AdapterTests(unittest.TestCase):
    def test_to_timestamp_normalizes_spacing_around_colons(self):
        self.assertEqual(_to_timestamp("2026-05-28 16:00 :00"), "2026-05-28 16:00:00")

    def test_load_czsc_supports_top_level_exports(self):
        RawBar = object()
        Freq = object()
        CZSC = object()
        czsc_module = SimpleNamespace(RawBar=RawBar, Freq=Freq, CZSC=CZSC)
        core_module = SimpleNamespace(RawBar=RawBar, Freq=Freq, CZSC=CZSC)

        def fake_import(name):
            if name == "numpy.typing":
                return object()
            if name == "czsc":
                return czsc_module
            if name == "czsc.core":
                return core_module
            raise ImportError(name)

        with patch("chantheory.adapters.import_module", side_effect=fake_import):
            actual_raw_bar, actual_freq, actual_czsc = load_czsc()

        self.assertIs(actual_raw_bar, RawBar)
        self.assertIs(actual_freq, Freq)
        self.assertIs(actual_czsc, CZSC)

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

    def test_p2_mapping_populates_schema_and_plot_primitives(self):
        rows = [
            {"date": "2025-01-01", "open": 10.0, "close": 10.4, "high": 10.5, "low": 9.9, "volume": 1000},
            {"date": "2025-01-02", "open": 10.4, "close": 9.8, "high": 10.6, "low": 9.7, "volume": 1200},
            {"date": "2025-01-03", "open": 9.8, "close": 10.8, "high": 10.9, "low": 9.6, "volume": 1500},
            {"date": "2025-01-06", "open": 10.8, "close": 10.1, "high": 11.0, "low": 10.0, "volume": 900},
            {"date": "2025-01-07", "open": 10.1, "close": 11.2, "high": 11.3, "low": 10.0, "volume": 1600},
        ]

        fx1 = SimpleNamespace(dt="2025-01-02", mark="D", fx=9.7)
        fx2 = SimpleNamespace(dt="2025-01-03", mark="G", fx=10.9)
        fx3 = SimpleNamespace(dt="2025-01-06", mark="D", fx=10.0)
        fx4 = SimpleNamespace(dt="2025-01-07", mark="G", fx=11.3)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Up", high=10.9, low=9.7)
        bi2 = SimpleNamespace(fx_a=fx2, fx_b=fx3, direction="Down", high=10.9, low=10.0)
        bi3 = SimpleNamespace(fx_a=fx3, fx_b=fx4, direction="Up", high=11.3, low=10.0)
        analyzer = SimpleNamespace(
            fx_list=[fx1, fx2, fx3, fx4],
            ubi_fxs=[fx4],
            finished_bis=[bi1, bi2, bi3],
            last_bi_extend=True,
        )
        zs = SimpleNamespace(sdt="2025-01-02", edt="2025-01-07", zg=11.0, zd=10.0, gg=11.3, dd=9.7, zz=10.5)
        sig_module = SimpleNamespace(get_zs_seq=lambda bis: [zs])

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 5)), patch(
            "chantheory.adapters.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(rows=rows, code="000001", market="sz", parameters={"min_bars": 10})

        self.assertEqual(result.symbol, "000001.SZ")
        self.assertEqual(len(result.fractals), 4)
        self.assertEqual(len(result.strokes), 3)
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(len(result.pivot_zones), 1)
        self.assertEqual(result.segments[0].meta["mapping_strategy"], "conservative_three_stroke_window")
        self.assertFalse(result.fractals[-1].confirmed)
        self.assertTrue(any(item.type == "line" and item.layer == "strokes" for item in result.plot_primitives))
        self.assertTrue(any(item.type == "box" and item.layer == "pivot_zones" for item in result.plot_primitives))
        active_pivot_alert = next(item for item in result.structure_alerts if item.alert_type == "active_pivot_zone")
        self.assertEqual(active_pivot_alert.meta["latest_stroke_position"], "above")
        self.assertIn("10.00-11.00", active_pivot_alert.message)
        self.assertIn("above the zone at 11.30", active_pivot_alert.message)
        self.assertTrue(any(item.warning_code == "INSUFFICIENT_BARS" for item in result.warnings))
        self.assertTrue(any(item.warning_code == "UNSTABLE_TAIL_STROKE" for item in result.warnings))
        self.assertTrue(result.summary)


if __name__ == "__main__":
    unittest.main()
