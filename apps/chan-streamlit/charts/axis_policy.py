from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List


MINUTE_TIMEFRAMES = {"1m", "5m", "30m", "60m"}


def is_minute_timeframe(timeframe: str) -> bool:
    return timeframe in MINUTE_TIMEFRAMES


def build_x_axis_range(x_values: List[object], visible_x_values: List[object], use_continuous_bar_axis: bool) -> List[object]:
    if not visible_x_values:
        return []
    if not use_continuous_bar_axis:
        return [visible_x_values[0], visible_x_values[-1]]

    index_by_value = {value: index for index, value in enumerate(x_values)}
    start_index = index_by_value.get(visible_x_values[0], 0)
    end_index = index_by_value.get(visible_x_values[-1], max(len(x_values) - 1, 0))
    return [max(start_index - 0.5, -0.5), end_index + 0.5]


def build_intraday_date_ticks(x_values: List[object]) -> tuple[List[object], List[str]]:
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


def build_y_axis_range(rows: List[Dict[str, object]], y_zoom: float) -> List[float] | None:
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


def build_daily_rangebreaks(x_values: List[object]) -> List[Dict[str, object]]:
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


def build_time_range_label(timeframe: str, timestamp: str) -> str:
    time_part = timestamp[11:16]
    if timeframe == "60m":
        starts = {"10:30": "09:30", "11:30": "10:30", "14:00": "13:00", "15:00": "14:00"}
        start = starts.get(time_part, "")
        return f"{start} - {time_part}" if start else time_part
    if timeframe == "30m":
        starts = {
            "10:00": "09:30",
            "10:30": "10:00",
            "11:00": "10:30",
            "11:30": "11:00",
            "13:30": "13:00",
            "14:00": "13:30",
            "14:30": "14:00",
            "15:00": "14:30",
        }
        start = starts.get(time_part, "")
        return f"{start} - {time_part}" if start else time_part
    return time_part
