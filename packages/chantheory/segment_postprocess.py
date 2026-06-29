from __future__ import annotations

from typing import Callable, Sequence, Set

from .segment_helpers import _is_more_extreme
from .schema import Segment, Stroke


def _extend_segment_if_more_extreme(
    segment: Segment,
    strokes: Sequence[Stroke],
    end_index: int,
    potential_endpoints: Set[int],
) -> bool:
    new_end = strokes[end_index]
    if not _is_more_extreme(
        segment.direction,
        current_price=segment.end_price,
        candidate_price=new_end.end_price,
    ):
        return False

    previous_end_index = int(segment.meta.get("end_stroke_index", -1))
    append_from = max(previous_end_index + 1, 0)
    for stroke in strokes[append_from : end_index + 1]:
        if stroke.id not in segment.stroke_ids:
            segment.stroke_ids.append(stroke.id)

    segment.end_timestamp = new_end.end_timestamp
    segment.end_price = new_end.end_price
    segment.id = (
        f"segment_{int(segment.meta.get('start_stroke_index', 0)) + 1:03d}_"
        f"{segment.start_timestamp}_{segment.end_timestamp}"
    )
    segment.confirmed = False
    segment.meta["status"] = "pending"
    segment.meta["stroke_count"] = len(segment.stroke_ids)
    segment.meta["end_stroke_index"] = end_index
    segment.meta["endpoint_is_potential_extreme"] = end_index in potential_endpoints
    segment.meta.setdefault("endpoint_update_indices", []).append(end_index)
    segment.meta["same_direction_candidate_merged"] = True
    segment.meta["same_direction_candidate_merge_policy"] = (
        "extend_pending_segment_instead_of_creating_same_direction_segment"
    )
    segment.meta["confirmed_by_segment_id"] = None
    return True


def _merge_adjacent_same_direction_segments(
    segments: Sequence[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
    *,
    extend_segment_if_more_extreme: Callable[[Segment, Sequence[Stroke], int, Set[int]], bool] = _extend_segment_if_more_extreme,
) -> list[Segment]:
    merged: list[Segment] = []
    for segment in segments:
        if not merged or merged[-1].direction != segment.direction:
            merged.append(segment)
            continue

        end_index = int(segment.meta.get("end_stroke_index", -1))
        if end_index >= 0 and extend_segment_if_more_extreme(
            segment=merged[-1],
            strokes=strokes,
            end_index=end_index,
            potential_endpoints=potential_endpoints,
        ):
            merged[-1].meta["adjacent_same_direction_segment_merged"] = True
            continue

        merged[-1].meta["adjacent_same_direction_segment_dropped"] = segment.id

    return merged


def _apply_segment_confirmation(segments: Sequence[Segment]) -> None:
    for index, segment in enumerate(segments):
        segment.confirmed = False
        segment.meta["status"] = "pending"
        segment.meta["confirmed_by_segment_id"] = None

        if index + 1 >= len(segments):
            continue

        next_segment = segments[index + 1]
        feature_break = segment.meta.get("feature_sequence_break")
        if isinstance(feature_break, dict):
            pending_reason = feature_break.get("pending_reason")
            if pending_reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                continue
            if pending_reason == "no_feature_fractal":
                continue
        if next_segment.direction != segment.direction:
            segment.confirmed = True
            segment.meta["status"] = "confirmed"
            segment.meta["confirmed_by_segment_id"] = next_segment.id


def _segments_are_connected_to_stroke(previous: Segment, stroke: Stroke) -> bool:
    return (
        previous.end_timestamp == stroke.start_timestamp
        and abs(previous.end_price - stroke.start_price) < 1e-9
    )


def _make_unfinished_tail_segment(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    potential_endpoints: Set[int],
    *,
    mapping_strategy: str,
    segment_has_directional_price_span: Callable[[str, float, float], bool],
) -> Segment | None:
    window = list(strokes[start_index : end_index + 1])
    first = window[0]
    direction = first.direction
    drawable_end_index = start_index
    for index in range(start_index, end_index + 1):
        if strokes[index].direction != direction:
            continue
        if segment_has_directional_price_span(
            segment_direction=direction,
            start_price=first.start_price,
            end_price=strokes[index].end_price,
        ) and _is_more_extreme(
            direction=direction,
            current_price=strokes[drawable_end_index].end_price,
            candidate_price=strokes[index].end_price,
        ):
            drawable_end_index = index

    endpoint_stroke = strokes[drawable_end_index]
    if not first.start_timestamp or not endpoint_stroke.end_timestamp:
        return None
    if not segment_has_directional_price_span(
        segment_direction=direction,
        start_price=first.start_price,
        end_price=endpoint_stroke.end_price,
    ):
        return None

    full_window = list(strokes[start_index : end_index + 1])

    return Segment(
        id=f"segment_growing_{start_index + 1:03d}_{first.start_timestamp}_{endpoint_stroke.end_timestamp}",
        direction=direction,
        stroke_ids=[stroke.id for stroke in full_window],
        start_timestamp=first.start_timestamp,
        end_timestamp=endpoint_stroke.end_timestamp,
        start_price=first.start_price,
        end_price=endpoint_stroke.end_price,
        confirmed=False,
        meta={
            "mapping_strategy": mapping_strategy,
            "status": "growing",
            "provisional": True,
            "stroke_count": len(full_window),
            "start_stroke_index": start_index,
            "end_stroke_index": end_index,
            "drawable_end_stroke_index": drawable_end_index,
            "available_tail_end_stroke_index": end_index,
            "endpoint_is_potential_extreme": drawable_end_index in potential_endpoints,
            "endpoint_direction_valid": segment_has_directional_price_span(
                segment_direction=direction,
                start_price=first.start_price,
                end_price=endpoint_stroke.end_price,
            ),
            "endpoint_update_indices": [],
            "feature_sequence_break": None,
            "confirmed_by_segment_id": None,
        },
    )


def _append_unfinished_tail_segment(
    segments: list[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
    *,
    segments_are_connected_to_stroke: Callable[[Segment, Stroke], bool] = _segments_are_connected_to_stroke,
    make_unfinished_tail_segment: Callable[[Sequence[Stroke], int, int, Set[int]], Segment | None],
) -> None:
    if not segments:
        return

    last_segment = segments[-1]
    feature_break = last_segment.meta.get("feature_sequence_break")
    if isinstance(feature_break, dict) and feature_break.get("pending_reason") == (
        "gap_feature_fractal_waiting_for_followup_reverse_fractal"
    ):
        return

    last_end_index = int(last_segment.meta.get("end_stroke_index", -1))
    tail_start_index = last_end_index + 1
    if tail_start_index >= len(strokes):
        return

    first_tail = strokes[tail_start_index]
    if first_tail.direction == last_segment.direction:
        return
    if not segments_are_connected_to_stroke(last_segment, first_tail):
        return

    tail_end_index = len(strokes) - 1
    segment = make_unfinished_tail_segment(
        strokes=strokes,
        start_index=tail_start_index,
        end_index=tail_end_index,
        potential_endpoints=potential_endpoints,
    )
    if segment is not None:
        segments.append(segment)
