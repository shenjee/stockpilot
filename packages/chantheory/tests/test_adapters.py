import sys
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.adapters import (
    _build_candidate_points,
    _build_candidate_point_events,
    _map_divergences,
    _build_signal_payloads,
    _map_pending_stroke,
    _map_strokes,
    _normalize_signals_config,
    analyze_multi_timeframe_tracker_klines,
    analyze_tracker_klines,
)
from chantheory.engine import load_czsc
from chantheory.structure_mapping import (
    map_segment_pivot_zones,
    normalize_direction as _normalize_direction,
    normalize_fractal_type as _normalize_fractal_type,
    to_timestamp as _to_timestamp,
)
from chantheory.schema import AnalysisResult, AnalysisWarning, Segment, Stroke
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


def _segment(
    suffix: str,
    direction: str,
    start_timestamp: str,
    start_price: float,
    end_timestamp: str,
    end_price: float,
    confirmed: bool = True,
) -> Segment:
    return Segment(
        id=f"segment_{suffix}",
        direction=direction,
        stroke_ids=[f"stroke_{suffix}_1", f"stroke_{suffix}_2", f"stroke_{suffix}_3"],
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        start_price=start_price,
        end_price=end_price,
        confirmed=confirmed,
    )


def _analysis_result(timeframe: str, bar_count: int, warning_code: str = "") -> AnalysisResult:
    warnings = []
    if warning_code:
        warnings.append(
            AnalysisWarning(
                id=f"warning_{timeframe}_{warning_code.lower()}",
                warning_code=warning_code,
                severity="warning",
                message=f"{timeframe} warning",
                field="bars",
            )
        )
    result = AnalysisResult(
        symbol="000001.SZ",
        timeframe=timeframe,
        source="tencent",
        engine="czsc",
        engine_version="0.10.12",
        parameters={"max_bi_num": 50},
        warnings=warnings,
        meta={"bar_count": bar_count, "engine_probe": {"status": "ok"}},
    )
    result.summary = [f"{timeframe} summary"]
    return result


class AdapterTests(unittest.TestCase):
    def test_map_segment_pivot_zones_builds_segment_level_zone(self):
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
        ]

        zones = map_segment_pivot_zones(segments)

        self.assertEqual(len(zones), 1)
        zone = zones[0]
        self.assertEqual(zone.level, "segment")
        self.assertEqual(zone.low, 12.0)
        self.assertEqual(zone.high, 18.0)
        self.assertEqual(zone.segment_ids, ["segment_1", "segment_2", "segment_3"])
        self.assertTrue(zone.active)
        self.assertEqual(zone.meta["core_segment_count"], 3)
        self.assertEqual(zone.meta["extension_segment_count"], 0)

    def test_map_segment_pivot_zones_skips_non_overlapping_segments(self):
        segments = [
            _segment("1", "down", "2025-01-01", 30.0, "2025-01-02", 20.0),
            _segment("2", "up", "2025-01-02", 20.0, "2025-01-03", 25.0),
            _segment("3", "down", "2025-01-03", 19.0, "2025-01-04", 10.0),
        ]

        self.assertEqual(map_segment_pivot_zones(segments), [])

    def test_map_segment_pivot_zones_extends_and_consumes_segments(self):
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
            _segment("4", "up", "2025-01-04", 12.0, "2025-01-05", 19.0),
            _segment("5", "down", "2025-01-05", 19.0, "2025-01-06", 18.5),
        ]

        zones = map_segment_pivot_zones(segments)

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].segment_ids, ["segment_1", "segment_2", "segment_3", "segment_4"])
        self.assertEqual(zones[0].meta["extension_segment_ids"], ["segment_4"])
        self.assertFalse(zones[0].active)
        self.assertEqual(zones[0].meta["leave_segment_id"], "segment_5")

    def test_map_segment_pivot_zones_pending_leave_keeps_active(self):
        # 离开段为 pending（未确认）时，active 应保持 True。
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
            _segment("4", "up", "2025-01-04", 12.0, "2025-01-05", 19.0),
            _segment("5", "down", "2025-01-05", 19.0, "2025-01-06", 18.5),
        ]
        segments[4].meta["status"] = "pending"
        segments[4].confirmed = False

        zones = map_segment_pivot_zones(segments)

        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].meta["leave_segment_id"], "segment_5")
        self.assertEqual(zones[0].meta["leave_segment_status"], "pending")
        self.assertTrue(zones[0].active, "pending 离开段不应让 active=False")

    def test_map_segment_pivot_zones_excludes_growing_segments(self):
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
        ]
        segments[2].meta["status"] = "growing"

        self.assertEqual(map_segment_pivot_zones(segments), [])

    def test_map_segment_pivot_zones_includes_pending_segments_with_flag(self):
        # pending 段（方向已确定，端点未确认）应参与中枢构造，
        # 且 meta.contains_pending_segments=True 标记中枢不稳定。
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
        ]
        segments[2].meta["status"] = "pending"
        segments[2].confirmed = False

        zones = map_segment_pivot_zones(segments)

        self.assertEqual(len(zones), 1)
        self.assertTrue(zones[0].meta["contains_pending_segments"])

    def test_map_segment_pivot_zones_all_confirmed_has_no_pending_flag(self):
        segments = [
            _segment("1", "down", "2025-01-01", 20.0, "2025-01-02", 10.0),
            _segment("2", "up", "2025-01-02", 10.0, "2025-01-03", 18.0),
            _segment("3", "down", "2025-01-03", 18.0, "2025-01-04", 12.0),
        ]
        for seg in segments:
            seg.meta["status"] = "confirmed"
            seg.confirmed = True

        zones = map_segment_pivot_zones(segments)

        self.assertEqual(len(zones), 1)
        self.assertFalse(zones[0].meta["contains_pending_segments"])

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

        with patch("chantheory.engine.import_module", side_effect=fake_import):
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

        with patch("chantheory.engine.import_module", side_effect=fake_import):
            actual_raw_bar, actual_freq, actual_czsc = load_czsc()

        self.assertIs(actual_raw_bar, py_raw_bar)
        self.assertIs(actual_freq, py_freq)
        self.assertIs(actual_czsc, py_czsc)

    def test_engine_failure_returns_frozen_schema(self):
        rows = [
            {"date": "2025-01-02", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000},
            {"date": "2025-01-03", "open": 11, "close": 11.4, "high": 11.5, "low": 10.9, "volume": 1200},
        ]

        with patch("chantheory.engine.load_czsc", side_effect=ImportError("czsc not installed")):
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

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 3)), patch(
            "chantheory.engine.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(
                rows=rows,
                code="000001",
                market="sz",
                parameters={"min_bars": 10},
                signals_config=[],
            )

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

    def test_map_divergences_detects_bullish_and_bearish_cases(self):
        bearish_strokes = [
            _stroke("1", "up", "2025-01-02", 10.0, "2025-01-03", 13.0),
            _stroke("2", "down", "2025-01-03", 13.0, "2025-01-06", 10.8),
            _stroke("3", "up", "2025-01-06", 10.8, "2025-01-07", 13.4),
        ]
        bullish_strokes = [
            _stroke("4", "down", "2025-01-08", 13.1, "2025-01-09", 9.6),
            _stroke("5", "up", "2025-01-09", 9.6, "2025-01-10", 11.2),
            _stroke("6", "down", "2025-01-10", 11.2, "2025-01-13", 9.2),
        ]
        pivot_zones = [
            SimpleNamespace(
                id="pivot_bearish",
                start_timestamp="2025-01-03",
                end_timestamp="2025-01-06",
                high=12.0,
                low=10.8,
            ),
            SimpleNamespace(
                id="pivot_bullish",
                start_timestamp="2025-01-09",
                end_timestamp="2025-01-10",
                high=11.2,
                low=9.8,
            ),
        ]

        divergences = _map_divergences(
            strokes=[*bearish_strokes, *bullish_strokes],
            pivot_zones=pivot_zones,
        )

        self.assertEqual([item.divergence_type for item in divergences], ["bearish", "bullish"])
        self.assertEqual(divergences[0].reference_id, "stroke_3")
        self.assertEqual(divergences[0].meta["pivot_zone_id"], "pivot_bearish")
        self.assertEqual(divergences[1].reference_id, "stroke_6")
        self.assertEqual(divergences[1].meta["pivot_zone_id"], "pivot_bullish")
        self.assertLess(divergences[0].meta["magnitude_ratio"], 1.0)
        self.assertIn("ratio", divergences[1].description)

    def test_p2_mapping_can_emit_divergence_output_and_primitives(self):
        rows = [
            {"date": "2025-01-02", "open": 10.0, "close": 11.8, "high": 12.0, "low": 9.9, "volume": 1000},
            {"date": "2025-01-03", "open": 11.8, "close": 13.0, "high": 13.1, "low": 11.7, "volume": 1200},
            {"date": "2025-01-06", "open": 12.9, "close": 10.8, "high": 13.0, "low": 10.7, "volume": 1300},
            {"date": "2025-01-07", "open": 10.8, "close": 13.4, "high": 13.5, "low": 10.7, "volume": 1400},
        ]

        fx1 = SimpleNamespace(dt="2025-01-02", mark="D", fx=10.0)
        fx2 = SimpleNamespace(dt="2025-01-03", mark="G", fx=13.0)
        fx3 = SimpleNamespace(dt="2025-01-06", mark="D", fx=10.8)
        fx4 = SimpleNamespace(dt="2025-01-07", mark="G", fx=13.4)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Up", high=13.0, low=10.0)
        bi2 = SimpleNamespace(fx_a=fx2, fx_b=fx3, direction="Down", high=13.0, low=10.8)
        bi3 = SimpleNamespace(fx_a=fx3, fx_b=fx4, direction="Up", high=13.4, low=10.8)
        analyzer = SimpleNamespace(
            fx_list=[fx1, fx2, fx3, fx4],
            ubi_fxs=[],
            finished_bis=[bi1, bi2, bi3],
            last_bi_extend=False,
        )
        zs = SimpleNamespace(sdt="2025-01-03", edt="2025-01-06", zg=12.0, zd=10.8, gg=13.0, dd=10.8, zz=11.4)
        sig_module = SimpleNamespace(get_zs_seq=lambda bis: [zs])

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 4)), patch(
            "chantheory.engine.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(
                rows=rows,
                code="000001",
                market="sz",
                parameters={"min_bars": 2},
                signals_config=[],
            )

        self.assertEqual(len(result.divergences), 1)
        self.assertEqual(result.divergences[0].divergence_type, "bearish")
        self.assertEqual(result.meta["mapping"]["divergence_count"], 1)
        self.assertTrue(any(item.layer == "divergences" for item in result.plot_primitives))
        self.assertFalse(any(item.warning_code == "DIVERGENCE_CONSERVATIVE_EMPTY" for item in result.warnings))
        self.assertTrue(any("confirmed divergence" in line for line in result.summary))

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


    def test_short_input_without_bis_does_not_fail_engine_probe(self):
        rows = [
            {"date": "2025-01-01", "open": 10.0, "close": 10.4, "high": 10.5, "low": 9.9, "volume": 1000},
            {"date": "2025-01-02", "open": 10.4, "close": 9.8, "high": 10.6, "low": 9.7, "volume": 1200},
            {"date": "2025-01-03", "open": 9.8, "close": 10.8, "high": 10.9, "low": 9.6, "volume": 1500},
        ]
        analyzer = SimpleNamespace(fx_list=[], ubi_fxs=[], finished_bis=[], bi_list=[])

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 3)):
            result = analyze_tracker_klines(rows=rows, code="000001", market="sz", signals_config=[])

        self.assertEqual(result.meta["engine_probe"]["status"], "ok")
        self.assertFalse(any(item.warning_code == "ENGINE_PROBE_FAILED" for item in result.warnings))

    def test_document_style_signals_config_removes_di_and_builds_distinct_keys(self):
        config = _normalize_signals_config(
            [
                {"name": "cxt_bi_status_V230101", "freq": "30分钟"},
                {"name": "cxt_bi_status_V230101", "freq": "日线"},
                {"name": "tas_ma_base_V221101", "freq": "日线", "di": 2, "timeperiod": 5, "ma_type": "SMA"},
                {"name": "bar_zdt_V230331", "freq": "30分钟", "di": 1},
            ]
        )

        self.assertEqual(
            [item["key"] for item in config],
            [
                "30分钟_cxt_bi_status_V230101",
                "日线_cxt_bi_status_V230101",
                "日线_tas_ma_base_V221101_ma_type=SMA_timeperiod=5",
                "30分钟_bar_zdt_V230331",
            ],
        )
        self.assertEqual(config[2]["di"], 2)
        self.assertNotIn("di", config[2]["kwargs"])
        self.assertEqual(config[2]["kwargs"]["freq"], "日线")
        self.assertEqual(config[2]["kwargs"]["timeperiod"], 5)

    def test_cxt_signal_points_use_default_versions_and_ignore_other(self):
        fx1 = SimpleNamespace(dt="2025-01-01", mark="D", fx=10.0)
        fx2 = SimpleNamespace(dt="2025-01-02", mark="G", fx=11.0)
        fx3 = SimpleNamespace(dt="2025-01-03", mark="D", fx=10.2)
        fx4 = SimpleNamespace(dt="2025-01-04", mark="G", fx=11.2)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Up", high=11.0, low=10.0)
        bi2 = SimpleNamespace(fx_a=fx3, fx_b=fx4, direction="Up", high=11.2, low=10.2)
        strokes = [
            _stroke("1", "up", "2025-01-01", 10.0, "2025-01-02", 11.0),
            _stroke("2", "up", "2025-01-03", 10.2, "2025-01-04", 11.2),
        ]
        bars_raw = [
            SimpleNamespace(dt="2025-01-01", close=10.0),
            SimpleNamespace(dt="2025-01-02", close=11.0),
            SimpleNamespace(dt="2025-01-03", close=10.2),
            SimpleNamespace(dt="2025-01-04", close=11.2),
        ]
        class MockAnalyzer:
            def __init__(self, bars, max_bi_num=50):
                self.bars_raw = list(bars)
                self.bi_list = [bi1, bi2]
            def update(self, bar):
                if bar not in self.bars_raw:
                    self.bars_raw.append(bar)

        analyzer = MockAnalyzer(bars_raw)
        calls = []

        def signal(name, value):
            def _func(_analyzer, **kwargs):
                calls.append(name)
                dt_str = _analyzer.bars_raw[-1].dt.strftime("%Y-%m-%d") if hasattr(_analyzer.bars_raw[-1].dt, "strftime") else str(_analyzer.bars_raw[-1].dt)
                if dt_str in ("2025-01-02", "2025-01-04"):
                    return {name: value}
                return {name: "其他_任意_任意_0"}
            return _func

        sig_module = SimpleNamespace(
            cxt_first_buy_V221126=signal("cxt_first_buy_V221126", "其他_任意_任意_0"),
            cxt_first_sell_V221126=signal("cxt_first_sell_V221126", "一卖_5笔_任意_0"),
            cxt_second_bs_V240524=signal("cxt_second_bs_V240524", "二买_任意_任意_0"),
            cxt_third_bs_V230319=signal("cxt_third_bs_V230319", "三卖_均线新低_任意_0"),
        )

        with patch("chantheory.signals.import_module", return_value=sig_module):
            signal_evaluations, signal_series, signal_events, signal_snapshots, warnings, _ = _build_signal_payloads(
                strokes=strokes,
                analyzer=analyzer,
                index_by_timestamp={
                    "2025-01-02": 1,
                    "2025-01-04": 3,
                },
                signals_config=None,
            )
        candidate_point_events = _build_candidate_point_events(signal_evaluations)
        buy_points, sell_points = _build_candidate_points(
            strokes=strokes,
            pivot_zones=[],
            candidate_point_events=candidate_point_events,
        )

        self.assertNotIn("cxt_second_bs_V230320", calls)
        self.assertNotIn("cxt_third_buy_V230228", calls)
        self.assertFalse(warnings)
        if not signal_series:
            print("EVALUATIONS:", signal_evaluations)
        self.assertEqual([series.signal_key for series in signal_series], ["first_buy", "first_sell", "second_bs", "third_bs"])
        self.assertEqual(len(signal_snapshots), 4)
        self.assertEqual(len(signal_events), 9)
        candidate_events = _build_candidate_point_events(signal_evaluations)
        self.assertEqual(len(candidate_events), 9)

    def test_signal_status_distinguishes_active_inactive_not_ready_error(self):
        strokes = [
            _stroke("1", "up", "2025-01-01", 10.0, "2025-01-02", 11.0),
        ]
        bars_raw = [
            SimpleNamespace(dt="2025-01-01", close=10.0),
            SimpleNamespace(dt="2025-01-02", close=11.0),
        ]

        class MockAnalyzer:
            def __init__(self, bars, max_bi_num=50):
                self.bars_raw = list(bars)
                self.bi_list = []
                self.max_bi_num = max_bi_num

            def update(self, bar):
                if bar not in self.bars_raw:
                    self.bars_raw.append(bar)

        analyzer = MockAnalyzer(bars_raw)

        def not_ready_func(_a, **kw):
            raise IndexError("list index out of range")

        def error_func(_a, **kw):
            raise ValueError("unexpected error")

        sig_module = SimpleNamespace(
            cxt_first_buy_V221126=lambda _a, **kw: {"cxt_first_buy_V221126": "一买_5笔_任意_0"},
            cxt_first_sell_V221126=lambda _a, **kw: {"cxt_first_sell_V221126": "其他_任意_任意_0"},
            cxt_second_bs_V240524=not_ready_func,
            cxt_third_bs_V230319=error_func,
        )

        with patch("chantheory.signals.import_module", return_value=sig_module):
            evaluations, series, events, snapshots, warnings, _ = _build_signal_payloads(
                strokes=strokes,
                analyzer=analyzer,
                index_by_timestamp={"2025-01-01": 0, "2025-01-02": 1},
                signals_config=None,
            )

        first_bar = [e for e in evaluations if e["bar_index"] == 0]
        status_by_key = {e["signal_key"]: e["status"] for e in first_bar}
        self.assertEqual(status_by_key["first_buy"], "active")
        self.assertEqual(status_by_key["first_sell"], "inactive")
        self.assertEqual(status_by_key["second_bs"], "not_ready")
        self.assertEqual(status_by_key["third_bs"], "error")

        for snapshot in snapshots:
            self.assertEqual(snapshot.statuses.get("first_buy"), "active")
            self.assertEqual(snapshot.statuses.get("second_bs"), "not_ready")
            self.assertEqual(snapshot.statuses.get("third_bs"), "error")

        self.assertTrue(any(w.warning_code == "SIGNAL_EVALUATION_FAILED" for w in warnings))

    def test_build_signal_payloads_uses_raw_bars_over_truncated_bars_raw(self):
        # Regression: czsc's CZSC.update() truncates analyzer.bars_raw after
        # bi_list forms (keeping only bars from the first stroke's start onward).
        # build_signal_payloads must replay over the complete raw_bars sequence
        # passed by the caller, not the truncated analyzer.bars_raw.
        full_bars = [
            SimpleNamespace(dt=f"2025-01-{day:02d}", close=10.0 + day)
            for day in range(1, 11)  # 10 bars: 2025-01-01 .. 2025-01-10
        ]
        index_by_timestamp = {bar.dt: idx for idx, bar in enumerate(full_bars)}

        class ReplayAnalyzer:
            def __init__(self, bars, max_bi_num=50):
                self.bars_raw = list(bars)
                self.bi_list = []
                self.max_bi_num = max_bi_num

            def update(self, bar):
                if bar not in self.bars_raw:
                    self.bars_raw.append(bar)

        analyzer = ReplayAnalyzer(full_bars)
        # Simulate czsc truncation: analyzer.bars_raw keeps only a tail slice.
        analyzer.bars_raw = list(full_bars[5:])

        sig_module = SimpleNamespace(
            cxt_first_buy_V221126=lambda _a, **kw: {"cxt_first_buy_V221126": "其他_任意_任意_0"},
            cxt_first_sell_V221126=lambda _a, **kw: {"cxt_first_sell_V221126": "其他_任意_任意_0"},
            cxt_second_bs_V240524=lambda _a, **kw: {"cxt_second_bs_V240524": "其他_任意_任意_0"},
            cxt_third_bs_V230319=lambda _a, **kw: {"cxt_third_bs_V230319": "其他_任意_任意_0"},
        )

        with patch("chantheory.signals.import_module", return_value=sig_module):
            evaluations, series, events, snapshots, warnings, _ = _build_signal_payloads(
                strokes=[],
                analyzer=analyzer,
                index_by_timestamp=index_by_timestamp,
                signals_config=None,
                raw_bars=full_bars,
            )

        # All 10 bars must produce snapshots, starting at bar index 0.
        self.assertEqual(len(snapshots), len(full_bars))
        self.assertEqual(snapshots[0].bar_index, 0)
        self.assertEqual(snapshots[-1].bar_index, len(full_bars) - 1)
        # The truncated analyzer.bars_raw (5 bars) must not drive the count.
        self.assertGreater(len(snapshots), len(analyzer.bars_raw))

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
            "chantheory.engine.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(rows=rows, code="000001", market="sz", signals_config=[])

        self.assertEqual(len(result.candidate_buy_points), 1)
        candidate = result.candidate_buy_points[0]
        self.assertEqual(candidate.point_type, "structure_buy_candidate")
        self.assertEqual(candidate.meta["signal_scope"], "structure_candidate_only")

    def test_analyze_tracker_klines_emits_generic_signal_layers_from_explicit_config(self):
        rows = [
            {"date": "2025-01-01", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 1000},
            {"date": "2025-01-02", "open": 10.2, "close": 10.5, "high": 10.6, "low": 10.1, "volume": 1100},
            {"date": "2025-01-03", "open": 10.5, "close": 10.1, "high": 10.6, "low": 10.0, "volume": 1200},
            {"date": "2025-01-04", "open": 10.1, "close": 10.8, "high": 10.9, "low": 10.0, "volume": 1300},
            {"date": "2025-01-05", "open": 10.8, "close": 10.3, "high": 10.9, "low": 10.2, "volume": 1400},
        ]
        fx1 = SimpleNamespace(dt="2025-01-01", mark="D", fx=9.9)
        fx2 = SimpleNamespace(dt="2025-01-02", mark="G", fx=10.6)
        fx3 = SimpleNamespace(dt="2025-01-03", mark="D", fx=10.0)
        fx4 = SimpleNamespace(dt="2025-01-04", mark="G", fx=10.9)
        fx5 = SimpleNamespace(dt="2025-01-05", mark="D", fx=10.2)
        bi1 = SimpleNamespace(fx_a=fx1, fx_b=fx2, direction="Up", high=10.6, low=9.9)
        bi2 = SimpleNamespace(fx_a=fx2, fx_b=fx3, direction="Down", high=10.6, low=10.0)
        bi3 = SimpleNamespace(fx_a=fx3, fx_b=fx4, direction="Up", high=10.9, low=10.0)
        bi4 = SimpleNamespace(fx_a=fx4, fx_b=fx5, direction="Down", high=10.9, low=10.2)
        bars_raw = [
            SimpleNamespace(dt="2025-01-01", close=10.2),
            SimpleNamespace(dt="2025-01-02", close=10.5),
            SimpleNamespace(dt="2025-01-03", close=10.1),
            SimpleNamespace(dt="2025-01-04", close=10.8),
            SimpleNamespace(dt="2025-01-05", close=10.3),
        ]
        class MockAnalyzer2:
            def __init__(self, bars, max_bi_num=50):
                self.bars_raw = list(bars)
                self.bi_list = [bi1, bi2, bi3, bi4]
            def update(self, bar):
                if bar not in self.bars_raw:
                    self.bars_raw.append(bar)

        analyzer = MockAnalyzer2(bars_raw)
        analyzer.fx_list = [fx1, fx2, fx3, fx4, fx5]
        analyzer.ubi_fxs = []
        analyzer.finished_bis = [bi1, bi2, bi3, bi4]
        analyzer.last_bi_extend = False

        def trend_signal(_analyzer, di=1, **_kwargs):
            dt_str = _analyzer.bars_raw[-1].dt.strftime("%Y-%m-%d") if hasattr(_analyzer.bars_raw[-1].dt, "strftime") else str(_analyzer.bars_raw[-1].dt)
            values = {
                "2025-01-02": "其他_任意_任意_0",
                "2025-01-03": "看多_低位_任意_0",
                "2025-01-04": "看多_加速_任意_0",
                "2025-01-05": "其他_任意_任意_0",
            }
            return {"trend_signal": values.get(dt_str, "其他_任意_任意_0")}

        signal_module = SimpleNamespace(trend_signal=trend_signal)

        def fake_import(name):
            if name == "custom.signals":
                return signal_module
            raise ImportError(name)

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, bars_raw)), patch(
            "chantheory.engine.load_czsc_utils", return_value=SimpleNamespace(get_zs_seq=lambda bis: [])
        ), patch("chantheory.signals.import_module", side_effect=fake_import):
            result = analyze_tracker_klines(
                rows=rows,
                code="000001",
                market="sz",
                signals_config=[
                    {
                        "module": "custom.signals",
                        "name": "trend_signal",
                        "key": "trend_bias",
                    }
                ],
            )

        self.assertEqual([series.signal_key for series in result.signal_series], ["trend_bias"])
        self.assertEqual([point.value for point in result.signal_series[0].points], ["其他_任意_任意_0", "其他_任意_任意_0", "看多_低位_任意_0", "看多_加速_任意_0", "其他_任意_任意_0"])
        self.assertEqual([event.event_type for event in result.signal_events], ["triggered", "switched", "invalidated"])
        self.assertEqual(result.signal_snapshots[2].active_signals, {"trend_bias": "看多_低位_任意_0"})
        self.assertEqual(result.meta["signals"]["config"][0]["key"], "trend_bias")
        self.assertEqual(result.meta["signals"]["event_count"], 3)
        self.assertEqual(result.candidate_point_events, [])
        self.assertEqual(result.candidate_buy_points, [])
        self.assertEqual(result.candidate_sell_points, [])

    def test_invalid_signals_config_adds_warning_without_breaking_analysis(self):
        rows = [
            {"date": "2025-01-01", "open": 10.0, "close": 10.2, "high": 10.3, "low": 9.9, "volume": 1000},
            {"date": "2025-01-02", "open": 10.2, "close": 10.5, "high": 10.6, "low": 10.1, "volume": 1100},
        ]
        analyzer = SimpleNamespace(fx_list=[], ubi_fxs=[], finished_bis=[], bi_list=[], last_bi_extend=False)

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * 2)):
            result = analyze_tracker_klines(
                rows=rows,
                code="000001",
                market="sz",
                signals_config={"signals": "bad"},
            )

        self.assertTrue(any(item.warning_code == "INVALID_SIGNALS_CONFIG" for item in result.warnings))
        self.assertEqual(result.signal_series, [])
        self.assertEqual(result.signal_events, [])
        self.assertEqual(result.signal_snapshots, [])

    def test_candidate_point_events_capture_trigger_switch_and_invalidate(self):
        evaluations = [
            {
                "signal_key": "second_bs",
                "signal_name": "cxt_second_bs_V240524",
                "module": "czsc.signals.cxt",
                "timestamp": "2025-01-02",
                "bar_index": 1,
                "reference_id": "stroke_1",
                "price": 10.2,
                "direction": "down",
                "value": "其他_任意_任意_0",
                "active": False,
            },
            {
                "signal_key": "second_bs",
                "signal_name": "cxt_second_bs_V240524",
                "module": "czsc.signals.cxt",
                "timestamp": "2025-01-03",
                "bar_index": 2,
                "reference_id": "stroke_2",
                "price": 10.1,
                "direction": "down",
                "value": "二买_任意_任意_0",
                "active": True,
            },
            {
                "signal_key": "second_bs",
                "signal_name": "cxt_second_bs_V240524",
                "module": "czsc.signals.cxt",
                "timestamp": "2025-01-04",
                "bar_index": 3,
                "reference_id": "stroke_3",
                "price": 11.4,
                "direction": "up",
                "value": "二卖_任意_任意_0",
                "active": True,
            },
            {
                "signal_key": "second_bs",
                "signal_name": "cxt_second_bs_V240524",
                "module": "czsc.signals.cxt",
                "timestamp": "2025-01-05",
                "bar_index": 4,
                "reference_id": "stroke_4",
                "price": 11.2,
                "direction": "up",
                "value": "其他_任意_任意_0",
                "active": False,
            },
        ]

        candidate_events = _build_candidate_point_events(evaluations)

        self.assertEqual([event.event_type for event in candidate_events], ["triggered", "switched", "invalidated"])
        self.assertEqual([event.point_type for event in candidate_events], ["second_buy", "second_sell", "second_sell"])
        self.assertEqual(candidate_events[1].meta["previous_point_type"], "second_buy")
        self.assertEqual(candidate_events[2].meta["previous_value"], "二卖_任意_任意_0")

    def test_analyze_multi_timeframe_tracker_klines_orders_levels_and_aggregates_warnings(self):
        rows_by_timeframe = {
            "week": [{"date": "2025-01-10", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000}],
            "day": [{"date": "2025-01-10", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000}],
            "month": [{"date": "2025-01-31", "open": 10, "close": 11, "high": 11.5, "low": 9.5, "volume": 2000}],
        }

        def fake_analyze_tracker_klines(rows, code, market, timeframe, source="tencent", parameters=None, signals_config=None, strict=True):
            if timeframe == "day":
                result = _analysis_result("day", 120, warning_code="UNSTABLE_TAIL_STROKE")
                result.signal_events = [SimpleNamespace(id="signal_event_day")]
                result.candidate_point_events = [SimpleNamespace(id="candidate_event_day")]
                return result
            if timeframe == "week":
                result = _analysis_result("week", 24)
                result.signal_events = [SimpleNamespace(id="signal_event_week_1"), SimpleNamespace(id="signal_event_week_2")]
                return result
            return _analysis_result("month", 6)

        with patch("chantheory.adapters.analyze_tracker_klines", side_effect=fake_analyze_tracker_klines):
            result = analyze_multi_timeframe_tracker_klines(
                rows_by_timeframe=rows_by_timeframe,
                code="000001",
                market="sz",
                base_timeframe="day",
            )

        self.assertEqual(result.symbol, "000001.SZ")
        self.assertEqual(result.timeframes, ["day", "week", "month"])
        self.assertEqual([level.role for level in result.levels], ["base", "higher", "higher"])
        self.assertEqual(result.meta["higher_timeframes"], ["week", "month"])
        self.assertEqual(result.meta["bar_count_by_timeframe"], {"day": 120, "week": 24, "month": 6})
        self.assertEqual(result.meta["signal_event_count"], 3)
        self.assertEqual(result.meta["candidate_point_event_count"], 1)
        self.assertTrue(any(item.warning_code == "UNSTABLE_TAIL_STROKE" for item in result.warnings))
        self.assertTrue(result.summary)

    def test_analyze_multi_timeframe_tracker_klines_warns_when_base_timeframe_missing(self):
        rows_by_timeframe = {
            "week": [{"date": "2025-01-10", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 1000}],
        }

        with patch("chantheory.adapters.analyze_tracker_klines", return_value=_analysis_result("week", 24)):
            result = analyze_multi_timeframe_tracker_klines(
                rows_by_timeframe=rows_by_timeframe,
                code="000001",
                market="sz",
                base_timeframe="day",
            )

        self.assertEqual(result.timeframes, ["week"])
        self.assertTrue(any(item.warning_code == "MULTI_TIMEFRAME_BASE_MISSING" for item in result.warnings))
        self.assertEqual(result.meta["roles"], {"week": "higher"})

    def test_fixture_engine_version_matches_pin(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "p2_sample_result.json"
        payload = json.loads(fixture_path.read_text())

        self.assertEqual(payload["engine_version"], "0.10.12")
        self.assertEqual(payload["meta"]["engine_assumptions"]["engine_version"], "0.10.12")

    def test_adapter_emits_both_stroke_and_segment_pivot_zones_with_input_isolation(self):
        # 构造 12 根交替笔，形成 4 个交替段（up/down/up/down），
        # 前 3 段重叠 → 产生 1 个段中枢；同时 mock get_zs_seq 返回 1 个笔中枢。
        # 验证：pivot_zones 同时包含 level=stroke 和 level=segment；
        #       mapping 计数正确；divergences/alerts/candidates 只引用 stroke pivot。
        prices = [10.0, 20.0, 15.0, 22.0, 16.0, 19.0, 12.0, 18.0, 14.0, 21.0, 17.0, 19.0, 15.0]
        fx_list = [
            SimpleNamespace(dt=f"2025-01-{i+1:02d}", mark=("G" if i % 2 == 1 else "D"), fx=prices[i])
            for i in range(len(prices))
        ]
        bis = [
            SimpleNamespace(
                fx_a=fx_list[i],
                fx_b=fx_list[i+1],
                direction=("Up" if prices[i+1] > prices[i] else "Down"),
                high=max(prices[i], prices[i+1]),
                low=min(prices[i], prices[i+1]),
            )
            for i in range(len(prices) - 1)
        ]
        analyzer = SimpleNamespace(fx_list=fx_list, ubi_fxs=[], finished_bis=bis, last_bi_extend=False)
        zs = SimpleNamespace(sdt="2025-01-02", edt="2025-01-05", zg=20.0, zd=15.0, gg=22.0, dd=10.0, zz=17.5)
        sig_module = SimpleNamespace(get_zs_seq=lambda bis: [zs])
        rows = [
            {"date": f"2025-01-{i+1:02d}", "open": p - 0.5, "close": p + 0.3, "high": p + 0.5, "low": p - 0.7, "volume": 1000}
            for i, p in enumerate(prices)
        ]

        with patch("chantheory.adapters._run_engine", return_value=(analyzer, [object()] * len(prices))), patch(
            "chantheory.engine.load_czsc_utils", return_value=sig_module
        ):
            result = analyze_tracker_klines(
                rows=rows,
                code="000001",
                market="sz",
                parameters={"min_bars": 2},
                signals_config=[],
            )

        levels = {zone.level for zone in result.pivot_zones}
        self.assertIn("stroke", levels, "应包含笔中枢")
        self.assertIn("segment", levels, "应包含段中枢")

        stroke_zones = [z for z in result.pivot_zones if z.level == "stroke"]
        segment_zones = [z for z in result.pivot_zones if z.level == "segment"]
        self.assertEqual(len(stroke_zones), 1)
        self.assertEqual(len(segment_zones), 1)

        mapping = result.meta["mapping"]
        self.assertEqual(mapping["stroke_pivot_zone_count"], 1)
        self.assertEqual(mapping["segment_pivot_zone_count"], 1)
        self.assertEqual(mapping["pivot_zone_count"], 2)

        stroke_zone_ids = {z.id for z in stroke_zones}
        segment_zone_ids = {z.id for z in segment_zones}

        # divergences 只引用 stroke pivot
        for div in result.divergences:
            self.assertIn(div.meta.get("pivot_zone_id"), stroke_zone_ids)

        # structure_alerts 只引用 stroke pivot（通过 meta.pivot_zone_id 或 related_ids）
        for alert in result.structure_alerts:
            related = set(alert.related_ids) | {alert.meta.get("pivot_zone_id")}
            self.assertTrue(
                related & stroke_zone_ids or not (related & segment_zone_ids),
                "structure_alerts 不应引用段中枢",
            )

        # candidate points 只引用 stroke pivot
        for point in result.candidate_buy_points + result.candidate_sell_points:
            self.assertIn(point.reference_id, stroke_zone_ids | {""})


if __name__ == "__main__":
    unittest.main()
