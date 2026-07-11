"""统一图表窗口 slot 策略，所有周期共享。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from charts.axis_policy import is_minute_timeframe

DEFAULT_SLOTS = 120
MIN_SLOTS = 40
MAX_SLOTS = 360
TARGET_TICK_COUNT = 7
ZOOM_STEP_DENOMINATOR = 6
ZOOM_STEP_MIN = 10


def clamp_slots(value: int) -> int:
    return max(MIN_SLOTS, min(MAX_SLOTS, value))


def default_slots() -> int:
    return DEFAULT_SLOTS


def zoom_step(current: int) -> int:
    """比例步进：每次约缩放 1/6 视野，下限 10。"""
    return max(ZOOM_STEP_MIN, round(current / ZOOM_STEP_DENOMINATOR))


def x_axis_range(row_count: int, slots: int) -> list[float]:
    """X 轴范围：数据 < slots 左对齐右侧留空；数据 >= slots 右对齐显示最新。"""
    if row_count <= 0:
        return [-0.5, float(slots - 1) + 0.5]
    start = max(0, row_count - slots)
    end = max(slots - 1, row_count - 1)
    return [start - 0.5, end + 0.5]


def build_tick_labels(
    timestamps: List[str],
    row_count: int,
    slots: int,
    timeframe: str,
) -> Tuple[List[int], List[str]]:
    """按目标标签数量生成刻度位置（索引）和格式化标签。

    分钟周期：同一天内只显示 HH:mm，跨日时显示 MM-DD HH:mm 标记日期边界。
    """
    if row_count <= 0:
        return [], []

    visible_start = max(0, row_count - slots)
    visible_end = row_count - 1
    visible_count = visible_end - visible_start + 1

    step = max(1, visible_count // TARGET_TICK_COUNT)
    tick_positions = list(range(visible_start, visible_end + 1, step))

    tick_labels = []
    prev_date: Optional[str] = None
    for pos in tick_positions:
        ts = str(timestamps[pos])
        tick_labels.append(_format_tick_label(ts, timeframe, prev_date))
        prev_date = ts[:10]

    return tick_positions, tick_labels


def _format_tick_label(timestamp: str, timeframe: str, prev_date: Optional[str] = None) -> str:
    if timeframe == "month":
        return timestamp[:7]
    if is_minute_timeframe(timeframe):
        time_part = timestamp[11:16]  # HH:mm
        date_part = timestamp[:10]    # YYYY-MM-DD
        if prev_date is not None and date_part == prev_date:
            return time_part
        # 首个 tick 或跨日边界：显示 MM-DD HH:mm
        try:
            dt = datetime.strptime(date_part, "%Y-%m-%d")
            return f"{dt.strftime('%m-%d')} {time_part}"
        except ValueError:
            return f"{date_part} {time_part}"
    # 日线/周线：MM-DD
    day_part = timestamp[:10]
    try:
        dt = datetime.strptime(day_part, "%Y-%m-%d")
        return dt.strftime("%m-%d")
    except ValueError:
        return day_part
