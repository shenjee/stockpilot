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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from charts.axis_policy import (  # noqa: E402
    build_y_axis_range,
    is_minute_timeframe,
)
from charts.window_policy import (  # noqa: E402
    DEFAULT_SLOTS,
    MAX_SLOTS,
    MIN_SLOTS,
    ZOOM_STEP_DENOMINATOR,
    ZOOM_STEP_MIN,
    default_slots,
    zoom_step,
)
from chantheory import get_default_max_bi_num  # noqa: E402
from charts.figure_builder import build_figure  # noqa: E402
from presenters.signal_tables import (  # noqa: E402
    _build_current_bar_event_table_rows,
    _build_current_bar_signal_summary_rows,
    _build_current_bar_signal_table_rows,
    _build_overview_card_rows,
    _build_signal_timeline_table_rows,
    _format_pivot_zone_count,
    _format_signal_timeline_event,
)
from services.analysis_service import run_analysis  # noqa: E402
from services.market_service import fetch_rows, fetch_stock_name, probe_market_suggestions, search_securities  # noqa: E402
from services.signal_payloads import (  # noqa: E402
    _build_current_bar_signal_payload,
    _build_debug_payload,
    _build_signal_timeline_payload,
)
from ui_text import (  # noqa: E402
    SUPPORTED_LANGUAGES,
    _build_display_summary,
    _build_help_items,
    _display_timestamp,
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
MIN_STOCK_DATE = date(1990, 12, 19)
TIMEFRAME_OPTIONS = ("1m", "5m", "30m", "day", "week", "month")
DEFAULT_TIMEFRAME = "day"
LAYER_KEYS = (
    "fractals",
    "strokes",
    "segments",
    "stroke_pivot_zones",
    "segment_pivot_zones",
    "divergences",
    "candidate_points",
    "volume_panel",
    "macd_panel",
)
Y_ZOOM_STEP = 1.2
MIN_Y_ZOOM = 0.45
MAX_Y_ZOOM = 3.0

_fetch_rows = fetch_rows
_probe_market_suggestions = probe_market_suggestions
_search_securities = search_securities
_build_figure = build_figure
_is_minute_timeframe = is_minute_timeframe
_build_y_axis_range = build_y_axis_range
_default_slots = default_slots
_zoom_step = zoom_step
_run_analysis = run_analysis


def _ordered_rows(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted((dict(item) for item in rows), key=lambda item: str(item["date"]))


# ---------------------------------------------------------------------------
# 证券主数据搜索 / 下拉选择
#
# 单个搜索框（streamlit_searchbox）：输入 code / 名称 / 拼音首字母即联想出
# 匹配项，点选一条。选中项携带 market 和 type；type=='index' 时下游拉 K 线
# 会强制不复权（修指数 qfq 返回空的 bug）。内部值编码为 "market:code:type"。
# ---------------------------------------------------------------------------

_SELECTED_SECURITY_KEY = "chan_security_searchbox"
# 首屏默认选中 上证指数（sh000001）——既验证指数不复权修复，又是常见起点。
_DEFAULT_SECURITY_VALUE = "sh:000001:index"


def _sec_search_options(searchterm: str) -> List[tuple]:
    """streamlit_searchbox 的搜索函数：返回 [(显示文字, 内部值), ...]。

    空输入默认联想 000001（平安银行 + 上证指数），给用户一个起点。
    """

    query = (searchterm or "").strip() or "000001"
    matches = _search_securities(query, limit=10)
    return [
        (f"{m['code']}    {m['name']}", f"{m['market']}:{m['code']}:{m['type']}")
        for m in matches
    ]


def _parse_security_value(value: object) -> Dict[str, object]:
    """内部值 "market:code:type" -> dict；解析失败返回空选。"""

    if not isinstance(value, str) or value.count(":") < 2:
        return {"code": "", "market": "", "type": ""}
    market, code, sec_type = value.split(":", 2)
    return {"code": code, "market": market, "type": sec_type}


def _resolve_security_selection(language: str) -> Dict[str, object]:
    """单搜索框：输入即联想，点选一条拿到 code/market/type。

    streamlit_searchbox 懒导入，避免测试桩环境（无该包）阻塞 app 导入；
    未选中时回退到默认 上证指数。
    """

    from streamlit_searchbox import st_searchbox

    selected = st_searchbox(
        _sec_search_options,
        placeholder=_t(language, "sec_search_placeholder"),
        label=_t(language, "sec_search_label"),
        default=_DEFAULT_SECURITY_VALUE,
        key=_SELECTED_SECURITY_KEY,
    )
    return _parse_security_value(selected or _DEFAULT_SECURITY_VALUE)


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

    with st.sidebar:
        st.markdown(_sidebar_section_title(_t(language, "inputs_header")), unsafe_allow_html=True)
        selected_security = _resolve_security_selection(language)
        symbol = str(selected_security["code"])
        market = str(selected_security["market"])
        security_type = str(selected_security["type"]) or None
        start_date = st.date_input(
            _t(language, "start_date_label"),
            value=DEFAULT_START,
            min_value=MIN_STOCK_DATE,
            max_value=DEFAULT_END,
        )
        end_date = st.date_input(
            _t(language, "end_date_label"),
            value=DEFAULT_END,
            min_value=MIN_STOCK_DATE,
            max_value=DEFAULT_END,
        )
        _default_max_bi = get_default_max_bi_num(st.session_state.chan_selected_timeframe)
        max_bi_num = st.number_input(_t(language, "max_bi_num_label"), min_value=10, max_value=1000, value=_default_max_bi, step=10)
        min_bars = st.number_input(_t(language, "min_bars_label"), min_value=10, max_value=500, value=60, step=10)
        strict_validation = st.checkbox(_t(language, "strict_validation_label"), value=True)
        st.markdown(_sidebar_section_title(_t(language, "layers_header")), unsafe_allow_html=True)
        _default_off = {"segments", "fractals", "stroke_pivot_zones", "segment_pivot_zones"}
        visibility = {layer: st.checkbox(_layer_label(layer, language), value=(layer not in _default_off)) for layer in LAYER_KEYS}
        st.markdown(_sidebar_section_title(_t(language, "display_header")), unsafe_allow_html=True)
        unified_hover = st.checkbox(_t(language, "crosshair_link_label"), value=True)
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
        "security_type": security_type,
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
        rows = _fetch_rows(
            symbol=symbol.strip(),
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            security_type=security_type,
        )
        if not rows:
            st.session_state.pop("chan_chart_rows", None)
            st.session_state.pop("chan_chart_result", None)
            st.session_state.pop("chan_chart_timeframe", None)
            st.session_state.pop("chan_chart_inputs", None)
            st.session_state.pop("chan_chart_stock_name", None)
            st.markdown(_page_title(_t(language, "page_title")), unsafe_allow_html=True)
            suggestions = _probe_market_suggestions(
                symbol=symbol.strip(),
                selected_market=market,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                security_type=security_type,
            )
            if suggestions:
                suggestion_lines = ", ".join(f"{item['market']} ({item['count']} rows)" for item in suggestions)
                st.warning(_t(language, "no_data_market", symbol=symbol.strip(), market=market, suggestions=suggestion_lines))
            else:
                st.warning(_t(language, "no_data_selected"))
            return

        result = _run_analysis(
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
        st.session_state.chan_chart_stock_name = fetch_stock_name(symbol.strip(), market)
    elif "chan_chart_rows" in st.session_state and "chan_chart_result" in st.session_state:
        rows = st.session_state.chan_chart_rows
        result = st.session_state.chan_chart_result
        timeframe = str(st.session_state.get("chan_chart_timeframe", timeframe))
    else:
        st.markdown(_page_title(_t(language, "page_title")), unsafe_allow_html=True)
        st.info(_t(language, "choose_inputs"))
        return

    stock_name = str(st.session_state.get("chan_chart_stock_name", "")).strip()
    if stock_name:
        st.markdown(
            _page_title(_t(language, "page_title_with_symbol", name=stock_name, code=symbol.strip())),
            unsafe_allow_html=True,
        )
    else:
        st.markdown(_page_title(_t(language, "page_title")), unsafe_allow_html=True)

    chart_rows = _ordered_rows(rows)
    row_count = len(chart_rows)
    _x_window = _default_slots()
    figure = _build_figure(
        rows=chart_rows,
        result_payload=result.to_dict(),
        visibility=visibility,
        timeframe=timeframe,
        language=language,
        x_window=_x_window,
        y_zoom=1.0,
        unified_hover=unified_hover,
    )

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
        "useContinuousBarAxis": True,
        "minSlots": MIN_SLOTS,
        "maxSlots": MAX_SLOTS,
        "defaultSlots": _x_window,
        "zoomStepDenominator": ZOOM_STEP_DENOMINATOR,
        "zoomStepMin": ZOOM_STEP_MIN,
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
            "showAll": _t(language, "show_all_label"),
            "fullscreen": _t(language, "fullscreen_label"),
            "helpButton": _t(language, "help_button_label"),
            "helpTitle": _t(language, "help_title"),
            "helpClose": _t(language, "help_close_label"),
            "helpItems": _build_help_items(language),
        },
    }

    returned_timeframe = chan_chart_widget(payload=payload, key="chan_chart_widget_inst")
    if returned_timeframe and returned_timeframe != st.session_state.chan_selected_timeframe:
        st.session_state.chan_selected_timeframe = returned_timeframe
        st.rerun()

    timeline_payload = _build_signal_timeline_payload(result)
    current_bar_payload = _build_current_bar_signal_payload(timeline_payload)
    overview_rows = _build_overview_card_rows(
        result,
        chart_rows=chart_rows,
        current_bar_payload=current_bar_payload,
        language=language,
    )

    st.markdown("---")
    tab_summary, tab_warn, tab_timeline, tab_current_bar, tab_debug = st.tabs([
        _t(language, "tab_summary"),
        _t(language, "tab_warnings"),
        _t(language, "tab_signal_timeline"),
        _t(language, "tab_current_bar_signals"),
        _t(language, "tab_debug"),
    ])

    with tab_summary:
        st.dataframe(overview_rows, width="stretch")
        st.caption(_t(language, "alerts_header"))
        if result.structure_alerts:
            for alert in result.structure_alerts:
                st.write(f"- {_format_alert_message(alert.__dict__, language)}")
        else:
            st.write(_t(language, "no_alerts"))
        for line in _build_display_summary(result, language):
            st.write(f"- {line}")

    with tab_warn:
        if result.warnings:
            for item in result.warnings:
                st.write(f"- [{_format_severity(item.severity, language)}] {item.warning_code}: {_format_warning_message(item, language)}")
        else:
            st.write(_t(language, "no_warnings"))

    with tab_timeline:
        if timeline_payload["rows"]:
            meta = timeline_payload["meta"]
            st.caption(
                _t(
                    language,
                    "signal_timeline_summary",
                    snapshots=meta["snapshot_count"],
                    events=meta["event_count"],
                    series=meta["series_count"],
                )
            )
            st.dataframe(_build_signal_timeline_table_rows(timeline_payload, language), width="stretch")
            with st.expander(_t(language, "signal_timeline_json_header"), expanded=False):
                st.json(timeline_payload)
        else:
            st.write(_t(language, "no_signal_replay"))

    with tab_current_bar:
        summary_rows = _build_current_bar_signal_summary_rows(current_bar_payload, language)
        signal_rows = _build_current_bar_signal_table_rows(current_bar_payload, language)
        event_rows = _build_current_bar_event_table_rows(current_bar_payload, language)
        if summary_rows:
            meta = current_bar_payload["meta"]
            st.caption(
                _t(
                    language,
                    "current_bar_signal_summary",
                    timestamp=_display_timestamp(str(meta.get("timestamp", ""))),
                    active_count=int(meta.get("active_signal_count", 0)),
                    event_count=int(meta.get("event_count", 0)),
                )
            )
            st.dataframe(summary_rows, width="stretch")
            st.dataframe(signal_rows, width="stretch")
            if event_rows:
                st.dataframe(event_rows, width="stretch")
            else:
                st.write(_t(language, "current_bar_no_events"))
            with st.expander(_t(language, "current_bar_json_header"), expanded=False):
                st.json(current_bar_payload)
        else:
            st.write(_t(language, "no_signal_replay"))

    with tab_debug:
        with st.expander(_t(language, "diagnostics_header"), expanded=False):
            st.json(_build_debug_payload(result))
        with st.expander(_t(language, "signals_header"), expanded=False):
            if result.signal_series or result.signal_events or result.signal_snapshots:
                st.json(
                    {
                        "signal_series": result.signal_series,
                        "signal_events": result.signal_events,
                        "signal_snapshots": result.signal_snapshots,
                    }
                )
            else:
                st.write(_t(language, "no_signal_replay"))
        with st.expander(_t(language, "candidate_replay_header"), expanded=False):
            if result.candidate_point_events:
                st.json({"candidate_point_events": result.candidate_point_events})
            else:
                st.write(_t(language, "no_candidate_replay"))
        with st.expander(_t(language, "raw_json_header"), expanded=False):
            st.json({"analysis": result.to_dict()})


if __name__ == "__main__":
    main()
