from __future__ import annotations

from dataclasses import dataclass

from .schema import Stroke


@dataclass(frozen=True)
class StrokeRange:
    stroke_index: int
    low: float
    high: float


def _is_more_extreme(direction: str, current_price: float, candidate_price: float) -> bool:
    if direction == "up":
        return candidate_price > current_price
    if direction == "down":
        return candidate_price < current_price
    return False


def _stroke_range(index: int, stroke: Stroke) -> StrokeRange:
    return StrokeRange(
        stroke_index=index,
        low=min(stroke.start_price, stroke.end_price),
        high=max(stroke.start_price, stroke.end_price),
    )


def _ranges_have_gap(left: StrokeRange, right: StrokeRange) -> bool:
    return left.high < right.low or right.high < left.low


def _range_contains(container: StrokeRange, inner: StrokeRange) -> bool:
    return container.low <= inner.low and container.high >= inner.high
