from __future__ import annotations

from typing import Dict, Iterable, List

import plotly.graph_objects as go
from plotly.subplots import make_subplots

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
    show_legend: bool = True,
    unified_hover: bool = True,
) -> go.Figure:
    ordered_rows = sorted(rows, key=lambda item: str(item["date"]))
    x_values = [item["date"] for item in ordered_rows]
    visible_rows = ordered_rows[-x_window:] if x_window else ordered_rows
    visible_x_values = [item["date"] for item in visible_rows]
    use_continuous_bar_axis = is_minute_timeframe(timeframe)
    show_volume = visibility.get("volume_panel", True)
    show_macd = visibility.get("macd_panel", True)
    subplot_specs = [[{"secondary_y": False}]]
    row_heights: List[float] = [0.58]
    subplot_titles = [_t(language, "candles_name")]
    if show_volume:
        subplot_specs.append([{"secondary_y": False}])
        row_heights.append(0.18)
        subplot_titles.append(_t(language, "volume_name"))
    if show_macd:
        subplot_specs.append([{"secondary_y": False}])
        row_heights.append(0.24)
        subplot_titles.append(_t(language, "macd_name"))
    row_count = len(subplot_specs)
    volume_row = 2 if show_volume else None
    macd_row = row_count if show_macd else None

    customdata = []
    for item in ordered_rows:
        date_str = str(item["date"])
        day = date_str[:10]
        time_range = build_time_range_label(timeframe, date_str)
        direction_icon = "▲" if float(item["close"]) >= float(item["open"]) else "▼"
        volume_value = float(item.get("volume", 0.0))
        customdata.append([day, time_range, direction_icon, volume_value])

    hovertemplate = (
        "<b>%{customdata[0]}</b><br>"
        "%{customdata[1]}<br>"
        "open: %{open}<br>"
        "high: %{high}<br>"
        "low: %{low}<br>"
        "close: %{close} %{customdata[2]}<br>"
        "volume: %{customdata[3]}<extra></extra>"
    ) if use_continuous_bar_axis else (
        "<b>%{x}</b><br>"
        "open: %{open}<br>"
        "high: %{high}<br>"
        "low: %{low}<br>"
        "close: %{close} %{customdata[2]}<br>"
        "volume: %{customdata[3]}<extra></extra>"
    )

    figure = make_subplots(
        rows=row_count,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )
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
        ),
        row=1,
        col=1,
    )

    render_plot_primitives(
        figure,
        result_payload,
        visibility,
        language,
        row=1,
        col=1,
        show_legend=show_legend,
    )

    if show_volume:
        volume_colors = [_volume_color(item) for item in ordered_rows]
        figure.add_trace(
            go.Bar(
                x=x_values,
                y=[float(item.get("volume", 0.0)) for item in ordered_rows],
                marker={"color": volume_colors},
                name=_t(language, "volume_name"),
                hovertemplate="<b>%{x}</b><br>volume: %{y}<extra></extra>",
            ),
            row=volume_row,
            col=1,
        )

    if show_macd:
        macd_series = _build_macd_series(ordered_rows)
        hist_colors = ["#DC2626" if value >= 0 else "#16A34A" for value in macd_series["hist"]]
        figure.add_trace(
            go.Bar(
                x=x_values,
                y=macd_series["hist"],
                marker={"color": hist_colors},
                name=_t(language, "macd_hist_name"),
                hovertemplate="<b>%{x}</b><br>MACD Hist: %{y:.4f}<extra></extra>",
            ),
            row=macd_row,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=macd_series["dif"],
                mode="lines",
                line={"color": "#2563EB", "width": 1.8},
                name=_t(language, "macd_dif_name"),
                hovertemplate="<b>%{x}</b><br>DIF: %{y:.4f}<extra></extra>",
            ),
            row=macd_row,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=macd_series["dea"],
                mode="lines",
                line={"color": "#F59E0B", "width": 1.8},
                name=_t(language, "macd_dea_name"),
                hovertemplate="<b>%{x}</b><br>DEA: %{y:.4f}<extra></extra>",
            ),
            row=macd_row,
            col=1,
        )

    figure.update_layout(
        margin={"l": 20, "r": 24, "t": 30, "b": 78},
        dragmode="pan",
        hovermode="x unified" if unified_hover else "closest",
        xaxis_rangeslider_visible=False,
        showlegend=show_legend,
        template="plotly_white",
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
        },
    )
    figure.update_annotations(font={"size": 12})
    if timeframe == "day":
        rangebreaks = build_daily_rangebreaks(x_values)
        if rangebreaks:
            for current_row in range(1, row_count + 1):
                figure.update_xaxes(rangebreaks=rangebreaks, row=current_row, col=1)
    elif use_continuous_bar_axis:
        tick_values, tick_text = build_intraday_date_ticks(x_values)
        for current_row in range(1, row_count + 1):
            figure.update_xaxes(
                type="category",
                categoryorder="array",
                categoryarray=x_values,
                tickmode="array",
                tickvals=tick_values,
                ticktext=tick_text,
                row=current_row,
                col=1,
            )
    if visible_x_values:
        x_range = build_x_axis_range(x_values, visible_x_values, use_continuous_bar_axis)
        for current_row in range(1, row_count + 1):
            figure.update_xaxes(range=x_range, row=current_row, col=1)
    y_range = build_y_axis_range(visible_rows, y_zoom)
    if y_range:
        figure.update_yaxes(range=y_range, row=1, col=1)
    if show_volume:
        figure.update_yaxes(title_text=_t(language, "volume_name"), row=volume_row, col=1)
    if show_macd:
        figure.update_yaxes(title_text=_t(language, "macd_name"), zeroline=True, zerolinecolor="#9CA3AF", row=macd_row, col=1)
    return figure


def _volume_color(item: Dict[str, object]) -> str:
    return "#DC2626" if float(item["close"]) >= float(item["open"]) else "#16A34A"


def _ema_series(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    ema = [values[0]]
    for value in values[1:]:
        ema.append(value * alpha + ema[-1] * (1 - alpha))
    return ema


def _build_macd_series(rows: List[Dict[str, object]]) -> Dict[str, List[float]]:
    closes = [float(item["close"]) for item in rows]
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif = [fast - slow for fast, slow in zip(ema12, ema26)]
    dea = _ema_series(dif, 9)
    hist = [(current_dif - current_dea) * 2 for current_dif, current_dea in zip(dif, dea)]
    return {"dif": dif, "dea": dea, "hist": hist}
