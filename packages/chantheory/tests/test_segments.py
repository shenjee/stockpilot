from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set

from packages.chantheory.schema import Segment, Stroke


SEGMENT_MAPPING_STRATEGY = "chan_feature_sequence_gap_fractal_confirmation"


@dataclass(frozen=True)
class SegmentEndpoint:
    stroke_index: int
    direction: str
    timestamp: str
    price: float


@dataclass(frozen=True)
class StrokeRange:
    stroke_index: int
    low: float
    high: float


@dataclass(frozen=True)
class FeatureBreakSignal:
    confirmed: bool
    reason: str
    meta: Dict[str, Any]


def derive_segments(strokes: Sequence[Stroke]) -> List[Segment]:
    segments: List[Segment] = []
    if len(strokes) < 3:
        return segments

    potential_endpoints = _potential_endpoint_indices(strokes)
    start_index = 0
    while start_index + 2 < len(strokes):
        # We pass the remainder of strokes so _is_valid_segment_seed can look ahead
        remaining_strokes = list(strokes[start_index:])
        if not remaining_strokes:
            break
            
        direction = remaining_strokes[0].direction
        if not _is_valid_segment_seed(remaining_strokes):
            start_index += 1
            continue

        end_index = start_index + 2
        endpoint_updates: List[int] = []
        feature_break_meta: Dict[str, Any] | None = None
        while end_index + 2 < len(strokes):
            next_start = end_index + 1
            break_signal = _opposite_segment_break_signal(
                strokes=strokes,
                current_end_index=end_index,
                direction=direction,
            )
            if break_signal.confirmed:
                feature_break_meta = break_signal.meta
                break

            extend_index = end_index + 2
            if extend_index >= len(strokes):
                if break_signal.reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                    feature_break_meta = {**break_signal.meta, "pending_reason": break_signal.reason}
                break
            if strokes[extend_index].direction != direction:
                if break_signal.reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                    feature_break_meta = {**break_signal.meta, "pending_reason": break_signal.reason}
                break
            if not _is_more_extreme(direction, current_price=strokes[end_index].end_price, candidate_price=strokes[extend_index].end_price):
                if break_signal.reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                    feature_break_meta = {**break_signal.meta, "pending_reason": break_signal.reason}
                break
            if break_signal.reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                feature_break_meta = None
            endpoint_updates.append(extend_index)
            end_index = extend_index

        segment = _make_segment(
            strokes=strokes,
            start_index=start_index,
            end_index=end_index,
            potential_endpoints=potential_endpoints,
            endpoint_updates=endpoint_updates,
            feature_break_meta=feature_break_meta,
        )
        if segment is not None:
            if segments and segments[-1].direction == segment.direction:
                if _extend_segment_if_more_extreme(
                    segment=segments[-1],
                    strokes=strokes,
                    end_index=end_index,
                    potential_endpoints=potential_endpoints,
                ):
                    start_index = end_index + 1
                else:
                    start_index += 1
                continue

            segments.append(segment)
            start_index = end_index + 1
        else:
            start_index += 1

    segments = _merge_adjacent_same_direction_segments(segments, strokes=strokes, potential_endpoints=potential_endpoints)
    segments = _enforce_segment_contract(segments)
    _apply_segment_confirmation(segments)
    _append_unfinished_tail_segment(segments=segments, strokes=strokes, potential_endpoints=potential_endpoints)
    return segments


def _make_segment(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    potential_endpoints: Set[int],
    endpoint_updates: Sequence[int],
    feature_break_meta: Dict[str, Any] | None = None,
) -> Segment | None:
    window = list(strokes[start_index : end_index + 1])
    first = window[0]
    last = window[-1]
    if not first.start_timestamp or not last.end_timestamp:
        return None

    direction = first.direction
    return Segment(
        id=f"segment_{start_index + 1:03d}_{first.start_timestamp}_{last.end_timestamp}",
        direction=direction,
        stroke_ids=[stroke.id for stroke in window],
        start_timestamp=first.start_timestamp,
        end_timestamp=last.end_timestamp,
        start_price=first.start_price,
        end_price=last.end_price,
        confirmed=False,
        meta={
            "mapping_strategy": SEGMENT_MAPPING_STRATEGY,
            "status": "pending",
            "stroke_count": len(window),
            "start_stroke_index": start_index,
            "end_stroke_index": end_index,
            "initial_three_overlap": True,
            "endpoint_is_potential_extreme": end_index in potential_endpoints,
            "endpoint_direction_valid": _segment_has_directional_price_span(
                segment_direction=direction,
                start_price=first.start_price,
                end_price=last.end_price,
            ),
            "potential_endpoint_indices": sorted(potential_endpoints),
            "endpoint_update_indices": list(endpoint_updates),
            "endpoint_update_policy": "same_direction_odd_stroke_extension_until_opposite_break",
            "opposite_break_rule": "feature_sequence_gap_fractal_confirmation",
            "feature_sequence_break": feature_break_meta,
            "confirmed_by_segment_id": None,
        },
    )


def _extend_segment_if_more_extreme(
    segment: Segment,
    strokes: Sequence[Stroke],
    end_index: int,
    potential_endpoints: Set[int],
) -> bool:
    new_end = strokes[end_index]
    if not _is_more_extreme(segment.direction, current_price=segment.end_price, candidate_price=new_end.end_price):
        return False

    previous_end_index = int(segment.meta.get("end_stroke_index", -1))
    append_from = max(previous_end_index + 1, 0)
    for stroke in strokes[append_from : end_index + 1]:
        if stroke.id not in segment.stroke_ids:
            segment.stroke_ids.append(stroke.id)

    segment.end_timestamp = new_end.end_timestamp
    segment.end_price = new_end.end_price
    segment.id = f"segment_{int(segment.meta.get('start_stroke_index', 0)) + 1:03d}_{segment.start_timestamp}_{segment.end_timestamp}"
    segment.confirmed = False
    segment.meta["status"] = "pending"
    segment.meta["stroke_count"] = len(segment.stroke_ids)
    segment.meta["end_stroke_index"] = end_index
    segment.meta["endpoint_is_potential_extreme"] = end_index in potential_endpoints
    segment.meta.setdefault("endpoint_update_indices", []).append(end_index)
    segment.meta["same_direction_candidate_merged"] = True
    segment.meta["same_direction_candidate_merge_policy"] = "extend_pending_segment_instead_of_creating_same_direction_segment"
    segment.meta["confirmed_by_segment_id"] = None
    return True


def _merge_adjacent_same_direction_segments(
    segments: Sequence[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
) -> List[Segment]:
    merged: List[Segment] = []
    for segment in segments:
        if not merged or merged[-1].direction != segment.direction:
            merged.append(segment)
            continue

        end_index = int(segment.meta.get("end_stroke_index", -1))
        if end_index >= 0 and _extend_segment_if_more_extreme(
            segment=merged[-1],
            strokes=strokes,
            end_index=end_index,
            potential_endpoints=potential_endpoints,
        ):
            merged[-1].meta["adjacent_same_direction_segment_merged"] = True
            continue

        merged[-1].meta["adjacent_same_direction_segment_dropped"] = segment.id

    return merged


def _enforce_segment_contract(segments: Sequence[Segment]) -> List[Segment]:
    valid: List[Segment] = []
    for segment in segments:
        if segment.direction not in {"up", "down"}:
            continue
        if len(segment.stroke_ids) < 3 or len(segment.stroke_ids) % 2 == 0:
            continue
        if valid and valid[-1].direction == segment.direction:
            continue
        if not _segment_has_directional_price_span(
            segment_direction=segment.direction,
            start_price=segment.start_price,
            end_price=segment.end_price,
        ):
            continue
        if valid and not _segments_are_connected(valid[-1], segment):
            continue
        valid.append(segment)
    return valid


def _append_unfinished_tail_segment(
    segments: List[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
) -> None:
    if not segments:
        return

    last_segment = segments[-1]
    feature_break = last_segment.meta.get("feature_sequence_break")
    if isinstance(feature_break, dict) and feature_break.get("pending_reason") == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
        return

    last_end_index = int(last_segment.meta.get("end_stroke_index", -1))
    tail_start_index = last_end_index + 1
    if tail_start_index >= len(strokes):
        return

    first_tail = strokes[tail_start_index]
    if first_tail.direction == last_segment.direction:
        return
    if not _segments_are_connected_to_stroke(last_segment, first_tail):
        return

    tail_end_index = len(strokes) - 1
    segment = _make_unfinished_tail_segment(
        strokes=strokes,
        start_index=tail_start_index,
        end_index=tail_end_index,
        potential_endpoints=potential_endpoints,
    )
    if segment is not None:
        segments.append(segment)


def _segments_are_connected_to_stroke(previous: Segment, stroke: Stroke) -> bool:
    return previous.end_timestamp == stroke.start_timestamp and abs(previous.end_price - stroke.start_price) < 1e-9


def _make_unfinished_tail_segment(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    potential_endpoints: Set[int],
) -> Segment | None:
    window = list(strokes[start_index : end_index + 1])
    first = window[0]
    direction = first.direction
    drawable_end_index = start_index
    for index in range(start_index, end_index + 1):
        if strokes[index].direction != direction:
            continue
        if _segment_has_directional_price_span(
            segment_direction=direction,
            start_price=first.start_price,
            end_price=strokes[index].end_price,
        ) and _is_more_extreme(
            direction=direction,
            current_price=strokes[drawable_end_index].end_price,
            candidate_price=strokes[index].end_price,
        ):
            drawable_end_index = index

    window = list(strokes[start_index : drawable_end_index + 1])
    last = window[-1]
    if not first.start_timestamp or not last.end_timestamp:
        return None
    if not _segment_has_directional_price_span(
        segment_direction=direction,
        start_price=first.start_price,
        end_price=last.end_price,
    ):
        return None

    return Segment(
        id=f"segment_growing_{start_index + 1:03d}_{first.start_timestamp}_{last.end_timestamp}",
        direction=direction,
        stroke_ids=[stroke.id for stroke in window],
        start_timestamp=first.start_timestamp,
        end_timestamp=last.end_timestamp,
        start_price=first.start_price,
        end_price=last.end_price,
        confirmed=False,
        meta={
            "mapping_strategy": SEGMENT_MAPPING_STRATEGY,
            "status": "growing",
            "provisional": True,
            "stroke_count": len(window),
            "start_stroke_index": start_index,
            "end_stroke_index": drawable_end_index,
            "available_tail_end_stroke_index": end_index,
            "initial_three_overlap": len(window) >= 3 and _strokes_have_overlap(window),
            "endpoint_is_potential_extreme": drawable_end_index in potential_endpoints,
            "endpoint_direction_valid": _segment_has_directional_price_span(
                segment_direction=direction,
                start_price=first.start_price,
                end_price=last.end_price,
            ),
            "potential_endpoint_indices": sorted(potential_endpoints),
            "endpoint_update_indices": [],
            "endpoint_update_policy": "unfinished_tail_drawn_before_segment_completion",
            "opposite_break_rule": "not_applicable_until_tail_segment_completes",
            "feature_sequence_break": None,
            "confirmed_by_segment_id": None,
        },
    )


def _segments_are_connected(previous: Segment, current: Segment) -> bool:
    return previous.end_timestamp == current.start_timestamp and abs(previous.end_price - current.start_price) < 1e-9


def _is_more_extreme(direction: str, current_price: float, candidate_price: float) -> bool:
    if direction == "up":
        return candidate_price > current_price
    if direction == "down":
        return candidate_price < current_price
    return False


def _potential_endpoint_indices(strokes: Sequence[Stroke]) -> Set[int]:
    endpoints = [_endpoint_from_stroke(index, stroke) for index, stroke in enumerate(strokes)]
    result: Set[int] = set()

    for direction in {"up", "down"}:
        same_direction = [endpoint for endpoint in endpoints if endpoint.direction == direction]
        for index in range(1, len(same_direction) - 1):
            previous = same_direction[index - 1]
            current = same_direction[index]
            next_endpoint = same_direction[index + 1]
            if direction == "up" and previous.price < current.price > next_endpoint.price:
                result.add(current.stroke_index)
            if direction == "down" and previous.price > current.price < next_endpoint.price:
                result.add(current.stroke_index)

    return result


def _endpoint_from_stroke(index: int, stroke: Stroke) -> SegmentEndpoint:
    return SegmentEndpoint(
        stroke_index=index,
        direction=stroke.direction,
        timestamp=stroke.end_timestamp,
        price=stroke.end_price,
    )


def _strokes_have_overlap(strokes: Sequence[Stroke]) -> bool:
    if len(strokes) < 3:
        return False

    lows = [min(stroke.start_price, stroke.end_price) for stroke in strokes[:3]]
    highs = [max(stroke.start_price, stroke.end_price) for stroke in strokes[:3]]
    return max(lows) <= min(highs)


def _is_valid_segment_seed(strokes: Sequence[Stroke]) -> bool:
    if len(strokes) < 3:
        return False
    direction = strokes[0].direction
    if direction not in {"up", "down"}:
        return False
    
    # Fast path for standard 3-stroke seed
    if (
        strokes[1].direction != direction
        and strokes[2].direction == direction
        and _strokes_have_overlap(strokes[:3])
        and _segment_has_directional_price_span(
            segment_direction=direction,
            start_price=strokes[0].start_price,
            end_price=strokes[2].end_price,
        )
    ):
        return True

    # Check for longer seeds (5, 7, etc. strokes) that eventually break out
    # They must start with an overlapping 3-stroke base.
    if not (
        strokes[1].direction != direction
        and strokes[2].direction == direction
        and _strokes_have_overlap(strokes[:3])
    ):
        return False

    # Look ahead to see if a subsequent stroke in the same direction breaks out
    for i in range(4, len(strokes), 2):
        if strokes[i].direction == direction:
            if _segment_has_directional_price_span(
                segment_direction=direction,
                start_price=strokes[0].start_price,
                end_price=strokes[i].end_price,
            ):
                return True

    return False


def _segment_has_directional_price_span(segment_direction: str, start_price: float, end_price: float) -> bool:
    if segment_direction == "up":
        return start_price < end_price
    if segment_direction == "down":
        return start_price > end_price
    return False


def _can_start_opposite_segment(strokes: Sequence[Stroke], start_index: int, direction: str) -> bool:
    if start_index + 2 >= len(strokes):
        return False
    first_three = list(strokes[start_index : start_index + 3])
    opposite = "down" if direction == "up" else "up"
    return first_three[0].direction == opposite and _is_valid_segment_seed(first_three)


def _opposite_segment_break_signal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
) -> FeatureBreakSignal:
    next_start = current_end_index + 1
    if not _can_start_opposite_segment(strokes=strokes, start_index=next_start, direction=direction):
        return FeatureBreakSignal(False, "no_valid_opposite_seed", {})

    feature_indices = [current_end_index - 1, next_start, next_start + 2]
    if feature_indices[0] < 0 or feature_indices[-1] >= len(strokes):
        return FeatureBreakSignal(False, "insufficient_feature_sequence", {})

    feature_strokes = [strokes[index] for index in feature_indices]
    opposite = "down" if direction == "up" else "up"
    if any(stroke.direction != opposite for stroke in feature_strokes):
        return FeatureBreakSignal(False, "feature_sequence_direction_mismatch", {})

    ranges = [_stroke_range(index, strokes[index]) for index in feature_indices]
    # Find the first chronological new peak
    first_new_peak_idx = None
    for idx in range(current_end_index + 2, len(strokes), 2):
        if _is_more_extreme(direction, current_price=strokes[current_end_index].end_price, candidate_price=strokes[idx].end_price):
            first_new_peak_idx = idx
            break
            
    if len(strokes) % 2 != (current_end_index % 2):
        last_idx = len(strokes) - 1
        if first_new_peak_idx is None and _is_more_extreme(direction, current_price=strokes[current_end_index].end_price, candidate_price=strokes[last_idx].end_price):
            first_new_peak_idx = last_idx

    has_gap = _ranges_have_gap(ranges[0], ranges[1])
    break_fractal = _is_feature_break_fractal(direction=direction, ranges=ranges)
    left_contained_by_middle = _range_contains(container=ranges[1], inner=ranges[0])
    base_meta: Dict[str, Any] = {
        "feature_sequence_indices": feature_indices,
        "feature_sequence_direction": opposite,
        "first_second_has_gap": has_gap,
        "break_fractal": break_fractal,
        "left_contained_by_middle": left_contained_by_middle,
    }

    first_fractal_signal = None
    first_fractal_completion_idx = None

    if break_fractal:
        first_fractal_completion_idx = feature_indices[2]
        if not has_gap:
            first_fractal_signal = FeatureBreakSignal(
                True,
                "no_gap_feature_fractal",
                {
                    **base_meta,
                    "confirmation_case": "no_gap_feature_fractal",
                    "followup_required": False,
                },
            )
        else:
            followup = _has_gap_followup_reverse_fractal(
                strokes=strokes,
                current_end_index=current_end_index,
                direction=direction,
            )
            if followup.confirmed:
                first_fractal_signal = FeatureBreakSignal(
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
                first_fractal_signal = FeatureBreakSignal(
                    False,
                    "gap_feature_fractal_waiting_for_followup_reverse_fractal",
                    {
                        **base_meta,
                        **followup.meta,
                        "confirmation_case": "gap_feature_fractal_waiting",
                        "followup_required": True,
                    },
                )

    if first_new_peak_idx is not None and first_fractal_completion_idx is not None:
        if first_new_peak_idx < first_fractal_completion_idx:
            return FeatureBreakSignal(False, "new_peak_found", {"new_peak_index": first_new_peak_idx})
        else:
            return first_fractal_signal
    elif first_new_peak_idx is not None:
        return FeatureBreakSignal(False, "new_peak_found", {"new_peak_index": first_new_peak_idx})
    elif first_fractal_signal is not None:
        return first_fractal_signal

    return FeatureBreakSignal(False, "no_feature_fractal", base_meta)


def _has_gap_followup_reverse_fractal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
) -> FeatureBreakSignal:
    reverse_feature_indices = [current_end_index, current_end_index + 2, current_end_index + 4]
    if reverse_feature_indices[-1] >= len(strokes):
        return FeatureBreakSignal(
            False,
            "insufficient_followup_reverse_sequence",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    if any(strokes[index].direction != direction for index in reverse_feature_indices):
        return FeatureBreakSignal(
            False,
            "followup_reverse_direction_mismatch",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    ranges = [_stroke_range(index, strokes[index]) for index in reverse_feature_indices]
    reverse_direction = "down" if direction == "up" else "up"
    confirmed = _is_feature_break_fractal(direction=reverse_direction, ranges=ranges)
    return FeatureBreakSignal(
        confirmed,
        "followup_reverse_fractal" if confirmed else "no_followup_reverse_fractal",
        {
            "followup_reverse_feature_indices": reverse_feature_indices,
            "followup_reverse_fractal": confirmed,
        },
    )


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


def _is_feature_break_fractal(direction: str, ranges: Sequence[StrokeRange]) -> bool:
    if len(ranges) < 3:
        return False
    left, middle, right = ranges[:3]
    if direction == "up":
        return middle.high >= left.high and middle.high > right.high
    if direction == "down":
        return middle.low <= left.low and middle.low < right.low
    return False


def _apply_segment_confirmation(segments: Sequence[Segment]) -> None:
    for index, segment in enumerate(segments):
        segment.confirmed = False
        segment.meta["status"] = "pending"
        segment.meta["confirmed_by_segment_id"] = None

        if index + 1 >= len(segments):
            continue

        next_segment = segments[index + 1]
        feature_break = segment.meta.get("feature_sequence_break")
        if isinstance(feature_break, dict) and feature_break.get("pending_reason") == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
            continue
        if next_segment.direction != segment.direction:
            segment.confirmed = True
            segment.meta["status"] = "confirmed"
            segment.meta["confirmed_by_segment_id"] = next_segment.id
