from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List

import streamlit as st

try:
    import plotly.graph_objects as go
except ImportError as exc:  # pragma: no cover - Streamlit runtime guard
    raise SystemExit("plotly is required for apps/chan-streamlit/app.py") from exc


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "skills" / "china-stock-daily-tracker" / "scripts"))

from chantheory import analyze_tracker_klines  # noqa: E402
from chantheory.schema import AnalysisResult, AnalysisWarning  # noqa: E402
from market_data import TencentStockDataProvider  # noqa: E402


DEFAULT_END = date.today()
DEFAULT_START = DEFAULT_END - timedelta(days=240)
SUPPORTED_LANGUAGES = {"zh": "中文", "en": "English"}
LAYER_KEYS = ("fractals", "strokes", "segments", "pivot_zones", "divergences", "alerts")
TIMEFRAME_LABELS = {
    "zh": {"1m": "1分钟", "5m": "5分钟", "15m": "15分钟", "30m": "30分钟", "60m": "60分钟", "day": "日线", "week": "周线", "month": "月线"},
    "en": {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "day": "day", "week": "week", "month": "month"},
}
TEXT = {
    "zh": {
        "page_title": "缠论调试台",
        "page_caption": "用于检查 Phase 2 结构映射、绘图原语、告警信息与原始 JSON 的 Streamlit 调试页面。",
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
        "summary_header": "摘要",
        "alerts_header": "结构提示",
        "warnings_header": "告警",
        "no_warnings": "没有告警。",
        "diagnostics_header": "诊断信息",
        "structure_counts_header": "结构统计",
        "raw_json_header": "原始 JSON",
        "layer_fractals": "显示分型",
        "layer_strokes": "显示笔",
        "layer_segments": "显示线段",
        "layer_pivot_zones": "显示中枢",
        "layer_divergences": "显示背驰",
        "layer_alerts": "显示提示",
        "candles_name": "K线",
        "severity_warning": "警告",
        "severity_info": "提示",
        "count_fractals": "分型",
        "count_strokes": "笔",
        "count_segments": "线段",
        "count_pivot_zones": "中枢",
        "count_divergences": "背驰",
        "count_plot_primitives": "绘图原语",
    },
    "en": {
        "page_title": "Chan Theory Debug App",
        "page_caption": "Phase 2 Streamlit tool for checking mapped structures, plot primitives, warnings, and raw JSON.",
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
        "summary_header": "Summary",
        "alerts_header": "Structure Alerts",
        "warnings_header": "Warnings",
        "no_warnings": "No warnings.",
        "diagnostics_header": "Diagnostics",
        "structure_counts_header": "Structure Counts",
        "raw_json_header": "Raw JSON",
        "layer_fractals": "Show Fractals",
        "layer_strokes": "Show Strokes",
        "layer_segments": "Show Segments",
        "layer_pivot_zones": "Show Pivot Zones",
        "layer_divergences": "Show Divergences",
        "layer_alerts": "Show Alerts",
        "candles_name": "K-line",
        "severity_warning": "warning",
        "severity_info": "info",
        "count_fractals": "fractals",
        "count_strokes": "strokes",
        "count_segments": "segments",
        "count_pivot_zones": "pivot_zones",
        "count_divergences": "divergences",
        "count_plot_primitives": "plot_primitives",
    },
}


def _t(language: str, key: str, **kwargs: object) -> str:
    template = TEXT.get(language, TEXT["en"]).get(key, TEXT["en"].get(key, key))
    return template.format(**kwargs)


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
            lines.append("存在一个保守买点候选，但尚未确认。" if language == "zh" else "A conservative buy-point candidate is present but not confirmed.")
        if result.candidate_sell_points:
            lines.append("存在一个保守卖点候选，但尚未确认。" if language == "zh" else "A conservative sell-point candidate is present but not confirmed.")
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
    st.caption(_t(language, "page_caption"))

    with st.sidebar:
        st.markdown(_sidebar_section_title(_t(language, "inputs_header")), unsafe_allow_html=True)
        symbol = st.text_input(_t(language, "symbol_label"), value="000001")
        market = st.selectbox(_t(language, "market_label"), ["sz", "sh", "bj"], index=0)
        timeframe = st.selectbox(_t(language, "timeframe_label"), ["day", "week", "month"], index=0)
        start_date = st.date_input(_t(language, "start_date_label"), value=DEFAULT_START)
        end_date = st.date_input(_t(language, "end_date_label"), value=DEFAULT_END)
        max_bi_num = st.number_input(_t(language, "max_bi_num_label"), min_value=10, max_value=200, value=50, step=10)
        min_bars = st.number_input(_t(language, "min_bars_label"), min_value=10, max_value=500, value=60, step=10)
        strict_validation = st.checkbox(_t(language, "strict_validation_label"), value=True)
        st.markdown(_sidebar_section_title(_t(language, "layers_header")), unsafe_allow_html=True)
        visibility = {
            layer: st.checkbox(_layer_label(layer, language), value=True)
            for layer in LAYER_KEYS
        }
        run = st.button(_t(language, "run_button"), type="primary")
        st.selectbox(
            _t(language, "language_label"),
            list(SUPPORTED_LANGUAGES.keys()),
            format_func=lambda code: SUPPORTED_LANGUAGES[code],
            key="language",
        )

    if not run:
        st.info(_t(language, "choose_inputs"))
        return

    rows = _fetch_rows(
        symbol=symbol.strip(),
        market=market,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    if not rows:
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

    chart_col, side_col = st.columns([3, 2])
    with chart_col:
        st.subheader(_t(language, "kline_overlay"))
        st.plotly_chart(
            _build_figure(
                rows=rows,
                result_payload=result.to_dict(),
                visibility=visibility,
                timeframe=timeframe,
                language=language,
            ),
            width="stretch",
        )

    with side_col:
        st.subheader(_t(language, "summary_header"))
        for line in _build_display_summary(result, language):
            st.write(f"- {line}")

        if result.structure_alerts:
            st.subheader(_t(language, "alerts_header"))
            for alert in result.structure_alerts:
                st.write(f"- {_format_alert_message(alert.__dict__, language)}")

        st.subheader(_t(language, "warnings_header"))
        if result.warnings:
            for item in result.warnings:
                st.write(f"- [{_format_severity(item.severity, language)}] {item.warning_code}: {_format_warning_message(item, language)}")
        else:
            st.write(_t(language, "no_warnings"))

        with st.expander(_t(language, "diagnostics_header"), expanded=False):
            st.json(
                {
                    "engine_probe": result.meta.get("engine_probe", {}),
                    "mapping": result.meta.get("mapping", {}),
                    "engine_assumptions": result.meta.get("engine_assumptions", {}),
                }
            )

    with st.expander(_t(language, "structure_counts_header"), expanded=False):
        st.json(
            {
                _t(language, "count_fractals"): len(result.fractals),
                _t(language, "count_strokes"): len(result.strokes),
                _t(language, "count_segments"): len(result.segments),
                _t(language, "count_pivot_zones"): len(result.pivot_zones),
                _t(language, "count_divergences"): len(result.divergences),
                _t(language, "count_plot_primitives"): len(result.plot_primitives),
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


def _build_figure(
    rows: Iterable[Dict[str, object]],
    result_payload: Dict[str, object],
    visibility: Dict[str, bool],
    timeframe: str,
    language: str,
) -> go.Figure:
    ordered_rows = sorted(rows, key=lambda item: str(item["date"]))
    x_values = [item["date"] for item in ordered_rows]
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=x_values,
            open=[float(item["open"]) for item in ordered_rows],
            high=[float(item["high"]) for item in ordered_rows],
            low=[float(item["low"]) for item in ordered_rows],
            close=[float(item["close"]) for item in ordered_rows],
            name=_t(language, "candles_name"),
        )
    )

    for primitive in result_payload.get("plot_primitives", []):
        layer = str(primitive.get("layer", ""))
        if not visibility.get(layer, True):
            continue
        primitive_type = primitive.get("type")
        if primitive_type == "marker":
            primitive_meta = dict(primitive.get("meta", {}) or {})
            figure.add_trace(
                go.Scatter(
                    x=[primitive.get("x")],
                    y=[primitive.get("y")],
                    mode="markers+text",
                    text=[primitive.get("text", "")],
                    textposition=str(primitive_meta.get("textposition", "top center")),
                    marker={"color": primitive.get("color", "#2563EB"), "size": 10},
                    name=layer,
                    showlegend=False,
                )
            )
        elif primitive_type == "line":
            figure.add_trace(
                go.Scatter(
                    x=[primitive.get("x1"), primitive.get("x2")],
                    y=[primitive.get("y1"), primitive.get("y2")],
                    mode="lines",
                    line={
                        "color": primitive.get("color", "#2563EB"),
                        "dash": "dash" if primitive.get("style") == "dashed" else "solid",
                        "width": 3 if layer == "segments" else 2,
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
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h"},
        template="plotly_white",
    )
    if timeframe == "day":
        rangebreaks = _build_daily_rangebreaks(x_values)
        if rangebreaks:
            figure.update_xaxes(rangebreaks=rangebreaks)
    return figure


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
