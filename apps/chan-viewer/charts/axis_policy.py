from __future__ import annotations

from typing import Dict, List


MINUTE_TIMEFRAMES = {"1m", "5m", "30m", "60m"}


def is_minute_timeframe(timeframe: str) -> bool:
    return timeframe in MINUTE_TIMEFRAMES


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
