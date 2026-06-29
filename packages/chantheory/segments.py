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
    f1_index = current_end_index - 1
    f2_index = current_end_index + 1

    if f2_index >= len(strokes):
        return FeatureBreakSignal(False, "insufficient_feature_sequence", {})

    # 反向特征序列的方向 = 与当前段方向相反
    opposite_direction = "down" if direction == "up" else "up"

    # Extract all feature strokes
    feature_strokes = []
    for idx in range(f1_index, len(strokes), 2):
        if idx >= 0:
            stroke = strokes[idx]
            # 防御性方向校验：特征序列每根笔都应等于反向段方向。
            # 如果上游 strokes 不严格交替，直接报错跳过，避免错误特征序列。
            if stroke.direction != opposite_direction:
                return FeatureBreakSignal(
                    False,
                    "feature_sequence_direction_mismatch",
                    {
                        "expected_direction": opposite_direction,
                        "violating_index": idx,
                        "violating_direction": stroke.direction,
                    },
                )
            feature_strokes.append((idx, _stroke_range(idx, stroke)))

    if len(feature_strokes) < 3:
        # Check if we found a new peak in the same direction strokes
        if len(strokes) % 2 != (current_end_index % 2):
            last_idx = len(strokes) - 1
            if _is_more_extreme(direction, current_price=strokes[current_end_index].end_price, candidate_price=strokes[last_idx].end_price):
                return FeatureBreakSignal(False, "new_peak_found", {"new_peak_index": last_idx})
        return FeatureBreakSignal(False, "insufficient_feature_sequence", {})

    # 特征序列不做包含合并：每根反向笔作为独立元素参与分型判断。
    # 缠论特征序列分型只要求中间元素相对左右元素成极值，不需要像 K 线那样先做包含合并。
    processed_features = [(idx, r, [idx]) for idx, r in feature_strokes]

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

    first_fractal_signal = None
    first_fractal_completion_idx = None

    # Now look for a fractal in the processed features
    for i in range(len(processed_features) - 2):
        f1_idx, f1_r, f1_orig = processed_features[i]
        f2_idx, f2_r, f2_orig = processed_features[i+1]
        f3_idx, f3_r, f3_orig = processed_features[i+2]
        
        # Is f2 the extreme?
        is_fractal = False
        if direction == "up":
            # Looking for TOP fractal
            if f2_r.high > f3_r.high and f2_r.low > f3_r.low:
                if f2_r.high > f1_r.high and f2_r.low > f1_r.low:
                    is_fractal = True
                elif _range_contains(f2_r, f1_r):
                    is_fractal = True
        else:
            # Looking for BOTTOM fractal
            if f2_r.low < f3_r.low and f2_r.high < f3_r.high:
                if f2_r.low < f1_r.low and f2_r.high < f1_r.high:
                    is_fractal = True
                elif _range_contains(f2_r, f1_r):
                    is_fractal = True
                    
        if is_fractal:
            has_gap = _ranges_have_gap(f1_r, f2_r)
            base_meta: Dict[str, Any] = {
                "feature_sequence_indices": f1_orig + f2_orig + f3_orig,
                "feature_sequence_direction": strokes[f1_idx].direction,
                "first_second_has_gap": has_gap,
                "break_fractal": True,
                "left_contained_by_middle": _range_contains(f2_r, f1_r),
            }
            first_fractal_completion_idx = f3_idx
            if not has_gap:
                first_fractal_signal = FeatureBreakSignal(
                    True,
                    "no_gap_feature_fractal",
                    {**base_meta, "confirmation_case": "no_gap_feature_fractal", "followup_required": False},
                )
            else:
                # 第一二特征元素之间有缺口：必须再出现一个反向特征序列分型才确认。
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
                            "pending_reason": "gap_feature_fractal_waiting_for_followup_reverse_fractal",
                        },
                    )
            break

    # Compare chronological order of new peak vs fractal completion
    if first_new_peak_idx is not None and first_fractal_completion_idx is not None:
        if first_new_peak_idx < first_fractal_completion_idx:
            return FeatureBreakSignal(False, "new_peak_found", {"new_peak_index": first_new_peak_idx})
        else:
            return first_fractal_signal
    elif first_new_peak_idx is not None:
        return FeatureBreakSignal(False, "new_peak_found", {"new_peak_index": first_new_peak_idx})
    elif first_fractal_signal is not None:
        return first_fractal_signal

    # 到这里说明特征序列已有足够元素（len(feature_strokes) >= 3），但既没有
    # 形成分型，也没有出现新峰值。与 "insufficient_feature_sequence"（特征序列
    # 元素不足，数据不够）区分开：这里是"有足够特征序列但未形成分型"，按缠论
    # 规则线段未被破坏，应保持 growing 而不是固定终点。
    return FeatureBreakSignal(False, "no_feature_fractal", {})


def _has_gap_followup_reverse_fractal(
    strokes: Sequence[Stroke],
    current_end_index: int,
    direction: str,
) -> FeatureBreakSignal:
    """缺口特征分型出现后，等待反向段特征序列形成分型再确认。

    direction 是当前段方向。缺口分型在反向特征序列中出现后，
    需要用反向段方向的特征序列（反向笔）再形成一个分型来确认段结束。

    反向段特征序列起点 = current_end_index + 1（第一根反向笔），
    步长 2，取三元素：current_end_index + 1, +3, +5。
    分型方向 = 反向段方向（与当前段方向相反）。
    """
    opposite_direction = "down" if direction == "up" else "up"
    reverse_feature_indices = [
        current_end_index + 1,
        current_end_index + 3,
        current_end_index + 5,
    ]
    if reverse_feature_indices[-1] >= len(strokes):
        return FeatureBreakSignal(
            False,
            "insufficient_followup_reverse_sequence",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    # 反向段特征序列的方向应等于 opposite_direction
    if any(strokes[index].direction != opposite_direction for index in reverse_feature_indices):
        return FeatureBreakSignal(
            False,
            "followup_reverse_direction_mismatch",
            {"followup_reverse_feature_indices": reverse_feature_indices},
        )

    ranges = [_stroke_range(index, strokes[index]) for index in reverse_feature_indices]
    # 反向段分型：opposite_direction == 'down' → 底分型（中间最低）
    #            opposite_direction == 'up' → 顶分型（中间最高）
    confirmed = _is_feature_break_fractal(direction=opposite_direction, ranges=ranges)
    return FeatureBreakSignal(
        confirmed,
        "followup_reverse_fractal" if confirmed else "no_followup_reverse_fractal",
        {
            "followup_reverse_feature_indices": reverse_feature_indices,
            "followup_reverse_fractal": confirmed,
        },
    )


def _is_feature_break_fractal(direction: str, ranges: Sequence[StrokeRange]) -> bool:
    """三元素特征序列分型：direction 是当前段方向。

    direction == 'up'  → 顶分型（中间最高）
    direction == 'down' → 底分型（中间最低）
    """
    if len(ranges) < 3:
        return False
    left, middle, right = ranges[:3]
    if direction == "up":
        return middle.high >= left.high and middle.high > right.high
    if direction == "down":
        return middle.low <= left.low and middle.low < right.low
    return False
