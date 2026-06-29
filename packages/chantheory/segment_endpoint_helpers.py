from __future__ import annotations

from typing import Callable, Sequence, Set

from .schema import Stroke


def _find_window_extreme_start_index(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    direction: str,
) -> int:
    window = list(strokes[start_index : end_index + 1])
    if not window:
        return start_index

    first = window[0]
    eps = 1e-9

    if direction == "up":
        first_low = min(first.start_price, first.end_price)
        window_min = min(min(s.start_price, s.end_price) for s in window)
        if first_low <= window_min + eps:
            return start_index
        for idx in range(start_index, end_index + 1):
            stroke = strokes[idx]
            if abs(min(stroke.start_price, stroke.end_price) - window_min) > eps:
                continue
            if stroke.direction == "down" and abs(stroke.end_price - window_min) < eps:
                return idx + 1
            if stroke.direction == "up" and abs(stroke.start_price - window_min) < eps:
                return idx
            return idx + 1
        return start_index

    if direction == "down":
        first_high = max(first.start_price, first.end_price)
        window_max = max(max(s.start_price, s.end_price) for s in window)
        if first_high >= window_max - eps:
            return start_index
        for idx in range(start_index, end_index + 1):
            stroke = strokes[idx]
            if abs(max(stroke.start_price, stroke.end_price) - window_max) > eps:
                continue
            if stroke.direction == "up" and abs(stroke.end_price - window_max) < eps:
                return idx + 1
            if stroke.direction == "down" and abs(stroke.start_price - window_max) < eps:
                return idx
            return idx + 1
        return start_index

    return start_index


def _endpoint_from_stroke(
    index: int,
    stroke: Stroke,
    *,
    endpoint_factory: Callable[..., object],
):
    return endpoint_factory(
        stroke_index=index,
        direction=stroke.direction,
        timestamp=stroke.end_timestamp,
        price=stroke.end_price,
    )


def _potential_endpoint_indices(
    strokes: Sequence[Stroke],
    *,
    endpoint_from_stroke: Callable[[int, Stroke], object],
) -> Set[int]:
    endpoints = [
        endpoint_from_stroke(index, stroke)
        for index, stroke in enumerate(strokes)
    ]
    result: Set[int] = set()

    for direction in ("up", "down"):
        same_direction = [
            endpoint for endpoint in endpoints if endpoint.direction == direction
        ]
        for index in range(1, len(same_direction) - 1):
            previous = same_direction[index - 1]
            current = same_direction[index]
            next_endpoint = same_direction[index + 1]
            if direction == "up" and previous.price < current.price > next_endpoint.price:
                result.add(current.stroke_index)
            if direction == "down" and previous.price > current.price < next_endpoint.price:
                result.add(current.stroke_index)

    return result
