from __future__ import annotations

from typing import Dict

import plotly.graph_objects as go

from ui_text import _format_alert_message, _layer_label


def _to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "rgba(245, 158, 11, 0.18)"
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _resolve_x(value: object, timestamp_to_index: Dict[str, int] | None) -> int | None:
    """将图元的时间戳 x 值转换为 slot 索引；未命中返回 None，由调用方跳过。"""
    if timestamp_to_index is None:
        if isinstance(value, (int, float)):
            return int(value)
        return None
    index = timestamp_to_index.get(str(value))
    return int(index) if index is not None else None


def render_plot_primitives(
    figure: go.Figure,
    result_payload: Dict[str, object],
    visibility: Dict[str, bool],
    language: str,
    row: int | None = None,
    col: int | None = None,
    timestamp_to_index: Dict[str, int] | None = None,
) -> None:
    trace_kwargs = {"row": row, "col": col} if row is not None and col is not None else {}
    layout_kwargs = {"row": row, "col": col} if row is not None and col is not None else {}
    for primitive in result_payload.get("plot_primitives", []):
        layer = str(primitive.get("layer", ""))
        primitive_meta = dict(primitive.get("meta", {}) or {})
        if layer == "pivot_zones":
            pivot_level = str(primitive_meta.get("level", "stroke"))
            visibility_key = "segment_pivot_zones" if pivot_level == "segment" else "stroke_pivot_zones"
            if not visibility.get(visibility_key, True):
                continue
            legend_name = _layer_label(visibility_key, language)
        else:
            if not visibility.get(layer, True):
                continue
            legend_name = _layer_label(layer, language)
        primitive_type = primitive.get("type")
        handled_trace = False
        if primitive_type == "marker":
            primitive_meta = dict(primitive.get("meta", {}) or {})
            style = primitive.get("style", "circle")
            x_val = _resolve_x(primitive.get("x"), timestamp_to_index)
            if x_val is None:
                continue
            if style == "text":
                figure.add_trace(
                    go.Scatter(
                        x=[x_val],
                        y=[primitive.get("y")],
                        mode="text",
                        text=[primitive.get("text", "")],
                        textposition=str(primitive_meta.get("textposition", "top center")),
                        textfont={"color": primitive.get("color", "#2563EB")},
                        name=legend_name,
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    **trace_kwargs,
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
                        x=[x_val],
                        y=[primitive.get("y")],
                        mode="markers+text",
                        text=[primitive.get("text", "")],
                        textposition=str(primitive_meta.get("textposition", "top center")),
                        marker={"color": primitive.get("color", "#2563EB"), "size": 10, "symbol": marker_symbol},
                        textfont={"color": primitive.get("color", "#2563EB")},
                        name=legend_name,
                        showlegend=False,
                        hoverinfo="skip",
                    ),
                    **trace_kwargs,
                )
            handled_trace = True
        elif primitive_type == "line":
            primitive_meta = primitive.get("meta") or {}
            width_multiplier = float(primitive_meta.get("width_multiplier", 1.0))
            x1_val = _resolve_x(primitive.get("x1"), timestamp_to_index)
            x2_val = _resolve_x(primitive.get("x2"), timestamp_to_index)
            if x1_val is None or x2_val is None:
                continue
            figure.add_trace(
                go.Scatter(
                    x=[x1_val, x2_val],
                    y=[primitive.get("y1"), primitive.get("y2")],
                    mode="lines",
                    line={
                        "color": primitive.get("color", "#2563EB"),
                        "dash": "dash" if primitive.get("style") == "dashed" else "solid",
                        "width": int(2 * width_multiplier),
                    },
                    name=legend_name,
                    showlegend=False,
                    hoverinfo="skip",
                ),
                **trace_kwargs,
            )
            handled_trace = True
        elif primitive_type == "box":
            x1_val = _resolve_x(primitive.get("x1"), timestamp_to_index)
            x2_val = _resolve_x(primitive.get("x2"), timestamp_to_index)
            if x1_val is None or x2_val is None:
                continue
            figure.add_shape(
                type="rect",
                x0=x1_val,
                x1=x2_val,
                y0=primitive.get("y2"),
                y1=primitive.get("y1"),
                line={"color": primitive.get("color", "#F59E0B"), "width": 2},
                fillcolor=_to_rgba(str(primitive.get("color", "#F59E0B")), 0.18),
                **layout_kwargs,
            )
            if primitive.get("text"):
                if language == "zh":
                    pivot_level = str(primitive_meta.get("level", "stroke"))
                    box_label = "段中枢" if pivot_level == "segment" else "笔中枢"
                else:
                    box_label = primitive.get("text")
                figure.add_annotation(
                    x=x2_val,
                    y=primitive.get("y1"),
                    text=box_label,
                    showarrow=False,
                    font={"color": primitive.get("color", "#F59E0B")},
                    **layout_kwargs,
                )
        elif primitive_type == "label":
            # 走势图上不再渲染 label 类提示语（如"最新活跃中枢区间..."），相关内容仅保留在摘要文本中。
            continue
