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


def _process_feature_inclusion(
    feature_strokes: Sequence[tuple[int, StrokeRange]],
    direction: str,
) -> list[tuple[int, StrokeRange, list[int]]]:
    """对特征序列执行缠论包含处理。

    相邻两个特征元素如果存在包含关系（一个区间完全包含另一个），按动态合并方向合并：
    - 合并方向由最近两个无包含元素的高低点趋势决定，而非固定使用原线段方向
    - 向上趋势：取 high 更高者，low 取两者较高者（向上取齐）
    - 向下趋势：取 low 更低者，high 取两者较低者（向下取齐）
    - 初始无法判断方向时（前两个元素即有包含），使用原线段方向作为 fallback
    - 连续包含时沿用已确定的合并方向

    合并后的元素继续与前一个比较，直到无包含关系。
    返回 [(stroke_index, merged_range, original_indices), ...]。
    """
    if not feature_strokes:
        return []

    processed: list[tuple[int, StrokeRange, list[int]]] = []
    merge_direction: str | None = None

    for idx, rng in feature_strokes:
        current_idx = idx
        current_rng = rng
        current_orig = [idx]

        while processed:
            prev_idx, prev_rng, prev_orig = processed[-1]
            if not _ranges_have_inclusion(prev_rng, current_rng):
                # 无包含关系：根据两个元素的高低关系确定或更新合并方向
                if current_rng.high > prev_rng.high and current_rng.low > prev_rng.low:
                    merge_direction = "up"
                elif current_rng.high < prev_rng.high and current_rng.low < prev_rng.low:
                    merge_direction = "down"
                break

            # 存在包含关系，需要合并
            # 合并方向尚未确定时（初始两个元素即有包含），使用原线段方向作为 fallback
            if merge_direction is None:
                merge_direction = direction

            if merge_direction == "up":
                merged_high = max(prev_rng.high, current_rng.high)
                merged_low = max(prev_rng.low, current_rng.low)
            else:
                merged_low = min(prev_rng.low, current_rng.low)
                merged_high = min(prev_rng.high, current_rng.high)

            merged_rng = StrokeRange(
                stroke_index=prev_idx,
                low=merged_low,
                high=merged_high,
            )
            merged_orig = prev_orig + current_orig
            processed.pop()
            current_idx = prev_idx
            current_rng = merged_rng
            current_orig = merged_orig

        processed.append((current_idx, current_rng, current_orig))

    return processed


def _ranges_have_inclusion(a: StrokeRange, b: StrokeRange) -> bool:
    """检查两个区间是否存在包含关系（任一方包含另一方）。"""
    return (a.low <= b.low and a.high >= b.high) or (b.low <= a.low and b.high >= a.high)


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
    raw_features: list[tuple[int, StrokeRange]] = []
    consumed_indices: list[int] = []
    ever_had_three_processed = False

    # 持续按步长 2 收集反向特征笔
    idx = current_end_index + 1
    while idx < len(strokes):
        if strokes[idx].direction != opposite_direction:
            return feature_break_signal_factory(
                False,
                "followup_reverse_direction_mismatch",
                {"followup_reverse_feature_indices": consumed_indices},
            )
        raw_features.append((idx, stroke_range(idx, strokes[idx])))
        consumed_indices.append(idx)

        # 每次添加新元素后都进行包含处理
        processed_features = _process_feature_inclusion(raw_features, opposite_direction)
        
        if len(processed_features) >= 3:
            ever_had_three_processed = True
            processed_ranges = [item[1] for item in processed_features]
            
            # 对所有滑动窗口进行检查，寻找第一个有效分型
            for window_start in range(len(processed_features) - 2):
                window_ranges = processed_ranges[window_start : window_start + 3]
                confirmed = is_feature_break_fractal(
                    direction=opposite_direction,
                    ranges=window_ranges,
                )
                if confirmed:
                    # 获取窗口第三个元素的所有原始索引，取最大值作为完成位置
                    window = processed_features[window_start : window_start + 3]
                    right_orig_indices = window[2][2]
                    completion_idx = max(right_orig_indices)
                    return feature_break_signal_factory(
                        confirmed,
                        "followup_reverse_fractal",
                        {
                            "followup_reverse_feature_indices": consumed_indices,
                            "followup_reverse_fractal": confirmed,
                            "followup_reverse_inclusion_applied": len(processed_features) < len(raw_features),
                            "followup_reverse_completion_idx": completion_idx,
                        },
                    )
        
        idx += 2

    # 数据耗尽后判断返回值
    if ever_had_three_processed:
        # 曾经获得至少三个处理后元素，但未找到分型
        return feature_break_signal_factory(
            False,
            "no_followup_reverse_fractal",
            {
                "followup_reverse_feature_indices": consumed_indices,
                "followup_reverse_fractal": False,
                "followup_reverse_inclusion_applied": len(_process_feature_inclusion(raw_features, opposite_direction)) < len(raw_features) if raw_features else False,
            },
        )
    else:
        # 始终不足三个处理后元素
        return feature_break_signal_factory(
            False,
            "insufficient_followup_reverse_sequence",
            {"followup_reverse_feature_indices": consumed_indices},
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

    # 特征序列包含处理：合并相邻有包含关系的特征元素
    processed_features = _process_feature_inclusion(feature_strokes, direction)

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
    confirmation_completion_idx = None

    for index in range(len(processed_features) - 2):
        f1_idx, f1_r, f1_orig = processed_features[index]
        _f2_idx, f2_r, f2_orig = processed_features[index + 1]
        f3_idx, f3_r, f3_orig = processed_features[index + 2]

        is_fractal = False
        if direction == "up":
            # 顶分型：f2.high 必须高于 f3.high（右侧关系必须满足），
            # 且 f2.high 不低于 f1.high（直接高于 或 通过包含关系吸收f1）
            if f2_r.high > f3_r.high and (
                f2_r.high >= f1_r.high or range_contains(f2_r, f1_r)
            ):
                is_fractal = True
        else:
            # 底分型：f2.low 必须低于 f3.low（右侧关系必须满足），
            # 且 f2.low 不高于 f1.low（直接低于 或 通过包含关系吸收f1）
            if f2_r.low < f3_r.low and (
                f2_r.low <= f1_r.low or range_contains(f2_r, f1_r)
            ):
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
            primary_completion_idx = max(f3_orig)
            if not has_gap:
                confirmation_completion_idx = primary_completion_idx
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
                    confirmation_completion_idx = followup.meta.get("followup_reverse_completion_idx")
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
                    # 缺口分型仍在等待，没有最终确认完成位置
                    confirmation_completion_idx = None
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

    if first_new_peak_idx is not None:
        if confirmation_completion_idx is None:
            # 缺口分型仍在等待确认，期间出现新高，必须延伸
            return feature_break_signal_factory(
                False,
                "new_peak_found",
                {"new_peak_index": first_new_peak_idx},
            )
        if first_new_peak_idx < confirmation_completion_idx:
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
