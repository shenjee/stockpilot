from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List

import streamlit as st
import streamlit.components.v1 as components

_WIDGET_PATH = Path(__file__).parent / "chan_chart_widget"
chan_chart_widget = components.declare_component("chan_chart_widget", path=str(_WIDGET_PATH))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "skills" / "china-stock-analysis" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from charts.axis_policy import (  # noqa: E402
    build_daily_rangebreaks,
    build_intraday_date_ticks,
    build_x_axis_range,
    build_y_axis_range,
    is_minute_timeframe,
)
from charts.figure_builder import build_figure  # noqa: E402
from services.analysis_service import run_analysis  # noqa: E402
from services.market_service import fetch_rows, probe_market_suggestions  # noqa: E402
from ui_text import (  # noqa: E402
    SUPPORTED_LANGUAGES,
    _build_display_summary,
    _format_alert_message,
    _format_severity,
    _format_timeframe,
    _format_warning_message,
    _frontend_template,
    _layer_label,
    _page_title,
    _sidebar_section_title,
    _t,
)


DEFAULT_END = date.today()
DEFAULT_START = DEFAULT_END - timedelta(days=240)
TIMEFRAME_OPTIONS = ("1m", "5m", "30m", "60m", "day")
DEFAULT_TIMEFRAME = "day"
LAYER_KEYS = ("fractals", "strokes", "segments", "pivot_zones", "divergences", "alerts", "candidate_points")
DEFAULT_X_WINDOW = 90
Y_ZOOM_STEP = 1.2
MIN_Y_ZOOM = 0.45
MAX_Y_ZOOM = 3.0

_fetch_rows = fetch_rows
_probe_market_suggestions = probe_market_suggestions
_build_figure = build_figure
_is_minute_timeframe = is_minute_timeframe
_build_x_axis_range = build_x_axis_range
_build_intraday_date_ticks = build_intraday_date_ticks
_build_y_axis_range = build_y_axis_range
_build_daily_rangebreaks = build_daily_rangebreaks


def _ordered_rows(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted((dict(item) for item in rows), key=lambda item: str(item["date"]))


def _build_chart_key(
    symbol: str,
    market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    rows: List[Dict[str, object]],
) -> str:
    first_date = str(rows[0]["date"]) if rows else ""
    last_date = str(rows[-1]["date"]) if rows else ""
    return "|".join([
        symbol,
        market,
        timeframe,
        start_date.isoformat(),
        end_date.isoformat(),
        str(len(rows)),
        first_date,
        last_date,
    ])


def main() -> None:
    if "language" not in st.session_state:
        st.session_state.language = "zh"
    if "chan_selected_timeframe" not in st.session_state:
        st.session_state.chan_selected_timeframe = DEFAULT_TIMEFRAME
    language = str(st.session_state.language)

    st.set_page_config(page_title=_t("zh", "page_title"), layout="wide")
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"] { display: none; }
        .block-container { padding-top: 0.75rem; padding-bottom: 2rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            min-height: 0px !important;
            height: 0px !important;
            margin-bottom: 4px !important;
            align-items: flex-start !important;
        }
        .sidebar-section-title { font-size: 1.1rem; font-weight: 700; line-height: 1.3; margin: 0.25rem 0 0.5rem 0; }
        .page-title { font-size: 1.2rem; font-weight: 700; line-height: 1.25; margin: 0 0 0.25rem 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(_page_title(_t(language, "page_title")), unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(_sidebar_section_title(_t(language, "inputs_header")), unsafe_allow_html=True)
        symbol = st.text_input(_t(language, "symbol_label"), value="000001")
        market = st.selectbox(_t(language, "market_label"), ["sz", "sh", "bj"], index=0)
        start_date = st.date_input(_t(language, "start_date_label"), value=DEFAULT_START)
        end_date = st.date_input(_t(language, "end_date_label"), value=DEFAULT_END)
        max_bi_num = st.number_input(_t(language, "max_bi_num_label"), min_value=10, max_value=200, value=50, step=10)
        min_bars = st.number_input(_t(language, "min_bars_label"), min_value=10, max_value=500, value=60, step=10)
        strict_validation = st.checkbox(_t(language, "strict_validation_label"), value=True)
        st.markdown(_sidebar_section_title(_t(language, "layers_header")), unsafe_allow_html=True)
        visibility = {layer: st.checkbox(_layer_label(layer, language), value=(layer != "segments")) for layer in LAYER_KEYS}
        run = st.button(_t(language, "run_button"), type="primary")
        st.selectbox(
            _t(language, "language_label"),
            list(SUPPORTED_LANGUAGES.keys()),
            format_func=lambda code: SUPPORTED_LANGUAGES[code],
            key="language",
        )

    timeframe = st.session_state.chan_selected_timeframe
    analysis_inputs = {
        "symbol": symbol.strip(),
        "market": market,
        "timeframe": timeframe,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "max_bi_num": int(max_bi_num),
        "min_bars": int(min_bars),
        "strict_validation": bool(strict_validation),
    }
    cached_inputs = st.session_state.get("chan_chart_inputs")
    should_analyze = bool(run) or (
        cached_inputs is not None
        and cached_inputs != analysis_inputs
        and "chan_chart_rows" in st.session_state
        and "chan_chart_result" in st.session_state
    )

    if should_analyze:
        rows = _fetch_rows(symbol=symbol.strip(), market=market, timeframe=timeframe, start_date=start_date, end_date=end_date)
        if not rows:
            st.session_state.pop("chan_chart_rows", None)
            st.session_state.pop("chan_chart_result", None)
            st.session_state.pop("chan_chart_timeframe", None)
            st.session_state.pop("chan_chart_inputs", None)
            suggestions = _probe_market_suggestions(
                symbol=symbol.strip(),
                selected_market=market,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
            )
            if suggestions:
                suggestion_lines = ", ".join(f"{item['market']} ({item['count']} rows)" for item in suggestions)
                st.warning(_t(language, "no_data_market", symbol=symbol.strip(), market=market, suggestions=suggestion_lines))
            else:
                st.warning(_t(language, "no_data_selected"))
            return

        result = run_analysis(
            rows=rows,
            symbol=symbol.strip(),
            market=market,
            timeframe=timeframe,
            max_bi_num=int(max_bi_num),
            min_bars=int(min_bars),
            strict_validation=bool(strict_validation),
        )
        st.session_state.chan_chart_rows = rows
        st.session_state.chan_chart_result = result
        st.session_state.chan_chart_timeframe = timeframe
        st.session_state.chan_chart_inputs = analysis_inputs
    elif "chan_chart_rows" in st.session_state and "chan_chart_result" in st.session_state:
        rows = st.session_state.chan_chart_rows
        result = st.session_state.chan_chart_result
        timeframe = str(st.session_state.get("chan_chart_timeframe", timeframe))
    else:
        st.info(_t(language, "choose_inputs"))
        return

    chart_rows = _ordered_rows(rows)
    figure = _build_figure(
        rows=chart_rows,
        result_payload=result.to_dict(),
        visibility=visibility,
        timeframe=timeframe,
        language=language,
        x_window=min(DEFAULT_X_WINDOW, max(len(chart_rows), 1)),
        y_zoom=1.0,
    )

    row_count = len(chart_rows)
    x_steps = [n for n in [30, 60, 90, 120, 240, 360, 480, row_count] if n <= row_count]
    if row_count not in x_steps:
        x_steps.append(row_count)

    payload = {
        "figure": json.loads(figure.to_json()),
        "rows": chart_rows,
        "chartKey": _build_chart_key(
            symbol=symbol.strip(),
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            rows=chart_rows,
        ),
        "timeframes": [{"value": tf, "label": _format_timeframe(tf, language)} for tf in TIMEFRAME_OPTIONS],
        "activeTimeframe": timeframe,
        "useContinuousBarAxis": _is_minute_timeframe(timeframe),
        "xWindowSteps": x_steps,
        "defaultXWindow": min(DEFAULT_X_WINDOW, max(row_count, 1)),
        "defaultYZoom": 1.0,
        "yZoomStep": Y_ZOOM_STEP,
        "minYZoom": MIN_Y_ZOOM,
        "maxYZoom": MAX_Y_ZOOM,
        "text": {
            "xAxisLabel": _t(language, "x_axis_label"),
            "yAxisLabel": _t(language, "y_axis_label"),
            "zoomIn": _t(language, "zoom_in_label"),
            "zoomOut": _t(language, "zoom_out_label"),
            "xWindowCaption": _t(language, "x_window_caption", count="{count}"),
            "yZoomCaption": _frontend_template(language, "y_zoom_caption"),
            "pan": _t(language, "pan_label"),
            "reset": _t(language, "reset_label"),
            "fullscreen": _t(language, "fullscreen_label"),
        },
    }

    returned_timeframe = chan_chart_widget(payload=payload, key="chan_chart_widget_inst")
    if returned_timeframe and returned_timeframe != st.session_state.chan_selected_timeframe:
        st.session_state.chan_selected_timeframe = returned_timeframe
        st.rerun()

    st.markdown("---")
    tab_struct, tab_summary, tab_warn, tab_debug = st.tabs([
        _t(language, "tab_structure"),
        _t(language, "tab_summary"),
        _t(language, "tab_warnings"),
        _t(language, "tab_debug"),
    ])

    with tab_struct:
        if result.structure_alerts:
            for alert in result.structure_alerts:
                st.write(f"- {_format_alert_message(alert.__dict__, language)}")
        else:
            st.write(_t(language, "no_alerts"))

    with tab_summary:
        for line in _build_display_summary(result, language):
            st.write(f"- {line}")

    with tab_warn:
        if result.warnings:
            for item in result.warnings:
                st.write(f"- [{_format_severity(item.severity, language)}] {item.warning_code}: {_format_warning_message(item, language)}")
        else:
            st.write(_t(language, "no_warnings"))

    with tab_debug:
        with st.expander(_t(language, "diagnostics_header"), expanded=False):
            st.json(
                {
                    "engine_probe": result.meta.get("engine_probe", {}),
                    "mapping": result.meta.get("mapping", {}),
                    "rendering": {"count_plot_primitives": len(result.plot_primitives)},
                    "engine_assumptions": result.meta.get("engine_assumptions", {}),
                }
            )
        with st.expander(_t(language, "raw_json_header"), expanded=False):
            st.json(result.to_dict())


if __name__ == "__main__":
    main()
