from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List

import streamlit as st
import streamlit.components.v1 as components

try:
    import plotly.graph_objects as go
except ImportError as exc:  # pragma: no cover - Streamlit runtime guard
    raise SystemExit("plotly is required for apps/chan-streamlit/app.py") from exc

_WIDGET_PATH = Path(__file__).parent / "chan_chart_widget"
chan_chart_widget = components.declare_component("chan_chart_widget", path=str(_WIDGET_PATH))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "skills" / "china-stock-daily-tracker" / "scripts"))

from chantheory import analyze_tracker_klines  # noqa: E402
from chantheory.schema import AnalysisResult, AnalysisWarning  # noqa: E402
from market_data import TencentStockDataProvider  # noqa: E402


DEFAULT_END = date.today()
DEFAULT_START = DEFAULT_END - timedelta(days=240)
SUPPORTED_LANGUAGES = {"zh": "中文", "en": "English"}
TIMEFRAME_OPTIONS = ("1m", "5m", "30m", "60m", "day")
DEFAULT_TIMEFRAME = "day"
MINUTE_TIMEFRAMES = {"1m", "5m", "30m", "60m"}
LAYER_KEYS = ("fractals", "strokes", "segments", "pivot_zones", "divergences", "alerts", "candidate_points")
X_WINDOW_STEPS = (20, 30, 45, 60, 90, 120, 180, 240, 360, 720)
DEFAULT_X_WINDOW = 90
Y_ZOOM_STEP = 1.2
MIN_Y_ZOOM = 0.45
MAX_Y_ZOOM = 3.0
TIMEFRAME_LABELS = {
    "zh": {"1m": "1 分", "5m": "5 分", "15m": "15 分", "30m": "30 分", "60m": "60 分", "day": "日 K", "week": "周 K", "month": "月 K"},
    "en": {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "day": "day", "week": "week", "month": "month"},
}
TEXT = {
    "zh": {
        "page_title": "缠论调试台",
        "language_label": "语言 / Language",
        "inputs_header": "参数",
        "symbol_label": "代码",
        "market_label": "市场",
        "timeframe_label": "周期",
        "start_date_label": "开始日期",
        "end_date_label": "结束日期",
        "max_bi_num_label": "max_bi_num",
        "min_bars_label": "min_bars",
        "strict_validation_label": "严格校验",
        "layers_header": "图层",
        "run_button": "运行分析",
        "choose_inputs": "请选择代码、周期、日期范围和参数，然后运行分析。",
        "no_data_selected": "所选参数未返回 K 线数据。",
        "no_data_market": "代码 `{symbol}` 在市场 `{market}` 下未返回 K 线数据。可尝试：{suggestions}。",
        "kline_overlay": "K 线叠加图",
        "tab_structure": "结构",
        "tab_summary": "摘要",
        "tab_warnings": "告警",
        "tab_debug": "调试",
        "no_alerts": "没有结构提示。",
        "summary_header": "摘要",
        "alerts_header": "结构提示",
        "warnings_header": "告警",
        "no_warnings": "没有告警。",
        "diagnostics_header": "结构诊断",
        "raw_json_header": "原始 JSON",
        "layer_fractals": "显示分型",
        "layer_strokes": "显示笔",
        "layer_segments": "显示线段",
        "layer_pivot_zones": "显示中枢",
        "layer_divergences": "显示背驰",
        "layer_alerts": "显示提示",
        "layer_candidate_points": "显示买卖点",
        "candles_name": "K线",
        "severity_warning": "警告",
        "severity_info": "提示",
        "count_fractals": "分型",
        "count_strokes": "笔",
        "count_segments": "线段",
        "count_pivot_zones": "中枢",
        "count_divergences": "背驰",
        "x_axis_label": "时间轴",
        "y_axis_label": "价格轴",
        "zoom_in_label": "+",
        "zoom_out_label": "-",
        "x_window_caption": "显示最近 {count} 根",
        "y_zoom_caption": "{scale:.2f}x 区间",
        "pan_label": "移动",
        "reset_label": "重置",
        "fullscreen_label": "全屏",
    },
    "en": {
        "page_title": "Chan Theory Debug App",
        "language_label": "Language / 语言",
        "inputs_header": "Inputs",
        "symbol_label": "Symbol",
        "market_label": "Market",
        "timeframe_label": "Timeframe",
        "start_date_label": "Start Date",
        "end_date_label": "End Date",
        "max_bi_num_label": "max_bi_num",
        "min_bars_label": "min_bars",
        "strict_validation_label": "strict_validation",
        "layers_header": "Layers",
        "run_button": "Run Analysis",
        "choose_inputs": "Choose a symbol, timeframe, date range, and parameters, then run analysis.",
        "no_data_selected": "No K-line data returned for the selected input.",
        "no_data_market": "No K-line data returned for `{symbol}` on market `{market}`. Try: {suggestions}.",
        "kline_overlay": "K-Line Overlay",
        "tab_structure": "Structure",
        "tab_summary": "Summary",
        "tab_warnings": "Warnings",
        "tab_debug": "Debug",
        "no_alerts": "No structure alerts.",
        "summary_header": "Summary",
        "alerts_header": "Structure Alerts",
        "warnings_header": "Warnings",
        "no_warnings": "No warnings.",
        "diagnostics_header": "Structure Diagnostics",
        "raw_json_header": "Raw JSON",
        "layer_fractals": "Show Fractals",
        "layer_strokes": "Show Strokes",
        "layer_segments": "Show Segments",
        "layer_pivot_zones": "Show Pivot Zones",
        "layer_divergences": "Show Divergences",
        "layer_alerts": "Show Alerts",
        "layer_candidate_points": "Show Buy/Sell Points",
        "candles_name": "K-line",
        "severity_warning": "warning",
        "severity_info": "info",
        "count_fractals": "fractals",
        "count_strokes": "strokes",
        "count_segments": "segments",
        "count_pivot_zones": "pivot_zones",
        "count_divergences": "divergences",
        "x_axis_label": "Time Axis",
        "y_axis_label": "Price Axis",
        "zoom_in_label": "+",
        "zoom_out_label": "-",
        "x_window_caption": "Last {count} bars",
        "y_zoom_caption": "{scale:.2f}x range",
        "pan_label": "Pan",
        "reset_label": "Reset",
        "fullscreen_label": "Full",
    },
}


def _t(language: str, key: str, **kwargs: object) -> str:
    template = TEXT.get(language, TEXT["en"]).get(key, TEXT["en"].get(key, key))
    return template.format(**kwargs)


def _frontend_template(language: str, key: str) -> str:
    template = TEXT.get(language, TEXT["en"]).get(key, TEXT["en"].get(key, key))
    return template.replace("{scale:.2f}", "{scale}")


def _layer_label(layer: str, language: str) -> str:
    return _t(language, f"layer_{layer}")


def _format_direction(direction: str, language: str) -> str:
    if language == "zh":
        return {"up": "向上", "down": "向下"}.get(direction, direction)
    return direction


def _format_timeframe(timeframe: str, language: str) -> str:
    return TIMEFRAME_LABELS.get(language, TIMEFRAME_LABELS["en"]).get(timeframe, timeframe)


def _display_timestamp(value: str) -> str:
    text = str(value).strip().replace("T", " ")
    text = " ".join(text.split())
    return re.sub(r"\s*:\s*", ":", text)


def _sidebar_section_title(text: str) -> str:
    return f'<div class="sidebar-section-title">{text}</div>'


def _page_title(text: str) -> str:
    return f'<div class="page-title">{text}</div>'


def _format_severity(severity: str, language: str) -> str:
    return _t(language, f"severity_{severity}") if severity in {"warning", "info"} else severity


def _format_warning_message(item: AnalysisWarning, language: str) -> str:
    if language == "en":
        return item.message

    if item.warning_code == "AMOUNT_DERIVED":
        return "缺失的 amount 会在 Phase 1 中暂时用 close * volume 推导。"
    if item.warning_code == "DIVERGENCE_CONSERVATIVE_EMPTY":
        return "Phase 2 中背驰结果暂保持保守为空，等待项目级稳定规则落定。"
    if item.warning_code == "INSUFFICIENT_BARS":
        return "当前 K 线数量偏少，结构映射的稳定性可能不足。"
    if item.warning_code == "UNSTABLE_TAIL_STROKE":
        return "最新一笔仍在延伸，最近结构尚未稳定。"
    if item.warning_code == "SEGMENTS_UNAVAILABLE":
        return "当前输入暂不支持稳定的线段映射，因此线段结果为空。"
    if item.warning_code == "NO_INPUT_BARS":
        return "标准化后没有可用于分析的 K 线。"
    if item.warning_code == "ENGINE_PROBE_FAILED":
        return item.message.replace("czsc probe or mapping failed during Phase 2", "Phase 2 中 czsc 探测或映射失败")
    return item.message


def _format_alert_message(alert: Dict[str, Any], language: str) -> str:
    alert_type = str(alert.get("alert_type", ""))
    meta = dict(alert.get("meta", {}) or {})
    if alert_type == "active_pivot_zone":
        zone_low = meta.get("zone_low")
        zone_high = meta.get("zone_high")
        end_price = meta.get("latest_stroke_end_price")
        position = str(meta.get("latest_stroke_position", "unknown"))
        if language == "zh":
            position_text = {
                "inside": "中枢内部",
                "above": "中枢上方",
                "below": "中枢下方",
                "unknown": "未知位置",
            }.get(position, position)
            if zone_low is not None and zone_high is not None and end_price is not None:
                return (
                    f"最新活跃中枢区间为 {float(zone_low):.2f}-{float(zone_high):.2f}；"
                    f"最新已确认笔终点价为 {float(end_price):.2f}，位于{position_text}。"
                )
            if zone_low is not None and zone_high is not None:
                return f"最新活跃中枢区间为 {float(zone_low):.2f}-{float(zone_high):.2f}。"
        else:
            if zone_low is not None and zone_high is not None and end_price is not None:
                return (
                    f"Latest active pivot zone spans {float(zone_low):.2f}-{float(zone_high):.2f}; "
                    f"the latest confirmed stroke ends {position} the zone at {float(end_price):.2f}."
                )
            if zone_low is not None and zone_high is not None:
                return f"Latest active pivot zone spans {float(zone_low):.2f}-{float(zone_high):.2f}."

    if alert_type == "unstable_tail_stroke":
        direction = _format_direction(str(meta.get("direction", "")), language)
        if language == "zh":
            return f"最新一笔仍在延伸，方向为{direction}，尾部结构需按未稳定处理。"
        return f"The latest stroke is still extending ({direction}) and should be treated as unstable."

    return str(alert.get("message", ""))


def _build_display_summary(result: AnalysisResult, language: str) -> List[str]:
    lines: List[str] = []
    bar_count = int(result.meta.get("bar_count", 0))
    engine_probe = result.meta.get("engine_probe", {})
    mapping = result.meta.get("mapping", {})

    if bar_count:
        if language == "zh":
            lines.append(f"{result.symbol} 已标准化 {bar_count} 根{_format_timeframe(result.timeframe, language)}K线，用于 Phase 2 分析。")
        else:
            lines.append(f"{result.symbol} normalized {bar_count} {_format_timeframe(result.timeframe, language)} bars for Phase 2 analysis.")
    else:
        lines.append(
            f"{result.symbol} has no normalized bars available for Phase 2 analysis."
            if language == "en"
            else f"{result.symbol} 当前没有可用于 Phase 2 分析的标准化 K 线。"
        )

    if engine_probe.get("status") == "ok":
        fractal_count = int(engine_probe.get("fractal_count", 0))
        finished_bi_count = int(engine_probe.get("finished_bi_count", 0))
        if language == "zh":
            lines.append(f"czsc {result.engine_version} 映射出 {fractal_count} 个分型和 {finished_bi_count} 笔已完成笔。")
        else:
            lines.append(f"czsc {result.engine_version} mapped {fractal_count} fractals and {finished_bi_count} finished strokes.")

        if result.segments or result.pivot_zones:
            segment_count = int(mapping.get("segment_count", len(result.segments)))
            pivot_zone_count = int(mapping.get("pivot_zone_count", len(result.pivot_zones)))
            if language == "zh":
                lines.append(f"Phase 2 产出 {segment_count} 个线段和 {pivot_zone_count} 个中枢。")
            else:
                lines.append(f"Phase 2 produced {segment_count} segments and {pivot_zone_count} pivot zones.")

        if result.strokes:
            last_stroke = result.strokes[-1]
            if language == "zh":
                lines.append(
                    f"最新一笔已确认笔方向为{_format_direction(last_stroke.direction, language)}，终点时间为 {_display_timestamp(last_stroke.end_timestamp)}。"
                )
            else:
                lines.append(
                    f"The latest confirmed stroke points {last_stroke.direction} into {_display_timestamp(last_stroke.end_timestamp)}."
                )

        if result.candidate_buy_points:
            zh_names = {"first_buy": "一买", "second_buy": "二买", "third_buy": "三买", "structure_buy_candidate": "保守买点"}
            en_names = {"first_buy": "first-buy", "second_buy": "second-buy", "third_buy": "third-buy", "structure_buy_candidate": "conservative buy-point"}
            
            for p in sorted(result.candidate_buy_points, key=lambda x: x.timestamp):
                t = p.point_type
                if language == "zh":
                    name = zh_names.get(t, "买点")
                    lines.append(f"买点提示：在 {_display_timestamp(p.timestamp)} 发现【{name}】，价格 {p.price:.2f}。")
                else:
                    name = en_names.get(t, "buy-point")
                    lines.append(f"Buy Point: [{name}] candidate at {_display_timestamp(p.timestamp)}, price {p.price:.2f}.")

        if result.candidate_sell_points:
            zh_names = {"first_sell": "一卖", "second_sell": "二卖", "third_sell": "三卖", "structure_sell_candidate": "保守卖点"}
            en_names = {"first_sell": "first-sell", "second_sell": "second-sell", "third_sell": "third-sell", "structure_sell_candidate": "conservative sell-point"}
            
            for p in sorted(result.candidate_sell_points, key=lambda x: x.timestamp):
                t = p.point_type
                if language == "zh":
                    name = zh_names.get(t, "卖点")
                    lines.append(f"卖点提示：在 {_display_timestamp(p.timestamp)} 发现【{name}】，价格 {p.price:.2f}。")
                else:
                    name = en_names.get(t, "sell-point")
                    lines.append(f"Sell Point: [{name}] candidate at {_display_timestamp(p.timestamp)}, price {p.price:.2f}.")
        if any(item.warning_code == "UNSTABLE_TAIL_STROKE" for item in result.warnings):
            lines.append("最新结构仍在延伸，尾部应按未稳定结构处理。" if language == "zh" else "The newest structure is still extending, so the tail should be treated as unstable.")
        return lines

    lines.append(
        "czsc 探测不可用，适配层返回带告警的冻结 schema。"
        if language == "zh"
        else "czsc probe is unavailable, so the adapter returns the frozen schema with warnings."
    )
    if result.warnings:
        lines.append(
            f"共记录 {len(result.warnings)} 条与标准化或引擎就绪状态相关的告警。"
            if language == "zh"
            else f"{len(result.warnings)} warning(s) recorded for normalization or engine readiness."
        )
    return lines


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
        header[data-testid="stHeader"] {
            display: none;
        }
        .block-container {
            padding-top: 0.75rem;
            padding-bottom: 2rem;
        }
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
            padding-top: 0.25rem;
        }
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {
            padding-top: 0.25rem;
        }
        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            min-height: 0px !important;
            height: 0px !important;
            margin-bottom: 4px !important;
            align-items: flex-start !important;
        }
        .sidebar-section-title {
            font-size: 1.1rem;
            font-weight: 700;
            line-height: 1.3;
            margin: 0.25rem 0 0.5rem 0;
        }
        .page-title {
            font-size: 1.2rem;
            font-weight: 700;
            line-height: 1.25;
            margin: 0 0 0.25rem 0;
        }
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
        visibility = {
            layer: st.checkbox(_layer_label(layer, language), value=(layer != "segments"))
            for layer in LAYER_KEYS
        }
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
        rows = _fetch_rows(
            symbol=symbol.strip(),
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
        )
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
                suggestion_lines = ", ".join(
                    f"{item['market']} ({item['count']} rows)" for item in suggestions
                )
                st.warning(_t(language, "no_data_market", symbol=symbol.strip(), market=market, suggestions=suggestion_lines))
            else:
                st.warning(_t(language, "no_data_selected"))
            return

        result = analyze_tracker_klines(
            rows=rows,
            code=symbol.strip(),
            market=market,
            timeframe=timeframe,
            parameters={
                "max_bi_num": int(max_bi_num),
                "min_bars": int(min_bars),
                "strict_validation": bool(strict_validation),
            },
            strict=bool(strict_validation),
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
    x_steps = [
        n
        for n in [30, 60, 90, 120, 240, 360, 480, row_count]
        if n <= row_count
    ]
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
        "timeframes": [
            {"value": tf, "label": _format_timeframe(tf, language)}
            for tf in TIMEFRAME_OPTIONS
        ],
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
            "xWindowCaption": _frontend_template(language, "x_window_caption"),
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
                    "rendering": {
                        "count_plot_primitives": len(result.plot_primitives)
                    },
                    "engine_assumptions": result.meta.get("engine_assumptions", {}),
                }
            )

        with st.expander(_t(language, "raw_json_header"), expanded=False):
            st.json(result.to_dict())


def _fetch_rows(
    symbol: str,
    market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> List[Dict[str, object]]:
    provider = TencentStockDataProvider()
    return provider.get_kline(
        code=symbol,
        market=market,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        ktype=timeframe,
    )


def _probe_market_suggestions(
    symbol: str,
    selected_market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> List[Dict[str, object]]:
    provider = TencentStockDataProvider()
    suggestions: List[Dict[str, object]] = []
    for candidate in ["sh", "sz", "bj"]:
        if candidate == selected_market:
            continue
        rows = provider.get_kline(
            code=symbol,
            market=candidate,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            ktype=timeframe,
        )
        if rows:
            suggestions.append({"market": candidate, "count": len(rows)})
    return suggestions


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


def _build_figure(
    rows: Iterable[Dict[str, object]],
    result_payload: Dict[str, object],
    visibility: Dict[str, bool],
    timeframe: str,
    language: str,
    x_window: int | None = None,
    y_zoom: float = 1.0,
) -> go.Figure:
    ordered_rows = sorted(rows, key=lambda item: str(item["date"]))
    x_values = [item["date"] for item in ordered_rows]
    visible_rows = ordered_rows[-x_window:] if x_window else ordered_rows
    visible_x_values = [item["date"] for item in visible_rows]
    use_continuous_bar_axis = _is_minute_timeframe(timeframe)
    
    customdata = []
    for item in ordered_rows:
        date_str = str(item["date"])
        day = date_str[:10]
        time_part = date_str[11:16]
        
        if timeframe == "60m":
            if time_part == "10:30":
                t_start = "09:30"
            elif time_part == "11:30":
                t_start = "10:30"
            elif time_part == "14:00":
                t_start = "13:00"
            elif time_part == "15:00":
                t_start = "14:00"
            else:
                t_start = ""
            time_range = f"{t_start} - {time_part}" if t_start else time_part
        elif timeframe == "30m":
            if time_part == "10:00":
                t_start = "09:30"
            elif time_part == "10:30":
                t_start = "10:00"
            elif time_part == "11:00":
                t_start = "10:30"
            elif time_part == "11:30":
                t_start = "11:00"
            elif time_part == "13:30":
                t_start = "13:00"
            elif time_part == "14:00":
                t_start = "13:30"
            elif time_part == "14:30":
                t_start = "14:00"
            elif time_part == "15:00":
                t_start = "14:30"
            else:
                t_start = ""
            time_range = f"{t_start} - {time_part}" if t_start else time_part
        else:
            time_range = time_part
            
        direction_icon = "▲" if float(item["close"]) >= float(item["open"]) else "▼"
        customdata.append([day, time_range, direction_icon])
        
    hovertemplate = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "open: %{open}<br>"
        "high: %{high}<br>"
        "low: %{low}<br>"
        "close: %{close} %{customdata[2]}<extra></extra>"
    ) if use_continuous_bar_axis else (
        "<b>%{x}</b><br>"
        "open: %{open}<br>"
        "high: %{high}<br>"
        "low: %{low}<br>"
        "close: %{close} %{customdata[2]}<extra></extra>"
    )

    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=x_values,
            open=[float(item["open"]) for item in ordered_rows],
            high=[float(item["high"]) for item in ordered_rows],
            low=[float(item["low"]) for item in ordered_rows],
            close=[float(item["close"]) for item in ordered_rows],
            name=_t(language, "candles_name"),
            customdata=customdata,
            hovertemplate=hovertemplate,
        )
    )

    for primitive in result_payload.get("plot_primitives", []):
        layer = str(primitive.get("layer", ""))
        if not visibility.get(layer, True):
            continue
        primitive_type = primitive.get("type")
        if primitive_type == "marker":
            primitive_meta = dict(primitive.get("meta", {}) or {})
            style = primitive.get("style", "circle")
            
            if style == "text":
                figure.add_trace(
                    go.Scatter(
                        x=[primitive.get("x")],
                        y=[primitive.get("y")],
                        mode="text",
                        text=[primitive.get("text", "")],
                        textposition=str(primitive_meta.get("textposition", "top center")),
                        textfont={"color": primitive.get("color", "#2563EB")},
                        name=layer,
                        showlegend=False,
                    )
                )
            else:
                style_mapping = {
                    "triangle_up": "triangle-up",
                    "triangle_down": "triangle-down",
                    "circle": "circle",
                    "diamond": "diamond",
                }
                marker_symbol = style_mapping.get(style, "circle")
                
                figure.add_trace(
                    go.Scatter(
                        x=[primitive.get("x")],
                        y=[primitive.get("y")],
                        mode="markers+text",
                        text=[primitive.get("text", "")],
                        textposition=str(primitive_meta.get("textposition", "top center")),
                        marker={"color": primitive.get("color", "#2563EB"), "size": 10, "symbol": marker_symbol},
                        textfont={"color": primitive.get("color", "#2563EB")},
                        name=layer,
                        showlegend=False,
                    )
                )
        elif primitive_type == "line":
            primitive_meta = primitive.get("meta") or {}
            width_multiplier = float(primitive_meta.get("width_multiplier", 1.0))
            figure.add_trace(
                go.Scatter(
                    x=[primitive.get("x1"), primitive.get("x2")],
                    y=[primitive.get("y1"), primitive.get("y2")],
                    mode="lines",
                    line={
                        "color": primitive.get("color", "#2563EB"),
                        "dash": "dash" if primitive.get("style") == "dashed" else "solid",
                        "width": int(2 * width_multiplier),
                    },
                    name=layer,
                    showlegend=False,
                )
            )
        elif primitive_type == "box":
            figure.add_shape(
                type="rect",
                x0=primitive.get("x1"),
                x1=primitive.get("x2"),
                y0=primitive.get("y2"),
                y1=primitive.get("y1"),
                line={"color": primitive.get("color", "#F59E0B"), "width": 2},
                fillcolor=_to_rgba(str(primitive.get("color", "#F59E0B")), 0.18),
            )
            if primitive.get("text"):
                figure.add_annotation(
                    x=primitive.get("x2"),
                    y=primitive.get("y1"),
                    text="中枢" if language == "zh" else primitive.get("text"),
                    showarrow=False,
                    font={"color": primitive.get("color", "#F59E0B")},
                )
        elif primitive_type == "label":
            figure.add_annotation(
                x=primitive.get("x"),
                y=primitive.get("y"),
                text=_format_alert_message(
                    {
                        "alert_type": primitive.get("meta", {}).get("alert_type", ""),
                        "meta": primitive.get("meta", {}).get("alert_meta", {}),
                        "message": primitive.get("text", ""),
                    },
                    language,
                ),
                showarrow=True,
                arrowcolor=primitive.get("color", "#0F766E"),
                font={"color": primitive.get("color", "#0F766E")},
            )

    figure.update_layout(
        margin={"l": 20, "r": 24, "t": 30, "b": 78},
        dragmode="pan",
        xaxis_rangeslider_visible=False,
        showlegend=False,
        template="plotly_white",
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
    )
    if timeframe == "day":
        rangebreaks = _build_daily_rangebreaks(x_values)
        if rangebreaks:
            figure.update_xaxes(rangebreaks=rangebreaks)
    elif use_continuous_bar_axis:
        tick_values, tick_text = _build_intraday_date_ticks(x_values)
        figure.update_xaxes(
            type="category",
            categoryorder="array",
            categoryarray=x_values,
            tickmode="array",
            tickvals=tick_values,
            ticktext=tick_text,
        )
    if visible_x_values:
        figure.update_xaxes(range=_build_x_axis_range(x_values, visible_x_values, use_continuous_bar_axis))
    y_range = _build_y_axis_range(visible_rows, y_zoom)
    if y_range:
        figure.update_yaxes(range=y_range)
    return figure


def _is_minute_timeframe(timeframe: str) -> bool:
    return timeframe in MINUTE_TIMEFRAMES


def _build_x_axis_range(x_values: List[object], visible_x_values: List[object], use_continuous_bar_axis: bool) -> List[object]:
    if not visible_x_values:
        return []
    if not use_continuous_bar_axis:
        return [visible_x_values[0], visible_x_values[-1]]

    index_by_value = {value: index for index, value in enumerate(x_values)}
    start_index = index_by_value.get(visible_x_values[0], 0)
    end_index = index_by_value.get(visible_x_values[-1], max(len(x_values) - 1, 0))
    return [max(start_index - 0.5, -0.5), end_index + 0.5]


def _build_intraday_date_ticks(x_values: List[object]) -> tuple[List[object], List[str]]:
    if not x_values:
        return [], []

    tick_values: List[object] = []
    tick_text: List[str] = []
    previous_day = ""
    for value in x_values:
        text = str(value)
        day = text[:10]
        if day != previous_day:
            tick_values.append(value)
            tick_text.append(day)
            previous_day = day

    return tick_values, tick_text


def _build_y_axis_range(rows: List[Dict[str, object]], y_zoom: float) -> List[float] | None:
    if not rows:
        return None
    lows = [float(item["low"]) for item in rows]
    highs = [float(item["high"]) for item in rows]
    low = min(lows)
    high = max(highs)
    if high <= low:
        padding = max(abs(high) * 0.02, 0.01)
        return [low - padding, high + padding]
    span = high - low
    padding = span * 0.08 * y_zoom
    return [low - padding, high + padding]


def _to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "rgba(245, 158, 11, 0.18)"
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _build_daily_rangebreaks(x_values: List[object]) -> List[Dict[str, object]]:
    trading_days: List[date] = []
    for value in x_values:
        try:
            trading_days.append(datetime.strptime(str(value), "%Y-%m-%d").date())
        except ValueError:
            return []

    if len(trading_days) < 2:
        return [{"bounds": ["sat", "mon"]}]

    missing_days: List[str] = []
    trading_day_set = set(trading_days)
    current_day = trading_days[0]
    last_day = trading_days[-1]
    while current_day <= last_day:
        if current_day not in trading_day_set:
            missing_days.append(current_day.strftime("%Y-%m-%d"))
        current_day += timedelta(days=1)

    rangebreaks: List[Dict[str, object]] = [{"bounds": ["sat", "mon"]}]
    if missing_days:
        rangebreaks.append({"values": missing_days})
    return rangebreaks


if __name__ == "__main__":
    main()
