import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import types


class _DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


if "streamlit" not in sys.modules:
    streamlit_stub = types.ModuleType("streamlit")
    streamlit_stub.session_state = {}
    streamlit_stub.set_page_config = lambda *args, **kwargs: None
    streamlit_stub.markdown = lambda *args, **kwargs: None
    streamlit_stub.text_input = lambda *args, **kwargs: ""

    def _selectbox_stub(*args, **kwargs):
        key = kwargs.get("key")
        if key is not None and key in streamlit_stub.session_state:
            return streamlit_stub.session_state[key]
        options = kwargs.get("options")
        if isinstance(options, (list, tuple)) and options:
            return options[0]
        return "day"

    streamlit_stub.selectbox = _selectbox_stub
    streamlit_stub.date_input = lambda *args, **kwargs: date(2026, 6, 12)
    streamlit_stub.number_input = lambda *args, **kwargs: 50
    streamlit_stub.checkbox = lambda *args, **kwargs: True
    streamlit_stub.button = lambda *args, **kwargs: False
    streamlit_stub.info = lambda *args, **kwargs: None
    streamlit_stub.warning = lambda *args, **kwargs: None
    streamlit_stub.write = lambda *args, **kwargs: None
    streamlit_stub.json = lambda *args, **kwargs: None
    streamlit_stub.captions = lambda *args, **kwargs: None
    streamlit_stub.caption = lambda *args, **kwargs: None
    streamlit_stub.dataframe = lambda *args, **kwargs: None
    streamlit_stub.rerun = lambda: None
    streamlit_stub.tabs = lambda labels: tuple(_DummyContext() for _ in labels)
    streamlit_stub.expander = lambda *args, **kwargs: _DummyContext()
    streamlit_stub.sidebar = _DummyContext()
    components_stub = types.ModuleType("streamlit.components")
    components_v1_stub = types.ModuleType("streamlit.components.v1")
    components_v1_stub.declare_component = lambda *args, **kwargs: (lambda **inner_kwargs: None)
    components_stub.v1 = components_v1_stub
    streamlit_stub.components = components_stub
    sys.modules["streamlit"] = streamlit_stub
    sys.modules["streamlit.components"] = components_stub
    sys.modules["streamlit.components.v1"] = components_v1_stub


APP_DIR = Path(__file__).resolve().parents[1]
APP_PATH = APP_DIR / "app.py"
SPEC = importlib.util.spec_from_file_location("chan_viewer_app", APP_PATH)
app = None
APP_IMPORT_ERROR = None
if SPEC and SPEC.loader:
    try:
        app = importlib.util.module_from_spec(SPEC)
        sys.modules[SPEC.name] = app
        SPEC.loader.exec_module(app)
    except ModuleNotFoundError as exc:
        APP_IMPORT_ERROR = exc
        app = None


@unittest.skipIf(app is None, f"app dependencies unavailable: {APP_IMPORT_ERROR}")
class ChartAxisTests(unittest.TestCase):
    def test_default_slots_is_constant_120(self):
        self.assertEqual(app._default_slots(), 120)

    def test_zoom_step_uses_proportional_ratio(self):
        """zoom_step(current) = max(10, round(current / 6))"""
        self.assertEqual(app._zoom_step(120), 20)
        self.assertEqual(app._zoom_step(240), 40)
        self.assertEqual(app._zoom_step(40), 10)
        self.assertEqual(app._zoom_step(60), 10)

    def test_zoom_step_clamps_to_minimum_10(self):
        self.assertEqual(app._zoom_step(1), 10)
        self.assertEqual(app._zoom_step(0), 10)

    def test_zoom_step_uses_shared_constants(self):
        """ZOOM_STEP 常量与 zoom_step 公式一致，前端通过 payload 使用同一组常量。"""
        from charts.window_policy import ZOOM_STEP_DENOMINATOR, ZOOM_STEP_MIN
        self.assertEqual(ZOOM_STEP_DENOMINATOR, 6)
        self.assertEqual(ZOOM_STEP_MIN, 10)
        # current = denominator * N -> step = N (当 N >= min)
        self.assertEqual(app._zoom_step(ZOOM_STEP_DENOMINATOR * 20), 20)
        # current < denominator * min -> step = min
        self.assertEqual(app._zoom_step(1), ZOOM_STEP_MIN)

    def test_unified_linear_axis_for_day_timeframe(self):
        rows = [
            {"date": "2026-06-10", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100},
            {"date": "2026-06-11", "open": 11, "close": 10.5, "high": 11.1, "low": 10.4, "volume": 120},
            {"date": "2026-06-12", "open": 10.6, "close": 10.8, "high": 11.0, "low": 10.5, "volume": 130},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={},
            timeframe="day",
            language="zh",
            x_window=120,
        )

        self.assertEqual(figure.layout.xaxis.type, "linear")
        self.assertEqual(tuple(figure.layout.xaxis.range), (-0.5, 119.5))
        self.assertEqual(tuple(figure.layout.xaxis3.range), (-0.5, 119.5))

    def test_unified_linear_axis_for_minute_timeframe(self):
        rows = [
            {"date": "2026-06-11 14:30:00", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100},
            {"date": "2026-06-11 15:00:00", "open": 11, "close": 10.5, "high": 11.1, "low": 10.4, "volume": 120},
            {"date": "2026-06-12 09:30:00", "open": 10.6, "close": 10.8, "high": 11.0, "low": 10.5, "volume": 130},
            {"date": "2026-06-12 10:00:00", "open": 10.8, "close": 11.2, "high": 11.3, "low": 10.7, "volume": 150},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={},
            timeframe="30m",
            language="zh",
            x_window=120,
        )

        self.assertEqual(figure.layout.xaxis.type, "linear")
        self.assertEqual(tuple(figure.layout.xaxis.range), (-0.5, 119.5))
        self.assertEqual(tuple(figure.layout.xaxis3.range), (-0.5, 119.5))

    def test_left_align_right_empty_when_data_less_than_slots(self):
        """43 根数据 + 120 slot -> 左对齐，右侧留空。"""
        rows = [
            {"date": f"2026-01-{day:02d}", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
            for day in range(1, 44)
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="zh",
            x_window=120,
        )

        self.assertEqual(figure.layout.xaxis.type, "linear")
        self.assertEqual(tuple(figure.layout.xaxis.range), (-0.5, 119.5))

    def test_right_align_when_data_exceeds_slots(self):
        """200 根数据 + 120 slot -> 右对齐，显示最新 120 根。"""
        rows = [
            {"date": f"2025-{month:02d}-15", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
            for month in range(1, 13)
            for day in [1, 15]
        ] * 9  # ~216 rows

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="zh",
            x_window=120,
        )

        row_count = len(rows)
        expected_start = row_count - 120 - 0.5
        expected_end = row_count - 1 + 0.5
        self.assertEqual(figure.layout.xaxis.type, "linear")
        self.assertAlmostEqual(figure.layout.xaxis.range[0], expected_start, places=2)
        self.assertAlmostEqual(figure.layout.xaxis.range[1], expected_end, places=2)

    def test_tick_labels_generated_by_target_count(self):
        rows = [
            {"date": f"2026-06-{day:02d}", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
            for day in range(1, 13)
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="zh",
            x_window=120,
        )

        tickvals = figure.layout.xaxis.tickvals
        ticktext = figure.layout.xaxis.ticktext
        self.assertTrue(len(tickvals) > 0)
        self.assertTrue(all(isinstance(v, (int, float)) for v in tickvals))
        self.assertTrue(len(ticktext) > 0)

    def test_tick_labels_month_timeframe_uses_year_month(self):
        rows = [
            {"date": f"2025-{month:02d}-15", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
            for month in range(1, 13)
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="month",
            language="zh",
            x_window=120,
        )

        ticktext = figure.layout.xaxis.ticktext
        self.assertTrue(all(len(t) == 7 for t in ticktext))

    def test_tick_labels_minute_timeframe_dedup_same_day(self):
        """1m 周期同一天内 tick 标签应显示 HH:mm，不重复日期。"""
        rows = [
            {"date": f"2026-06-12 {h:02d}:{m:02d}:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100}
            for h, m in [(9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (13, 0), (13, 30)]
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="30m",
            language="zh",
            x_window=120,
        )

        ticktext = figure.layout.xaxis.ticktext
        self.assertTrue(len(ticktext) >= 2)
        # 第一个 tick 应包含日期前缀 (MM-DD HH:mm)
        self.assertRegex(ticktext[0], r"^\d{2}-\d{2} \d{2}:\d{2}$")
        # 后续同日 tick 应只显示 HH:mm
        for label in ticktext[1:]:
            self.assertRegex(label, r"^\d{2}:\d{2}$", f"Expected HH:mm, got '{label}'")

    def test_tick_labels_minute_timeframe_shows_date_at_day_boundary(self):
        """分钟周期跨日时应在日期边界显示 MM-DD HH:mm。"""
        rows = [
            {"date": "2026-06-11 14:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11 14:30:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11 15:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-12 09:30:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-12 10:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-12 10:30:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-12 11:00:00", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="30m",
            language="zh",
            x_window=120,
        )

        ticktext = figure.layout.xaxis.ticktext
        # 跨日标签含空格 (MM-DD HH:mm)，同日标签只有 HH:mm
        date_labels = [t for t in ticktext if " " in t]
        self.assertTrue(len(date_labels) >= 2, f"Expected >=2 date boundary labels, got {ticktext}")

    def test_build_figure_adds_volume_and_macd_subpanes(self):
        rows = [
            {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.0, "volume": 140},
            {"date": "2026-06-12", "open": 10.2, "close": 10.9, "high": 11.0, "low": 10.1, "volume": 180},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": True, "macd_panel": True},
            timeframe="day",
            language="en",
            x_window=120,
        )

        self.assertEqual(len(figure.data), 5)
        self.assertEqual([trace.name for trace in figure.data], ["K-line", "Volume", "MACD Hist", "DIF", "DEA"])
        self.assertTrue(figure.layout.showlegend)
        self.assertEqual(figure.layout.yaxis2.title.text, "Volume")
        self.assertEqual(figure.layout.yaxis3.title.text, "MACD")

    def test_build_figure_can_hide_volume_and_macd_subpanes(self):
        rows = [
            {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.0, "volume": 140},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={"volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="en",
            x_window=120,
        )

        self.assertEqual(len(figure.data), 1)
        self.assertEqual(figure.data[0].name, "K-line")

    def test_ordered_rows_and_chart_key_use_display_order(self):
        rows = [
            {"date": "2026-06-12", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100},
            {"date": "2026-06-10", "open": 9, "close": 10, "high": 10.1, "low": 8.9, "volume": 120},
            {"date": "2026-06-11", "open": 10, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 130},
        ]

        ordered = app._ordered_rows(rows)
        key = app._build_chart_key(
            symbol="000001",
            market="sz",
            timeframe="day",
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 12),
            rows=ordered,
        )

        self.assertEqual([row["date"] for row in ordered], ["2026-06-10", "2026-06-11", "2026-06-12"])
        self.assertTrue(key.endswith("|3|2026-06-10|2026-06-12"))

    def test_frontend_template_preserves_scale_placeholder(self):
        self.assertEqual(app._frontend_template("zh", "y_zoom_caption"), "{scale}x 区间")
        self.assertEqual(app._frontend_template("en", "y_zoom_caption"), "{scale}x range")

    def test_build_debug_payload_includes_signal_metadata(self):
        result = SimpleNamespace(
            meta={
                "engine_probe": {"status": "ok"},
                "mapping": {"signal_event_count": 3},
                "engine_assumptions": {"segment_strategy": "demo"},
                "signals": {"event_count": 3, "candidate_point_event_count": 2},
            },
            plot_primitives=[{"id": "primitive_1"}],
        )

        payload = app._build_debug_payload(result)

        self.assertEqual(payload["engine_probe"]["status"], "ok")
        self.assertEqual(payload["mapping"]["signal_event_count"], 3)
        self.assertEqual(payload["rendering"]["count_plot_primitives"], 1)
        self.assertEqual(payload["signals"]["candidate_point_event_count"], 2)

    def test_build_signal_timeline_payload_aggregates_snapshots_and_events(self):
        result = SimpleNamespace(
            signal_series=[SimpleNamespace(signal_key="trend_bias", signal_name="Trend Bias")],
            signal_snapshots=[
                SimpleNamespace(
                    timestamp="2026-06-12 10:00:00",
                    bar_index=3,
                    values={"trend_bias": "bullish"},
                    active_signals={"trend_bias": "bullish"},
                    statuses={"trend_bias": "active"},
                    reference_id="stroke_3",
                    price=10.8,
                    meta={"signal_names": {"trend_bias": "Trend Bias"}},
                )
            ],
            signal_events=[
                SimpleNamespace(
                    signal_key="trend_bias",
                    signal_name="Trend Bias",
                    event_type="triggered",
                    timestamp="2026-06-12 10:00:00",
                    bar_index=3,
                    value="bullish",
                    active=True,
                    reference_id="stroke_3",
                    price=10.8,
                    meta={"previous_value": ""},
                )
            ],
        )

        payload = app._build_signal_timeline_payload(result)

        self.assertEqual(payload["meta"]["series_count"], 1)
        self.assertEqual(payload["meta"]["snapshot_count"], 1)
        self.assertEqual(payload["meta"]["event_count"], 1)
        self.assertEqual(payload["meta"]["row_count"], 1)
        self.assertEqual(payload["rows"][0]["active_signals"][0]["signal_key"], "trend_bias")
        self.assertEqual(payload["rows"][0]["events"][0]["event_type"], "triggered")

    def test_build_signal_timeline_table_rows_formats_display_columns(self):
        payload = {
            "rows": [
                {
                    "timestamp": "2026-06-12 10:00:00",
                    "bar_index": 3,
                    "price": 10.8,
                    "reference_id": "stroke_3",
                    "active_signals": [{"signal_name": "Trend Bias", "value": "bullish"}],
                    "events": [
                        {
                            "signal_key": "trend_bias",
                            "signal_name": "Trend Bias",
                            "event_type": "switched",
                            "value": "bullish",
                            "previous_value": "neutral",
                        }
                    ],
                }
            ]
        }

        rows = app._build_signal_timeline_table_rows(payload, language="en")

        self.assertEqual(rows[0]["Timestamp"], "2026-06-12 10:00:00")
        self.assertEqual(rows[0]["Bar"], 3)
        self.assertEqual(rows[0]["Active Signals"], "Trend Bias=bullish")
        self.assertEqual(rows[0]["State Events"], "Trend Bias switched: neutral -> bullish")
        self.assertEqual(rows[0]["Reference"], "stroke_3")

    def test_build_current_bar_signal_payload_uses_latest_timeline_row(self):
        timeline_payload = {
            "rows": [
                {
                    "timestamp": "2026-06-12 09:30:00",
                    "bar_index": 2,
                    "price": 10.4,
                    "reference_id": "stroke_2",
                    "values": [{"signal_key": "trend_bias", "signal_name": "Trend Bias", "value": "neutral"}],
                    "active_signals": [],
                    "events": [],
                },
                {
                    "timestamp": "2026-06-12 10:00:00",
                    "bar_index": 3,
                    "price": 10.8,
                    "reference_id": "stroke_3",
                    "values": [{"signal_key": "trend_bias", "signal_name": "Trend Bias", "value": "bullish"}],
                    "active_signals": [{"signal_key": "trend_bias", "signal_name": "Trend Bias", "value": "bullish"}],
                    "events": [
                        {
                            "signal_key": "trend_bias",
                            "signal_name": "Trend Bias",
                            "event_type": "triggered",
                            "value": "bullish",
                            "previous_value": "",
                        }
                    ],
                },
            ]
        }

        payload = app._build_current_bar_signal_payload(timeline_payload)

        self.assertEqual(payload["meta"]["timestamp"], "2026-06-12 10:00:00")
        self.assertEqual(payload["meta"]["active_signal_count"], 1)
        self.assertEqual(payload["signals"][0]["value"], "bullish")
        self.assertEqual(payload["events"][0]["event_type"], "triggered")

    def test_build_current_bar_signal_tables_format_summary_signal_and_event_rows(self):
        current_payload = {
            "meta": {
                "timestamp": "2026-06-12 10:00:00",
                "bar_index": 3,
                "price": 10.8,
                "reference_id": "stroke_3",
                "signal_count": 1,
                "active_signal_count": 1,
                "event_count": 1,
            },
            "signals": [
                {"signal_key": "trend_bias", "signal_name": "Trend Bias", "value": "bullish", "active": True}
            ],
            "events": [
                {
                    "signal_key": "trend_bias",
                    "signal_name": "Trend Bias",
                    "event_type": "switched",
                    "value": "bullish",
                    "previous_value": "neutral",
                }
            ],
        }

        summary_rows = app._build_current_bar_signal_summary_rows(current_payload, language="en")
        signal_rows = app._build_current_bar_signal_table_rows(current_payload, language="en")
        event_rows = app._build_current_bar_event_table_rows(current_payload, language="en")

        self.assertEqual(summary_rows[0]["Field"], "Timestamp")
        self.assertEqual(summary_rows[0]["Value"], "2026-06-12 10:00:00")
        self.assertEqual(signal_rows[0]["Signal Name"], "Trend Bias")
        self.assertEqual(signal_rows[0]["Status"], "active")
        self.assertEqual(event_rows[0]["Event"], "switched")
        self.assertEqual(event_rows[0]["Change"], "neutral -> bullish")

    def test_build_figure_respects_legend_hover_and_overlay_legend_groups(self):
        rows = [
            {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.0, "volume": 140},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={
                "plot_primitives": [
                    {
                        "type": "line",
                        "layer": "strokes",
                        "x1": "2026-06-10",
                        "x2": "2026-06-11",
                        "y1": 9.9,
                        "y2": 10.7,
                    },
                    {
                        "type": "line",
                        "layer": "strokes",
                        "x1": "2026-06-11",
                        "x2": "2026-06-11",
                        "y1": 10.0,
                        "y2": 10.6,
                    },
                ]
            },
            visibility={"strokes": True, "volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="en",
            x_window=120,
            show_legend=True,
            unified_hover=False,
        )

        self.assertTrue(figure.layout.showlegend)
        self.assertEqual(figure.layout.hovermode, "closest")
        self.assertEqual(figure.data[1].name, "Show Strokes")
        self.assertTrue(figure.data[1].showlegend)
        self.assertFalse(figure.data[2].showlegend)

    def test_build_figure_filters_pivot_zones_by_level(self):
        rows = [
            {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.0, "volume": 140},
        ]
        result_payload = {
            "plot_primitives": [
                {
                    "type": "box",
                    "layer": "pivot_zones",
                    "x1": "2026-06-10",
                    "x2": "2026-06-11",
                    "y1": 10.6,
                    "y2": 10.0,
                    "color": "#F59E0B",
                    "meta": {"level": "stroke"},
                },
                {
                    "type": "box",
                    "layer": "pivot_zones",
                    "x1": "2026-06-10",
                    "x2": "2026-06-11",
                    "y1": 10.7,
                    "y2": 9.9,
                    "color": "#8B5CF6",
                    "meta": {"level": "segment"},
                },
            ]
        }

        stroke_only = app._build_figure(
            rows=rows,
            result_payload=result_payload,
            visibility={"stroke_pivot_zones": True, "segment_pivot_zones": False, "volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="en",
            x_window=120,
        )
        segment_only = app._build_figure(
            rows=rows,
            result_payload=result_payload,
            visibility={"stroke_pivot_zones": False, "segment_pivot_zones": True, "volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="en",
            x_window=120,
        )

        self.assertEqual(len(stroke_only.layout.shapes), 1)
        self.assertEqual(stroke_only.layout.shapes[0].line.color, "#F59E0B")
        self.assertEqual(len(segment_only.layout.shapes), 1)
        self.assertEqual(segment_only.layout.shapes[0].line.color, "#8B5CF6")

    def test_build_figure_zh_box_label_distinguishes_pivot_level(self):
        rows = [
            {"date": "2026-06-10", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9, "volume": 100},
            {"date": "2026-06-11", "open": 10.5, "close": 10.2, "high": 10.7, "low": 10.0, "volume": 140},
        ]
        result_payload = {
            "plot_primitives": [
                {
                    "type": "box",
                    "layer": "pivot_zones",
                    "x1": "2026-06-10",
                    "x2": "2026-06-11",
                    "y1": 10.6,
                    "y2": 10.0,
                    "color": "#F59E0B",
                    "text": "Pivot Zone",
                    "meta": {"level": "stroke"},
                },
                {
                    "type": "box",
                    "layer": "pivot_zones",
                    "x1": "2026-06-10",
                    "x2": "2026-06-11",
                    "y1": 10.7,
                    "y2": 9.9,
                    "color": "#8B5CF6",
                    "text": "Segment Pivot Zone",
                    "meta": {"level": "segment"},
                },
            ]
        }

        figure = app._build_figure(
            rows=rows,
            result_payload=result_payload,
            visibility={"stroke_pivot_zones": True, "segment_pivot_zones": True, "volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="zh",
            x_window=120,
        )

        annotation_texts = [ann.text for ann in figure.layout.annotations]
        self.assertIn("笔中枢", annotation_texts)
        self.assertIn("段中枢", annotation_texts)

    def test_build_overview_card_rows_format_display_data(self):
        result = SimpleNamespace(
            timeframe="day",
            strokes=[SimpleNamespace()],
            segments=[SimpleNamespace()],
            pivot_zones=[],
            signal_series=[SimpleNamespace(), SimpleNamespace()],
            warnings=[SimpleNamespace()],
            candidate_buy_points=[SimpleNamespace()],
            candidate_sell_points=[SimpleNamespace()],
        )
        overview_rows = app._build_overview_card_rows(
            result,
            chart_rows=[{"date": "2026-06-12", "close": 10.8}],
            current_bar_payload={"meta": {"active_signal_count": 1, "timestamp": "2026-06-12", "price": 10.8}},
            language="en",
        )

        self.assertEqual(overview_rows[0]["Field"], "Base Timeframe")
        self.assertEqual(overview_rows[1]["Field"], "Latest Bar")
        self.assertEqual(overview_rows[2]["Value"], "10.8")


if __name__ == "__main__":
    unittest.main()
