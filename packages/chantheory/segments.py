from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set

from .segment_helpers import (
    StrokeRange,
    _is_more_extreme,
    _range_contains,
    _ranges_have_gap,
    _stroke_range,
)
from .segment_postprocess import (
    _apply_segment_confirmation as _apply_segment_confirmation_impl,
    _append_unfinished_tail_segment as _append_unfinished_tail_segment_impl,
    _extend_segment_if_more_extreme as _extend_segment_if_more_extreme_impl,
    _make_unfinished_tail_segment as _make_unfinished_tail_segment_impl,
    _merge_adjacent_same_direction_segments as _merge_adjacent_same_direction_segments_impl,
    _segments_are_connected_to_stroke as _segments_are_connected_to_stroke_impl,
)
from .segment_endpoint_helpers import (
    _endpoint_from_stroke as _endpoint_from_stroke_impl,
    _find_window_extreme_start_index as _find_window_extreme_start_index_impl,
    _potential_endpoint_indices as _potential_endpoint_indices_impl,
)
from .segment_break_helpers import (
    _has_gap_followup_reverse_fractal as _has_gap_followup_reverse_fractal_impl,
    _is_feature_break_fractal as _is_feature_break_fractal_impl,
    _opposite_segment_break_signal as _opposite_segment_break_signal_impl,
)
from .segment_seed_helpers import (
    _is_valid_segment_seed as _is_valid_segment_seed_impl,
    _segment_has_directional_price_span as _segment_has_directional_price_span_impl,
    _strokes_have_overlap as _strokes_have_overlap_impl,
)
from .schema import Segment, Stroke


SEGMENT_MAPPING_STRATEGY = "chan_feature_sequence_gap_fractal_confirmation"


@dataclass(frozen=True)
class SegmentEndpoint:
    stroke_index: int
    direction: str
    timestamp: str
    price: float


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

        peak_index = start_index + 2
        endpoint_updates: List[int] = []
        feature_break_meta: Dict[str, Any] | None = None

        # 防御性上限：peak_index 每次必须严格递增，最多遍历到 strokes 末尾。
        max_iterations = max(len(strokes) - peak_index, 1)
        for _ in range(max_iterations):
            break_signal = _opposite_segment_break_signal(
                strokes=strokes,
                current_end_index=peak_index,
                direction=direction,
            )

            if break_signal.confirmed:
                feature_break_meta = break_signal.meta
                break

            if break_signal.reason == "new_peak_found":
                new_peak = break_signal.meta["new_peak_index"]
                # 防止意外回退或停滞导致死循环
                if new_peak <= peak_index:
                    break
                if new_peak in endpoint_updates:
                    break
                endpoint_updates.append(new_peak)
                peak_index = new_peak
                continue

            if break_signal.reason == "gap_feature_fractal_waiting_for_followup_reverse_fractal":
                # 有缺口但未出现后续反向分型：本段保持 pending，等待更多笔
                feature_break_meta = break_signal.meta
                break

            if break_signal.reason == "insufficient_feature_sequence":
                break

            # 有足够特征序列元素但未形成分型：按缠论规则（文档 2.1.8），线段
            # 结束必须由特征序列分型确认。标记此段为 no_feature_fractal，后续
            # 确认逻辑不会确认它，避免在没有顶/底分型的情况下把段标记为已完成。
            if break_signal.reason == "no_feature_fractal":
                feature_break_meta = {"pending_reason": "no_feature_fractal"}
                break

            # 其他未明确分支：保守退出，避免死循环
            break

        end_index = peak_index
        segment = _make_segment(
            strokes=strokes,
            start_index=start_index,
            end_index=end_index,
            potential_endpoints=potential_endpoints,
            endpoint_updates=endpoint_updates,
            feature_break_meta=feature_break_meta,
        )
        if segment is not None:
            # Check if this segment violates the absolute extreme rule
            # and contains a stroke that extends the PREVIOUS segment
            if segments and segments[-1].direction != segment.direction:
                prev_segment = segments[-1]
                violation_idx = -1
                for idx in range(start_index, end_index + 1):
                    if strokes[idx].direction == prev_segment.direction:
                        if _is_more_extreme(prev_segment.direction, current_price=prev_segment.end_price, candidate_price=strokes[idx].end_price):
                            violation_idx = idx
                            break
                
                if violation_idx != -1:
                    # The previous segment should be extended to violation_idx
                    if _extend_segment_if_more_extreme(
                        segment=prev_segment,
                        strokes=strokes,
                        end_index=violation_idx,
                        potential_endpoints=potential_endpoints,
                    ):
                        start_index = violation_idx + 1
                        continue

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

            # 段起点必须是 window 内的绝对极值。
            # 如果起点不是极值（未被上方 violation 检查处理的情况），
            # 从 window 内的真正极值点重新开始找段。
            extreme_start = _find_window_extreme_start_index(
                strokes=strokes,
                start_index=start_index,
                end_index=end_index,
                direction=direction,
            )
            if extreme_start > start_index:
                start_index = extreme_start
                continue

            segments.append(segment)
            start_index = end_index + 1
        else:
            start_index += 1

    segments = _merge_adjacent_same_direction_segments(segments, strokes=strokes, potential_endpoints=potential_endpoints)
    segments = _enforce_segment_contract(segments, strokes=strokes)
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
            "endpoint_is_potential_extreme": end_index in potential_endpoints,
            "endpoint_direction_valid": _segment_has_directional_price_span(
                segment_direction=direction,
                start_price=first.start_price,
                end_price=last.end_price,
            ),
            "endpoint_update_indices": list(endpoint_updates),
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
    return _extend_segment_if_more_extreme_impl(
        segment=segment,
        strokes=strokes,
        end_index=end_index,
        potential_endpoints=potential_endpoints,
    )


def _merge_adjacent_same_direction_segments(
    segments: Sequence[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
) -> List[Segment]:
    return _merge_adjacent_same_direction_segments_impl(
        segments=segments,
        strokes=strokes,
        potential_endpoints=potential_endpoints,
        extend_segment_if_more_extreme=_extend_segment_if_more_extreme,
    )


def _apply_segment_confirmation(segments: Sequence[Segment]) -> None:
    _apply_segment_confirmation_impl(segments)


def _enforce_segment_contract(segments: Sequence[Segment], strokes: Sequence[Stroke] = None) -> List[Segment]:
    valid: List[Segment] = []
    dropped: List[Dict[str, Any]] = []
    for segment in segments:
        if segment.direction not in {"up", "down"}:
            dropped.append({"segment_id": segment.id, "reason": "invalid_direction"})
            continue
        if len(segment.stroke_ids) < 3 or len(segment.stroke_ids) % 2 == 0:
            dropped.append({"segment_id": segment.id, "reason": "stroke_count_invalid", "stroke_count": len(segment.stroke_ids)})
            continue
        if valid and valid[-1].direction == segment.direction:
            dropped.append({"segment_id": segment.id, "reason": "same_direction_as_previous_after_merge"})
            continue
        if not _segment_has_directional_price_span(
            segment_direction=segment.direction,
            start_price=segment.start_price,
            end_price=segment.end_price,
        ):
            dropped.append({"segment_id": segment.id, "reason": "endpoint_direction_invalid"})
            continue

        # Record whether the start and end are the absolute extremes of the segment.
        #
        # This used to be a hard rejection. In real 5m data it can drop the first
        # opposite segment after a confirmed segment, leaving valid[-1] stuck on
        # an old segment and causing a same-direction / not-connected cascade.
        # Keep the diagnostic metadata, but let the later connection and
        # direction rules decide whether the candidate can participate.
        if strokes:
            start_idx = int(segment.meta.get("start_stroke_index", -1))
            end_idx = int(segment.meta.get("end_stroke_index", -1))
            if start_idx >= 0 and end_idx >= 0 and end_idx < len(strokes):
                window = strokes[start_idx:end_idx + 1]
                highs = [max(s.start_price, s.end_price) for s in window]
                lows = [min(s.start_price, s.end_price) for s in window]
                max_price = max(highs)
                min_price = min(lows)

                # We use a small epsilon for floating point comparison
                eps = 1e-9
                if segment.direction == "up":
                    endpoint_is_absolute_extreme = segment.start_price <= min_price + eps and segment.end_price >= max_price - eps
                else:
                    endpoint_is_absolute_extreme = segment.start_price >= max_price - eps and segment.end_price <= min_price + eps
                segment.meta["endpoint_is_absolute_extreme"] = endpoint_is_absolute_extreme
                if not endpoint_is_absolute_extreme:
                    segment.meta["endpoint_absolute_extreme_diagnostic"] = {
                        "direction": segment.direction,
                        "segment_start_price": segment.start_price,
                        "segment_end_price": segment.end_price,
                        "window_min_price": min_price,
                        "window_max_price": max_price,
                    }

        if valid and not _segments_are_connected(valid[-1], segment):
            dropped.append({
                "segment_id": segment.id,
                "reason": "not_connected_to_previous_segment",
                "previous_segment_id": valid[-1].id,
            })
            continue
        valid.append(segment)

    if dropped and valid:
        # 把被丢弃段的摘要挂到最末有效段的 meta 上，便于上游排查"段消失"问题。
        # 如果所有候选段都被丢掉，derive_segments 当前返回 []；后续如需暴露诊断，
        # 应通过显式返回结构或 analysis warnings 传递，避免模块级全局状态。
        valid[-1].meta["dropped_segments_summary"] = dropped
    return valid


def _segments_are_connected_to_stroke(previous: Segment, stroke: Stroke) -> bool:
    return _segments_are_connected_to_stroke_impl(previous, stroke)


def _make_unfinished_tail_segment(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    potential_endpoints: Set[int],
) -> Segment | None:
    return _make_unfinished_tail_segment_impl(
        strokes=strokes,
        start_index=start_index,
        end_index=end_index,
        potential_endpoints=potential_endpoints,
        mapping_strategy=SEGMENT_MAPPING_STRATEGY,
        segment_has_directional_price_span=_segment_has_directional_price_span,
    )


def _append_unfinished_tail_segment(
    segments: List[Segment],
    strokes: Sequence[Stroke],
    potential_endpoints: Set[int],
) -> None:
    _append_unfinished_tail_segment_impl(
        segments=segments,
        strokes=strokes,
        potential_endpoints=potential_endpoints,
        segments_are_connected_to_stroke=_segments_are_connected_to_stroke,
        make_unfinished_tail_segment=_make_unfinished_tail_segment,
    )


def _segments_are_connected(previous: Segment, current: Segment) -> bool:
    return previous.end_timestamp == current.start_timestamp and abs(previous.end_price - current.start_price) < 1e-9


def _find_window_extreme_start_index(
    strokes: Sequence[Stroke],
    start_index: int,
    end_index: int,
    direction: str,
) -> int:
    return _find_window_extreme_start_index_impl(
        strokes=strokes,
        start_index=start_index,
        end_index=end_index,
        direction=direction,
    )


def _potential_endpoint_indices(strokes: Sequence[Stroke]) -> Set[int]:
    return _potential_endpoint_indices_impl(
        strokes=strokes,
        endpoint_from_stroke=_endpoint_from_stroke,
    )


def _endpoint_from_stroke(index: int, stroke: Stroke) -> SegmentEndpoint:
    return _endpoint_from_stroke_impl(
        index=index,
        stroke=stroke,
        endpoint_factory=SegmentEndpoint,
    )


def _strokes_have_overlap(strokes: Sequence[Stroke]) -> bool:
    return _strokes_have_overlap_impl(strokes)


def _is_valid_segment_seed(strokes: Sequence[Stroke]) -> bool:
    return _is_valid_segment_seed_impl(
        strokes,
        strokes_have_overlap=_strokes_have_overlap,
        segment_has_directional_price_span=_segment_has_directional_price_span,
    )


def _segment_has_directional_price_span(
    segment_direction: str,
    start_price: float,
    end_price: float,
) -> bool:
    return _segment_has_directional_price_span_impl(
        segment_direction,
        start_price,
        end_price,
    )


def _opposite_segment_break_signal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
) -> FeatureBreakSignal:
    return _opposite_segment_break_signal_impl(
        strokes=strokes,
        current_end_index=current_end_index,
        direction=direction,
        stroke_range=_stroke_range,
        is_more_extreme=_is_more_extreme,
        range_contains=_range_contains,
        ranges_have_gap=_ranges_have_gap,
        has_gap_followup_reverse_fractal=_has_gap_followup_reverse_fractal,
        feature_break_signal_factory=FeatureBreakSignal,
    )


def _has_gap_followup_reverse_fractal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
) -> FeatureBreakSignal:
    return _has_gap_followup_reverse_fractal_impl(
        strokes=strokes,
        current_end_index=current_end_index,
        direction=direction,
        stroke_range=_stroke_range,
        is_feature_break_fractal=_is_feature_break_fractal,
        feature_break_signal_factory=FeatureBreakSignal,
    )


def _is_feature_break_fractal(direction: str, ranges: Sequence[StrokeRange]) -> bool:
    return _is_feature_break_fractal_impl(direction=direction, ranges=ranges)
