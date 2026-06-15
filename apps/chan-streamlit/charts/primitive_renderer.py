from __future__ import annotations

from typing import Dict

import plotly.graph_objects as go

from ui_text import _format_alert_message


def _to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "rgba(245, 158, 11, 0.18)"
    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha})"


def render_plot_primitives(figure: go.Figure, result_payload: Dict[str, object], visibility: Dict[str, bool], language: str) -> None:
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
