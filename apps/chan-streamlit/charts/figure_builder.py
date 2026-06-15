from __future__ import annotations

from typing import Dict, Iterable

import plotly.graph_objects as go

from charts.axis_policy import (
    build_daily_rangebreaks,
    build_intraday_date_ticks,
    build_time_range_label,
    build_x_axis_range,
    build_y_axis_range,
    is_minute_timeframe,
)
from charts.primitive_renderer import render_plot_primitives
from ui_text import _t


def build_figure(
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
    use_continuous_bar_axis = is_minute_timeframe(timeframe)

    customdata = []
    for item in ordered_rows:
        date_str = str(item["date"])
        day = date_str[:10]
        time_range = build_time_range_label(timeframe, date_str)
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

    render_plot_primitives(figure, result_payload, visibility, language)

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
        rangebreaks = build_daily_rangebreaks(x_values)
        if rangebreaks:
            figure.update_xaxes(rangebreaks=rangebreaks)
    elif use_continuous_bar_axis:
        tick_values, tick_text = build_intraday_date_ticks(x_values)
        figure.update_xaxes(
            type="category",
            categoryorder="array",
            categoryarray=x_values,
            tickmode="array",
            tickvals=tick_values,
            ticktext=tick_text,
        )
    if visible_x_values:
        figure.update_xaxes(range=build_x_axis_range(x_values, visible_x_values, use_continuous_bar_axis))
    y_range = build_y_axis_range(visible_rows, y_zoom)
    if y_range:
        figure.update_yaxes(range=y_range)
    return figure
