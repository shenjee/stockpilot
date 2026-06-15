from __future__ import annotations

import re
from typing import Any, Dict, List

from chantheory.schema import AnalysisResult, AnalysisWarning


SUPPORTED_LANGUAGES = {"zh": "中文", "en": "English"}
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
                lines.append(f"最新一笔已确认笔方向为{_format_direction(last_stroke.direction, language)}，终点时间为 {_display_timestamp(last_stroke.end_timestamp)}。")
            else:
                lines.append(f"The latest confirmed stroke points {last_stroke.direction} into {_display_timestamp(last_stroke.end_timestamp)}.")

        if result.candidate_buy_points:
            zh_names = {"first_buy": "一买", "second_buy": "二买", "third_buy": "三买", "structure_buy_candidate": "保守买点"}
            en_names = {"first_buy": "first-buy", "second_buy": "second-buy", "third_buy": "third-buy", "structure_buy_candidate": "conservative buy-point"}
            for point in sorted(result.candidate_buy_points, key=lambda x: x.timestamp):
                point_type = point.point_type
                if language == "zh":
                    name = zh_names.get(point_type, "买点")
                    lines.append(f"买点提示：在 {_display_timestamp(point.timestamp)} 发现【{name}】，价格 {point.price:.2f}。")
                else:
                    name = en_names.get(point_type, "buy-point")
                    lines.append(f"Buy Point: [{name}] candidate at {_display_timestamp(point.timestamp)}, price {point.price:.2f}.")

        if result.candidate_sell_points:
            zh_names = {"first_sell": "一卖", "second_sell": "二卖", "third_sell": "三卖", "structure_sell_candidate": "保守卖点"}
            en_names = {"first_sell": "first-sell", "second_sell": "second-sell", "third_sell": "third-sell", "structure_sell_candidate": "conservative sell-point"}
            for point in sorted(result.candidate_sell_points, key=lambda x: x.timestamp):
                point_type = point.point_type
                if language == "zh":
                    name = zh_names.get(point_type, "卖点")
                    lines.append(f"卖点提示：在 {_display_timestamp(point.timestamp)} 发现【{name}】，价格 {point.price:.2f}。")
                else:
                    name = en_names.get(point_type, "sell-point")
                    lines.append(f"Sell Point: [{name}] candidate at {_display_timestamp(point.timestamp)}, price {point.price:.2f}.")
        if any(item.warning_code == "UNSTABLE_TAIL_STROKE" for item in result.warnings):
            lines.append("最新结构仍在延伸，尾部应按未稳定结构处理。" if language == "zh" else "The newest structure is still extending, so the tail should be treated as unstable.")
        return lines

    lines.append("czsc 探测不可用，适配层返回带告警的冻结 schema。" if language == "zh" else "czsc probe is unavailable, so the adapter returns the frozen schema with warnings.")
    if result.warnings:
        lines.append(f"共记录 {len(result.warnings)} 条与标准化或引擎就绪状态相关的告警。" if language == "zh" else f"{len(result.warnings)} warning(s) recorded for normalization or engine readiness.")
    return lines
