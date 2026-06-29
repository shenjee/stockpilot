from __future__ import annotations

from typing import Callable, Sequence

from .schema import Stroke


def _segment_has_directional_price_span(
    segment_direction: str,
    start_price: float,
    end_price: float,
) -> bool:
    if segment_direction == "up":
        return start_price < end_price
    if segment_direction == "down":
        return start_price > end_price
    return False


def _strokes_have_overlap(strokes: Sequence[Stroke]) -> bool:
    if len(strokes) < 3:
        return False

    lows = [min(stroke.start_price, stroke.end_price) for stroke in strokes[:3]]
    highs = [max(stroke.start_price, stroke.end_price) for stroke in strokes[:3]]
    return max(lows) <= min(highs)


def _is_valid_segment_seed(
    strokes: Sequence[Stroke],
    *,
    strokes_have_overlap: Callable[[Sequence[Stroke]], bool] = _strokes_have_overlap,
    segment_has_directional_price_span: Callable[[str, float, float], bool] = _segment_has_directional_price_span,
) -> bool:
    if len(strokes) < 3:
        return False
    direction = strokes[0].direction
    if direction not in {"up", "down"}:
        return False

    if (
        strokes[1].direction != direction
        and strokes[2].direction == direction
        and strokes_have_overlap(strokes[:3])
        and segment_has_directional_price_span(
            segment_direction=direction,
            start_price=strokes[0].start_price,
            end_price=strokes[2].end_price,
        )
    ):
        return True

    if not (
        strokes[1].direction != direction
        and strokes[2].direction == direction
        and strokes_have_overlap(strokes[:3])
    ):
        return False

    for index in range(4, len(strokes), 2):
        if strokes[index].direction == direction and segment_has_directional_price_span(
            segment_direction=direction,
            start_price=strokes[0].start_price,
            end_price=strokes[index].end_price,
        ):
            return True

    return False
