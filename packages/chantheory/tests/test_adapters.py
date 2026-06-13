import sys
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.adapters import (
    _map_pending_stroke,
    _map_strokes,
    _normalize_direction,
    _normalize_fractal_type,
    _to_timestamp,
    analyze_tracker_klines,
    load_czsc,
)
from chantheory.schema import Stroke
from chantheory.segments import SEGMENT_MAPPING_STRATEGY


def _stroke(
    suffix: str,
    direction: str,
    start_timestamp: str,
    start_price: float,
    end_timestamp: str,
    end_price: float,
) -> Stroke:
    return Stroke(
        id=f"stroke_{suffix}",
        direction=direction,
        start_fractal_id=f"fractal_{suffix}_start",
        end_fractal_id=f"fractal_{suffix}_end",
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        start_price=start_price,
        end_price=end_price,
        confirmed=True,
    )


class AdapterTests(unittest.TestCase):
    def test_to_timestamp_normalizes_spacing_around_colons(self):
        self.assertEqual(_to_timestamp("2026-05-28 16:00 :00"), "2026-05-28 16:00:00")

    def test_normalize_fractal_type_supports_czsc_mark_enums(self):
        top_mark = SimpleNamespace(name="G", value="顶分型")
        bottom_mark = SimpleNamespace(name="D", value="底分型")

        self.assertEqual(_normalize_fractal_type(top_mark), "top")
        self.assertEqual(_normalize_fractal_type(bottom_mark), "bottom")
        self.assertEqual(_normalize_fractal_type("高"), "top")
        self.assertEqual(_normalize_fractal_type("低"), "bottom")

    def test_normalize_direction_supports_czsc_direction_enums(self):
        up_direction = SimpleNamespace(name="Up", value="向上")
        down_direction = SimpleNamespace(name="Down", value="向下")

        self.assertEqual(_normalize_direction(up_direction), "up")
        self.assertEqual(_normalize_direction(down_direction), "down")
        self.assertEqual(_normalize_direction("向上"), "up")
        self.assertEqual(_normalize_direction("向下"), "down")

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

    def test_load_czsc_prefers_pure_python_exports(self):
        top_raw_bar = object()
        top_freq = object()
        top_czsc = object()
        py_raw_bar = object()
        py_freq = object()
        py_czsc = object()
        czsc_module = SimpleNamespace(RawBar=top_raw_bar, Freq=top_freq, CZSC=top_czsc)
        py_objects_module = SimpleNamespace(RawBar=py_raw_bar, Freq=py_freq)
        py_analyze_module = SimpleNamespace(CZSC=py_czsc)

        def fake_import(name):
            if name == "numpy.typing":
                return object()
            if name == "czsc":
                return czsc_module
            if name == "czsc.py.objects":
                return py_objects_module
            if name == "czsc.py.analyze":
                return py_analyze_module
            raise ImportError(name)

        with patch("chantheory.adapters.import_module", side_effect=fake_import):
            actual_raw_bar, actual_freq, actual_czsc = load_czsc()

        self.assertIs(actual_raw_bar, py_raw_bar)
        self.assertIs(actual_freq, py_freq)
        self.assertIs(actual_czsc, py_czsc)

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

    def test_normalization_failure_returns_frozen_schema(self):
        result = analyze_tracker_klines(
            rows=[
                {"date": "2025-01-02", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000},
            ],
            code="000001",
            market="bad",
        )

        self.assertEqual(result.symbol, "000001.BAD")
        self.assertEqual(result.engine, "czsc")
        self.assertEqual(result.fractals, [])
        self.assertEqual(result.meta["engine_probe"]["status"], "skipped")
        self.assertTrue(any(item.warning_code == "NORMALIZATION_FAILED" for item in result.warnings))
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
        self.assertEqual(result.segments[0].meta["mapping_strategy"], SEGMENT_MAPPING_STRATEGY)
        self.assertFalse(result.segments[0].confirmed)
        self.assertEqual(result.segments[0].meta["status"], "pending")
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

    def test_map_strokes_repairs_adjacent_endpoint_gap(self):
        fx1 = SimpleNamespace(dt="2025-01-02", mark="D", fx=9.7)
        fx2 = SimpleNamespace(dt="2025-01-03", mark="G", fx=10.9)
        fx2_gap = SimpleNamespace(dt="2025-01-04", mark="G", fx=10.7)
        fx3 = SimpleNamespace(dt="2025-01-06", mark="D", fx=10.0)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Up", high=10.9, low=9.7)
        bi2 = SimpleNamespace(fx_a=fx2_gap, fx_b=fx3, direction="Down", high=10.7, low=10.0)
        analyzer = SimpleNamespace(finished_bis=[bi1, bi2])

        strokes = _map_strokes(analyzer)

        self.assertEqual(len(strokes), 2)
        self.assertEqual(strokes[1].start_timestamp, strokes[0].end_timestamp)
        self.assertEqual(strokes[1].start_price, strokes[0].end_price)
        self.assertEqual(strokes[1].start_fractal_id, strokes[0].end_fractal_id)
        self.assertTrue(strokes[1].meta["continuity_adjusted"])
        self.assertEqual(strokes[1].meta["original_start_timestamp"], "2025-01-04")
        self.assertEqual(strokes[1].meta["original_start_price"], 10.7)

    def test_map_pending_stroke_builds_unconfirmed_tail_from_ubi(self):
        fx1 = SimpleNamespace(dt="2025-01-02", mark="G", fx=10.9)
        fx2 = SimpleNamespace(dt="2025-01-03", mark="D", fx=9.7)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Down", high=10.9, low=9.7)
        strokes = _map_strokes(SimpleNamespace(finished_bis=[bi1]))
        high_bar = SimpleNamespace(dt="2025-01-07", high=11.2)
        ubi = {"fx_a": fx2, "direction": "Up", "high": 11.2, "high_bar": high_bar}

        pending = _map_pending_stroke(SimpleNamespace(ubi=ubi), strokes)

        self.assertIsNotNone(pending)
        self.assertFalse(pending.confirmed)
        self.assertEqual(pending.start_timestamp, strokes[-1].end_timestamp)
        self.assertEqual(pending.start_price, strokes[-1].end_price)
        self.assertEqual(pending.end_timestamp, "2025-01-07")
        self.assertEqual(pending.end_price, 11.2)
        self.assertTrue(pending.meta["pending"])

    def test_map_pending_stroke_alternates_after_previous_stroke(self):
        strokes = [
            _stroke("1", "down", "2026-06-11 13:30:00", 11.30, "2026-06-12 09:30:00", 10.88),
        ]
        fx = SimpleNamespace(dt="2026-06-12 09:30:00", mark="D", fx=10.88)
        high_bar = SimpleNamespace(dt="2026-06-12 14:30:00", high=11.24)
        low_bar = SimpleNamespace(dt="2026-06-11 15:00:00", low=11.31)
        ubi = {
            "fx_a": fx,
            "direction": "Down",
            "high": 11.24,
            "low": 11.31,
            "high_bar": high_bar,
            "low_bar": low_bar,
        }

        pending = _map_pending_stroke(SimpleNamespace(ubi=ubi), strokes)

        self.assertIsNotNone(pending)
        self.assertEqual(pending.direction, "up")
        self.assertEqual(pending.start_timestamp, "2026-06-12 09:30:00")
        self.assertEqual(pending.end_timestamp, "2026-06-12 14:30:00")
        self.assertEqual(pending.end_price, 11.24)
        self.assertEqual(pending.meta["raw_direction"], "Down")

    def test_map_pending_stroke_drops_non_forward_tail(self):
        strokes = [
            _stroke("1", "down", "2026-06-11 13:30:00", 11.30, "2026-06-12 09:30:00", 10.88),
        ]
        fx = SimpleNamespace(dt="2026-06-12 09:30:00", mark="D", fx=10.88)
        high_bar = SimpleNamespace(dt="2026-06-11 15:00:00", high=11.31)
        ubi = {"fx_a": fx, "direction": "Up", "high": 11.31, "high_bar": high_bar}

        self.assertIsNone(_map_pending_stroke(SimpleNamespace(ubi=ubi), strokes))

    def test_candidate_points_are_structure_only(self):
        rows = [
            {"date": "2025-01-01", "open": 10.0, "close": 10.4, "high": 10.5, "low": 9.9, "volume": 1000},
            {"date": "2025-01-02", "open": 10.4, "close": 9.8, "high": 10.6, "low": 9.7, "volume": 1200},
            {"date": "2025-01-03", "open": 9.8, "close": 10.8, "high": 10.9, "low": 9.6, "volume": 1500},
        ]
        fx1 = SimpleNamespace(dt="2025-01-01", mark="G", fx=10.5)
        fx2 = SimpleNamespace(dt="2025-01-02", mark="D", fx=9.7)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Down", high=10.5, low=9.7)
        analyzer = SimpleNamespace(fx_list=[fx1, fx2], ubi_fxs=[], finished_bis=[bi1], last_bi_extend=False)
        zs = SimpleNamespace(sdt="2025-01-01", edt="2025-01-02", zg=10.0, zd=9.5, gg=10.5, dd=9.5, zz=9.75)
        sig_module = SimpleNamespace(get_zs_seq=lambda bis: [zs])

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 3)), patch(
            "chantheory.adapters.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(rows=rows, code="000001", market="sz")

        self.assertEqual(len(result.candidate_buy_points), 1)
        candidate = result.candidate_buy_points[0]
        self.assertEqual(candidate.point_type, "structure_buy_candidate")
        self.assertEqual(candidate.meta["signal_scope"], "structure_candidate_only")

    def test_fixture_engine_version_matches_pin(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "p2_sample_result.json"
        payload = json.loads(fixture_path.read_text())

        self.assertEqual(payload["engine_version"], "0.10.12")
        self.assertEqual(payload["meta"]["engine_assumptions"]["engine_version"], "0.10.12")


if __name__ == "__main__":
    unittest.main()
