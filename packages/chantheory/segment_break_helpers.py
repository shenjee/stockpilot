from __future__ import annotations

from typing import Any, Callable, Sequence

from .segment_helpers import StrokeRange
from .schema import Stroke


def _is_feature_break_fractal(
    direction: str,
    ranges: Sequence[StrokeRange],
) -> bool:
    if len(ranges) < 3:
        return False
    left, middle, right = ranges[:3]
    if direction == "up":
        return middle.high >= left.high and middle.high > right.high
    if direction == "down":
        return middle.low <= left.low and middle.low < right.low
    return False


def _has_gap_followup_reverse_fractal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
    *,
    stroke_range: Callable[[int, Stroke], StrokeRange],
    is_feature_break_fractal: Callable[[str, Sequence[StrokeRange]], bool],
    feature_break_signal_factory: Callable[[bool, str, dict[str, Any]], Any],
):
    opposite_direction = "down" if direction == "up" else "up"
    reverse_feature_indices = [
        current_end_index + 1,
        current_end_index + 3,
        current_end_index + 5,
    ]
    if reverse_feature_indices[-1] >= len(strokes):
        return feature_break_signal_factory(
            False,
            "insufficient_followup_reverse_sequence",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    if any(strokes[index].direction != opposite_direction for index in reverse_feature_indices):
        return feature_break_signal_factory(
            False,
            "followup_reverse_direction_mismatch",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    ranges = [stroke_range(index, strokes[index]) for index in reverse_feature_indices]
    confirmed = is_feature_break_fractal(
        direction=opposite_direction,
        ranges=ranges,
    )
    return feature_break_signal_factory(
        confirmed,
        "followup_reverse_fractal" if confirmed else "no_followup_reverse_fractal",
        {
            "followup_reverse_feature_indices": reverse_feature_indices,
            "followup_reverse_fractal": confirmed,
        },
    )


def _opposite_segment_break_signal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
    *,
    stroke_range: Callable[[int, Stroke], StrokeRange],
    is_more_extreme: Callable[[str, float, float], bool],
    range_contains: Callable[[StrokeRange, StrokeRange], bool],
    ranges_have_gap: Callable[[StrokeRange, StrokeRange], bool],
    has_gap_followup_reverse_fractal: Callable[[Sequence[Stroke], int, str], Any],
    feature_break_signal_factory: Callable[[bool, str, dict[str, Any]], Any],
):
    f1_index = current_end_index - 1
    f2_index = current_end_index + 1

    if f2_index >= len(strokes):
        return feature_break_signal_factory(False, "insufficient_feature_sequence", {})

    opposite_direction = "down" if direction == "up" else "up"

    feature_strokes = []
    for idx in range(f1_index, len(strokes), 2):
        if idx >= 0:
            stroke = strokes[idx]
            if stroke.direction != opposite_direction:
                return feature_break_signal_factory(
                    False,
                    "feature_sequence_direction_mismatch",
                    {
                        "expected_direction": opposite_direction,
                        "violating_index": idx,
                        "violating_direction": stroke.direction,
                    },
                )
            feature_strokes.append((idx, stroke_range(idx, stroke)))

    if len(feature_strokes) < 3:
        if len(strokes) % 2 != (current_end_index % 2):
            last_idx = len(strokes) - 1
            if is_more_extreme(
                direction,
                current_price=strokes[current_end_index].end_price,
                candidate_price=strokes[last_idx].end_price,
            ):
                return feature_break_signal_factory(
                    False,
                    "new_peak_found",
                    {"new_peak_index": last_idx},
                )
        return feature_break_signal_factory(False, "insufficient_feature_sequence", {})

    processed_features = [(idx, rng, [idx]) for idx, rng in feature_strokes]

    first_new_peak_idx = None
    for idx in range(current_end_index + 2, len(strokes), 2):
        if is_more_extreme(
            direction,
            current_price=strokes[current_end_index].end_price,
            candidate_price=strokes[idx].end_price,
        ):
            first_new_peak_idx = idx
            break

    if len(strokes) % 2 != (current_end_index % 2):
        last_idx = len(strokes) - 1
        if first_new_peak_idx is None and is_more_extreme(
            direction,
            current_price=strokes[current_end_index].end_price,
            candidate_price=strokes[last_idx].end_price,
        ):
            first_new_peak_idx = last_idx

    first_fractal_signal = None
    first_fractal_completion_idx = None

    for index in range(len(processed_features) - 2):
        f1_idx, f1_r, f1_orig = processed_features[index]
        _f2_idx, f2_r, f2_orig = processed_features[index + 1]
        f3_idx, f3_r, f3_orig = processed_features[index + 2]

        is_fractal = False
        if direction == "up":
            # 顶分型：中间高点 > 左右高点，low 不需要满足包含条件
            if f2_r.high >= f1_r.high and f2_r.high > f3_r.high:
                is_fractal = True
            elif range_contains(f2_r, f1_r):
                is_fractal = True
        else:
            # 底分型：中间低点 < 左右低点，high 不需要满足包含条件
            if f2_r.low <= f1_r.low and f2_r.low < f3_r.low:
                is_fractal = True
            elif range_contains(f2_r, f1_r):
                is_fractal = True

        if is_fractal:
            has_gap = ranges_have_gap(f1_r, f2_r)
            base_meta: dict[str, Any] = {
                "feature_sequence_indices": f1_orig + f2_orig + f3_orig,
                "feature_sequence_direction": strokes[f1_idx].direction,
                "first_second_has_gap": has_gap,
                "break_fractal": True,
                "left_contained_by_middle": range_contains(f2_r, f1_r),
            }
            first_fractal_completion_idx = f3_idx
            if not has_gap:
                first_fractal_signal = feature_break_signal_factory(
                    True,
                    "no_gap_feature_fractal",
                    {
                        **base_meta,
                        "confirmation_case": "no_gap_feature_fractal",
                        "followup_required": False,
                    },
                )
            else:
                followup = has_gap_followup_reverse_fractal(
                    strokes=strokes,
                    current_end_index=current_end_index,
                    direction=direction,
                )
                if followup.confirmed:
                    first_fractal_signal = feature_break_signal_factory(
                        True,
                        "gap_feature_fractal_with_followup_reverse_fractal",
                        {
                            **base_meta,
                            **followup.meta,
                            "confirmation_case": "gap_feature_fractal_with_followup_reverse_fractal",
                            "followup_required": True,
                        },
                    )
                else:
                    first_fractal_signal = feature_break_signal_factory(
                        False,
                        "gap_feature_fractal_waiting_for_followup_reverse_fractal",
                        {
                            **base_meta,
                            **followup.meta,
                            "confirmation_case": "gap_feature_fractal_waiting",
                            "followup_required": True,
                            "pending_reason": "gap_feature_fractal_waiting_for_followup_reverse_fractal",
                        },
                    )
            break

    if first_new_peak_idx is not None and first_fractal_completion_idx is not None:
        if first_new_peak_idx < first_fractal_completion_idx:
            return feature_break_signal_factory(
                False,
                "new_peak_found",
                {"new_peak_index": first_new_peak_idx},
            )
        return first_fractal_signal
    if first_new_peak_idx is not None:
        return feature_break_signal_factory(
            False,
            "new_peak_found",
            {"new_peak_index": first_new_peak_idx},
        )
    if first_fractal_signal is not None:
        return first_fractal_signal

    return feature_break_signal_factory(False, "no_feature_fractal", {})
