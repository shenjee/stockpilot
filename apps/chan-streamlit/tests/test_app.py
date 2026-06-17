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
    streamlit_stub.selectbox = lambda *args, **kwargs: "day"
    streamlit_stub.date_input = lambda *args, **kwargs: date(2026, 6, 12)
    streamlit_stub.number_input = lambda *args, **kwargs: 50
    streamlit_stub.checkbox = lambda *args, **kwargs: True
    streamlit_stub.button = lambda *args, **kwargs: False
    streamlit_stub.info = lambda *args, **kwargs: None
    streamlit_stub.warning = lambda *args, **kwargs: None
    streamlit_stub.write = lambda *args, **kwargs: None
    streamlit_stub.json = lambda *args, **kwargs: None
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
SPEC = importlib.util.spec_from_file_location("chan_streamlit_app", APP_PATH)
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
    def test_minute_timeframe_uses_continuous_category_axis(self):
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
            x_window=3,
        )

        self.assertEqual(figure.layout.xaxis.type, "category")
        self.assertEqual(figure.layout.xaxis3.type, "category")
        self.assertEqual(tuple(figure.layout.xaxis.categoryarray), tuple(row["date"] for row in rows))
        self.assertEqual(tuple(figure.layout.xaxis.tickvals), ("2026-06-11 14:30:00", "2026-06-12 09:30:00"))
        self.assertEqual(tuple(figure.layout.xaxis.ticktext), ("2026-06-11", "2026-06-12"))
        self.assertEqual(tuple(figure.layout.xaxis.range), (0.5, 3.5))
        self.assertEqual(tuple(figure.layout.xaxis3.range), (0.5, 3.5))

    def test_day_timeframe_keeps_date_range(self):
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
            x_window=2,
        )

        self.assertIsNone(figure.layout.xaxis.type)
        self.assertEqual(tuple(figure.layout.xaxis.range), ("2026-06-11", "2026-06-12"))
        self.assertEqual(tuple(figure.layout.xaxis3.range), ("2026-06-11", "2026-06-12"))

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
            x_window=3,
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
            x_window=2,
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
        multi_result = SimpleNamespace(meta={"level_count": 3, "higher_timeframes": ["week", "month"]})

        payload = app._build_debug_payload(result, multi_result=multi_result)

        self.assertEqual(payload["engine_probe"]["status"], "ok")
        self.assertEqual(payload["mapping"]["signal_event_count"], 3)
        self.assertEqual(payload["rendering"]["count_plot_primitives"], 1)
        self.assertEqual(payload["signals"]["candidate_point_event_count"], 2)
        self.assertEqual(payload["multi_timeframe"]["level_count"], 3)

    def test_build_signal_timeline_payload_aggregates_snapshots_and_events(self):
        result = SimpleNamespace(
            signal_series=[SimpleNamespace(signal_key="trend_bias", signal_name="Trend Bias")],
            signal_snapshots=[
                SimpleNamespace(
                    timestamp="2026-06-12 10:00:00",
                    bar_index=3,
                    values={"trend_bias": "bullish"},
                    active_signals={"trend_bias": "bullish"},
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
            x_window=2,
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
            x_window=2,
        )
        segment_only = app._build_figure(
            rows=rows,
            result_payload=result_payload,
            visibility={"stroke_pivot_zones": False, "segment_pivot_zones": True, "volume_panel": False, "macd_panel": False},
            timeframe="day",
            language="en",
            x_window=2,
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
            x_window=2,
        )

        annotation_texts = [ann.text for ann in figure.layout.annotations]
        self.assertIn("笔中枢", annotation_texts)
        self.assertIn("段中枢", annotation_texts)

    def test_build_multi_timeframe_payload_aggregates_levels(self):
        base_analysis = SimpleNamespace(
            warnings=[SimpleNamespace()],
            strokes=[SimpleNamespace()],
            segments=[],
            pivot_zones=[],
            signal_series=[SimpleNamespace()],
            signal_snapshots=[SimpleNamespace(active_signals={"trend_bias": "bullish"})],
            candidate_buy_points=[SimpleNamespace()],
            candidate_sell_points=[],
        )
        higher_analysis = SimpleNamespace(
            warnings=[],
            strokes=[SimpleNamespace(), SimpleNamespace()],
            segments=[SimpleNamespace()],
            pivot_zones=[SimpleNamespace()],
            signal_series=[],
            signal_snapshots=[],
            candidate_buy_points=[],
            candidate_sell_points=[SimpleNamespace()],
        )
        multi_result = SimpleNamespace(
            base_timeframe="day",
            levels=[
                SimpleNamespace(timeframe="day", role="base", bar_count=2, analysis=base_analysis),
                SimpleNamespace(timeframe="week", role="higher", bar_count=1, analysis=higher_analysis),
            ],
        )

        payload = app._build_multi_timeframe_payload(
            multi_result,
            rows_by_timeframe={
                "day": [
                    {"date": "2026-06-11", "close": 10.2},
                    {"date": "2026-06-12", "close": 10.8},
                ],
                "week": [{"date": "2026-06-12", "close": 10.8}],
            },
        )

        self.assertEqual(payload["meta"]["base_timeframe"], "day")
        self.assertEqual(payload["meta"]["level_count"], 2)
        self.assertEqual(payload["meta"]["higher_count"], 1)
        self.assertEqual(payload["levels"][0]["active_signal_count"], 1)
        self.assertEqual(payload["levels"][0]["latest_timestamp"], "2026-06-12")
        self.assertEqual(payload["levels"][1]["candidate_count"], 1)

    def test_build_overview_and_multi_timeframe_card_rows_format_display_data(self):
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
        multi_result = SimpleNamespace(
            levels=[
                SimpleNamespace(timeframe="day"),
                SimpleNamespace(timeframe="week"),
                SimpleNamespace(timeframe="month"),
            ]
        )
        overview_rows = app._build_overview_card_rows(
            result,
            chart_rows=[{"date": "2026-06-12", "close": 10.8}],
            multi_result=multi_result,
            current_bar_payload={"meta": {"active_signal_count": 1, "timestamp": "2026-06-12", "price": 10.8}},
            language="en",
        )
        card_rows = app._build_multi_timeframe_card_rows(
            {
                "role": "higher",
                "latest_timestamp": "2026-06-12",
                "latest_close": 10.8,
                "bar_count": 10,
                "stroke_count": 4,
                "segment_count": 2,
                "pivot_zone_count": 1,
                "signal_series_count": 3,
                "active_signal_count": 1,
                "warning_count": 2,
                "candidate_count": 1,
            },
            language="en",
        )

        self.assertEqual(overview_rows[0]["Field"], "Base Timeframe")
        self.assertEqual(overview_rows[1]["Value"], "day | week | month")
        self.assertEqual(overview_rows[3]["Value"], "10.8")
        self.assertEqual(card_rows[0]["Field"], "Role")
        self.assertEqual(card_rows[0]["Value"], "higher")
        self.assertEqual(card_rows[-1]["Value"], "1")


if __name__ == "__main__":
    unittest.main()
