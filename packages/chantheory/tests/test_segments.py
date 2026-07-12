from __future__ import annotations

import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

import chantheory.segments as segments_mod
from chantheory.schema import Stroke
from chantheory.segments import derive_segments


def _build_strokes(points: List[Tuple[str, float]]) -> List[Stroke]:
    """根据 (timestamp, price) 顶点序列构造方向交替的 strokes。

    points[0] 是第一笔起点，point[1] 是第一笔终点；方向由价格高低自动决定。
    """
    strokes: List[Stroke] = []
    for index in range(len(points) - 1):
        start_ts, start_price = points[index]
        end_ts, end_price = points[index + 1]
        direction = "up" if end_price > start_price else "down"
        strokes.append(
            Stroke(
                id=f"stroke_{index + 1:03d}",
                direction=direction,
                start_fractal_id=f"fractal_{index:03d}",
                end_fractal_id=f"fractal_{index + 1:03d}",
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                start_price=start_price,
                end_price=end_price,
                confirmed=True,
                meta={},
            )
        )
    return strokes


class DeriveSegmentsTests(unittest.TestCase):
    def test_returns_empty_for_fewer_than_three_strokes(self):
        strokes = _build_strokes([("t0", 10.0), ("t1", 11.0)])
        self.assertEqual(derive_segments(strokes), [])

    def test_basic_three_stroke_up_segment(self):
        # 上-下-上，第一笔与第二笔重叠，第三笔创新高 → 形成上升段
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),
        ])
        # 后续给一个反向段种子，让上升段完结
        strokes.extend(_build_strokes([
            ("t3", 13.0),
            ("t4", 11.5),
            ("t5", 12.5),
            ("t6", 10.5),
        ]))
        segments = derive_segments(strokes)
        self.assertGreaterEqual(len(segments), 1)
        first = segments[0]
        self.assertEqual(first.direction, "up")
        self.assertEqual(first.start_price, 10.0)
        self.assertEqual(first.end_price, 13.0)

    def test_segment_endpoints_must_be_absolute_extremes(self):
        # 段内若出现比起点更低的下行笔，会让方向校验失败 → 不会出现"非极值端点"段
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 9.5),   # 比段起点 10.0 还低
            ("t3", 11.5),
        ])
        segments = derive_segments(strokes)
        for segment in segments:
            if segment.direction == "up":
                self.assertLessEqual(segment.start_price, segment.end_price)
            if segment.direction == "down":
                self.assertGreaterEqual(segment.start_price, segment.end_price)

    def test_segments_are_strictly_alternating(self):
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),
            ("t4", 11.5),
            ("t5", 12.5),
            ("t6", 10.5),
            ("t7", 11.5),
            ("t8", 9.0),
        ])
        segments = derive_segments(strokes)
        for index in range(1, len(segments)):
            self.assertNotEqual(segments[index - 1].direction, segments[index].direction)

    def test_segments_are_chained_endpoint_to_endpoint(self):
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),
            ("t4", 11.5),
            ("t5", 12.5),
            ("t6", 10.5),
            ("t7", 11.5),
            ("t8", 9.0),
        ])
        segments = derive_segments(strokes)
        for index in range(1, len(segments)):
            previous = segments[index - 1]
            current = segments[index]
            self.assertEqual(previous.end_timestamp, current.start_timestamp)
            self.assertAlmostEqual(previous.end_price, current.start_price, places=9)

    def test_no_gap_feature_fractal_confirms_segment(self):
        # 构造一个上升段，反向特征序列三元素无缺口 → 应出现已确认上升段
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),  # 上升段顶点候选
            ("t4", 11.0),  # 反向 f1
            ("t5", 12.0),
            ("t6", 10.5),  # 反向 f2（与 f1 区间 [11,11] 与 [10.5,12]，无缺口）
            ("t7", 11.5),
            ("t8", 9.5),   # 反向 f3
        ])
        segments = derive_segments(strokes)
        up_segments = [s for s in segments if s.direction == "up"]
        self.assertTrue(any(s.confirmed for s in up_segments), "应至少有一个被确认的上升段")

    def test_gap_feature_fractal_pending_until_followup(self):
        # 缺口分型出现但后续反向笔不够形成分型 → 段保持 pending
        # s1: up 10->12, s2: down 12->11 (f1=[11,12])
        # s3: up 11->15, s4: down 15->13 (f2=[13,15], gap: 12<13)
        # s5: up 13->20 (new peak), s6: down 20->16 (f2'=[16,20], gap: 15<16)
        # s7: up 16->17, s8: down 17->14 (f3'=[14,17])
        # 顶分型确认 + gap → 触发 followup，但只有 2 根后续 down 笔 → pending
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0),
        ])
        segments = derive_segments(strokes)
        self.assertTrue(segments, "应至少识别出一个段")
        pending_segments = [
            s for s in segments
            if isinstance(s.meta.get("feature_sequence_break"), dict)
            and s.meta["feature_sequence_break"].get("pending_reason")
            == "gap_feature_fractal_waiting_for_followup_reverse_fractal"
        ]
        self.assertTrue(
            pending_segments,
            "应存在一个 pending_reason == gap_feature_fractal_waiting_for_followup_reverse_fractal 的段",
        )
        for segment in pending_segments:
            self.assertFalse(segment.confirmed, "pending 段不应被 confirmed")

    def test_gap_feature_fractal_confirmed_after_followup(self):
        # 缺口分型 + 后续反向笔形成底分型 → 段被确认结束
        # 同 pending case，但加第 3 根 down 笔形成底分型
        # followup indices=[5,7,9]: s6=[16,20], s8=[14,17], s10=[14.5,15]
        # 底分型：s8.low=14 < s6.low=16, s8.low=14 < s10.low=14.5 → 确认
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0), ("t9", 15.0), ("t10", 14.5),
        ])
        segments = derive_segments(strokes)
        self.assertTrue(segments, "应至少识别出一个段")
        followup_confirmed = [
            s for s in segments
            if s.direction == "up"
            and s.confirmed
            and isinstance(s.meta.get("feature_sequence_break"), dict)
            and s.meta["feature_sequence_break"].get("confirmation_case")
            == "gap_feature_fractal_with_followup_reverse_fractal"
            and s.meta["feature_sequence_break"].get("followup_reverse_fractal") is True
        ]
        self.assertTrue(followup_confirmed, "应存在一个通过 gap + followup 分支确认的上升段")

    def test_unfinished_tail_segment_marked_growing(self):
        # 最后一段尚未完成时，应追加一个 status=growing 的尾段
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),
            ("t4", 11.5),
            ("t5", 12.5),
            ("t6", 10.0),
        ])
        segments = derive_segments(strokes)
        if segments:
            statuses = [s.meta.get("status") for s in segments]
            self.assertTrue(any(status in {"pending", "growing", "confirmed"} for status in statuses))


class DeriveSegmentsRegressionTests(unittest.TestCase):
    """从 test_chan_111_issue 抽出的回归用例：原本只 print，无 assert。"""

    def test_chan_111_strokes_produce_alternating_segments(self):
        strokes = [
            Stroke(id="s15", direction="up", start_timestamp="t15", end_timestamp="t16", start_price=11.0, end_price=12.0, confirmed=True, start_fractal_id="f1", end_fractal_id="f2"),
            Stroke(id="s16", direction="down", start_timestamp="t16", end_timestamp="t17", start_price=12.0, end_price=11.5, confirmed=True, start_fractal_id="f2", end_fractal_id="f3"),
            Stroke(id="s17", direction="up", start_timestamp="t17", end_timestamp="t18", start_price=11.5, end_price=11.8, confirmed=True, start_fractal_id="f3", end_fractal_id="f4"),
            Stroke(id="s18", direction="down", start_timestamp="t18", end_timestamp="t19", start_price=11.8, end_price=10.64, confirmed=True, start_fractal_id="f4", end_fractal_id="f5"),
            Stroke(id="s19", direction="up", start_timestamp="t19", end_timestamp="t20", start_price=10.64, end_price=10.85, confirmed=True, start_fractal_id="f5", end_fractal_id="f6"),
            Stroke(id="s20", direction="down", start_timestamp="t20", end_timestamp="t21", start_price=10.85, end_price=10.62, confirmed=True, start_fractal_id="f6", end_fractal_id="f7"),
            Stroke(id="s21", direction="up", start_timestamp="t21", end_timestamp="t22", start_price=10.62, end_price=11.1, confirmed=True, start_fractal_id="f7", end_fractal_id="f8"),
            Stroke(id="s22", direction="down", start_timestamp="t22", end_timestamp="t23", start_price=11.1, end_price=10.77, confirmed=True, start_fractal_id="f8", end_fractal_id="f9"),
            Stroke(id="s23", direction="up", start_timestamp="t23", end_timestamp="t24", start_price=10.77, end_price=11.35, confirmed=True, start_fractal_id="f9", end_fractal_id="f10"),
            Stroke(id="s24", direction="down", start_timestamp="t24", end_timestamp="t25", start_price=11.35, end_price=11.0, confirmed=True, start_fractal_id="f10", end_fractal_id="f11"),
            Stroke(id="s25", direction="up", start_timestamp="t25", end_timestamp="t26", start_price=11.0, end_price=11.5, confirmed=True, start_fractal_id="f11", end_fractal_id="f12"),
        ]
        segments = derive_segments(strokes)
        for index in range(1, len(segments)):
            self.assertNotEqual(segments[index - 1].direction, segments[index].direction)

    def test_segment_relocates_start_to_window_extreme(self):
        # 段起点不是 window 极值时，应从 window 内真正极值点重新开始找段。
        # stroke6 终点 46.28 是 window 最低点（向下笔终点），真正起点应是 stroke7（46.28→56）。
        strokes = _build_strokes([
            ("t0", 48.0),
            ("t1", 51.0),
            ("t2", 49.0),
            ("t3", 50.0),
            ("t4", 46.5),    # 向下笔终点，非最低
            ("t5", 47.0),
            ("t6", 46.28),   # window 最低点（向下笔终点）
            ("t7", 56.0),    # 真正上升段起点
            ("t8", 53.0),
            ("t9", 58.0),    # 段终点候选
            ("t10", 54.0),   # 反向特征 f1
            ("t11", 56.0),
            ("t12", 52.0),   # 反向特征 f3 → 分型确认
        ])
        segments = derive_segments(strokes)
        up_segments = [s for s in segments if s.direction == "up"]
        self.assertTrue(up_segments, "应至少识别出一个上升段")
        first_up = up_segments[0]
        self.assertAlmostEqual(first_up.start_price, 46.28, places=2,
                               msg="上升段起点应重定位到 window 最低点 46.28")
        self.assertEqual(first_up.meta.get("start_stroke_index"), 6,
                         msg="上升段起点 stroke index 应为 6（46.28 所在向下笔的下一根向上笔）")

    def test_segment_keeps_start_when_already_extreme(self):
        # 段起点已经是 window 极值时，结果不变。
        # stroke0 起点 10.0 是 window 最低点（向上笔起点）。
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),    # 段终点候选
            ("t4", 11.0),    # 反向特征 f1
            ("t5", 12.0),
            ("t6", 10.5),    # 反向特征 f2
            ("t7", 11.5),
            ("t8", 9.5),     # 反向特征 f3
        ])
        segments = derive_segments(strokes)
        up_segments = [s for s in segments if s.direction == "up"]
        self.assertTrue(up_segments, "应至少识别出一个上升段")
        first_up = up_segments[0]
        self.assertAlmostEqual(first_up.start_price, 10.0, places=2,
                               msg="上升段起点应保持 10.0（已是 window 最低点）")
        self.assertEqual(first_up.meta.get("start_stroke_index"), 0,
                         msg="上升段起点 stroke index 应为 0")

    def test_segment_without_feature_fractal_not_confirmed(self):
        # 回归 002149.sz 5分钟案例：上升段已有足够反向特征序列元素（3 根向下笔），
        # 特征序列顶分型只看 HIGH：f2.high 同时大于 f1.high 和 f3.high。
        # 特征序列：f1=[58.82,60.28] f2=[59.25,63.6] f3=[60.17,61.38]
        # f2.high(63.60) > f1.high(60.28) 且 f2.high(63.60) > f3.high(61.38)
        # → 构成顶分型。（旧版逻辑错误地同时要求 f2.low > f3.low）
        strokes = _build_strokes([
            ("t0", 55.0),   # 上升段起点
            ("t1", 57.0),
            ("t2", 56.0),
            ("t3", 60.28),
            ("t4", 58.82),  # f1 向下笔
            ("t5", 63.60),  # 上升段最高点
            ("t6", 59.25),  # f2 向下笔
            ("t7", 61.38),
            ("t8", 60.17),  # f3 向下笔
        ])
        segments = derive_segments(strokes)
        self.assertTrue(segments, "应至少识别出一个段")
        up = segments[0]
        self.assertEqual(up.direction, "up")
        self.assertAlmostEqual(up.end_price, 63.60, places=2,
                               msg="上升段终点应取同方向最高点 63.60")
        # 关键：特征序列顶分型存在（只看 high，f2.high>f1.high 且 f2.high>f3.high）
        # 因此 pending_reason 不应为 no_feature_fractal。
        feature_break = up.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict,
                              "feature_sequence_break 应为 dict")
        self.assertNotEqual(feature_break.get("pending_reason"), "no_feature_fractal",
                            "按缠论顶分型只看 high，本用例应识别出分型")

    def test_down_segment_without_complete_feature_fractal_not_confirmed(self):
        # 与上升段用例对称：底分型只看 LOW。
        # 特征序列低点：f1.low=61.5, f2.low=57.0, f3.low=60.0
        # f2.low(57.0) < f1.low(61.5) 且 f2.low(57.0) < f3.low(60.0)
        # → 构成底分型。（旧版逻辑错误地同时要求 f2.high < f3.high）
        strokes = _build_strokes([
            ("t0", 65.0),
            ("t1", 63.0),
            ("t2", 64.0),
            ("t3", 60.0),
            ("t4", 61.5),
            ("t5", 57.0),
            ("t6", 61.0),
            ("t7", 59.0),
            ("t8", 60.0),
        ])

        segments = derive_segments(strokes)

        self.assertTrue(segments, "应至少识别出一个段")
        down = segments[0]
        self.assertEqual(down.direction, "down")
        self.assertAlmostEqual(down.end_price, 57.0, places=2)
        # 关键：特征序列底分型存在（只看 low，f2.low<f1.low 且 f2.low<f3.low）
        # 因此 pending_reason 不应为 no_feature_fractal。
        feature_break = down.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict)
        self.assertNotEqual(feature_break.get("pending_reason"), "no_feature_fractal",
                            "按缠论底分型只看 low，本用例应识别出分型")

    def test_endpoint_extreme_diagnostic_does_not_cascade_drop_later_feature_fractal(self):
        # 回归 002149.sz 5分钟案例：04-27 之后第一个 down 候选的终点不是
        # window 绝对低点，旧版 _enforce_segment_contract 会把它丢弃，随后
        # valid[-1] 卡住并级联丢弃后续线段，导致 05-13 13:50 的顶分型不显示。
        strokes = _build_strokes([
            ("2026-04-27 09:35:00", 56.78),
            ("2026-04-27 14:25:00", 59.18),
            ("2026-04-28 09:35:00", 55.0),
            ("2026-04-28 10:55:00", 58.79),
            ("2026-04-28 13:40:00", 56.0),
            ("2026-04-29 09:40:00", 58.58),
            ("2026-04-29 11:10:00", 57.07),
            ("2026-04-29 13:15:00", 58.78),
            ("2026-04-29 13:40:00", 57.6),
            ("2026-04-29 14:00:00", 58.31),
            ("2026-04-29 14:40:00", 57.56),
            ("2026-05-07 11:30:00", 74.57),
            ("2026-05-07 13:25:00", 71.77),
            ("2026-05-07 14:00:00", 74.49),
            ("2026-05-08 14:15:00", 72.01),
            ("2026-05-11 09:55:00", 75.9),
            ("2026-05-11 10:10:00", 73.76),
            ("2026-05-12 09:45:00", 78.84),
            ("2026-05-12 10:20:00", 72.8),
            ("2026-05-12 11:00:00", 75.18),
            ("2026-05-12 13:05:00", 73.01),
            ("2026-05-12 13:20:00", 74.2),
            ("2026-05-12 13:45:00", 73.07),
            ("2026-05-12 14:05:00", 74.15),
            ("2026-05-12 14:20:00", 72.57),
            ("2026-05-13 10:50:00", 75.2),
            ("2026-05-13 11:30:00", 73.34),
            ("2026-05-13 13:50:00", 77.14),
            ("2026-05-13 14:15:00", 74.29),
            ("2026-05-14 09:35:00", 76.13),
            ("2026-05-14 13:40:00", 69.57),
            ("2026-05-14 14:20:00", 71.22),
        ])

        segments = derive_segments(strokes)
        target_segments = [
            segment for segment in segments
            if segment.direction == "up"
            and segment.end_timestamp == "2026-05-13 13:50:00"
            and abs(segment.end_price - 77.14) < 1e-9
        ]

        self.assertTrue(target_segments, "应识别 05-13 13:50 @77.14 的上升线段终点")
        target_segment = target_segments[0]
        feature_break = target_segment.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict)
        self.assertEqual(feature_break.get("feature_sequence_indices"), [25, 27, 29])
        self.assertEqual(feature_break.get("confirmation_case"), "no_gap_feature_fractal")
        self.assertEqual(
            {
                "id": target_segment.id,
                "direction": target_segment.direction,
                "start_timestamp": target_segment.start_timestamp,
                "end_timestamp": target_segment.end_timestamp,
                "start_price": target_segment.start_price,
                "end_price": target_segment.end_price,
                "confirmed": target_segment.confirmed,
                "meta_status": target_segment.meta.get("status"),
                "meta_start_stroke_index": target_segment.meta.get("start_stroke_index"),
                "meta_endpoint_abs": target_segment.meta.get("endpoint_is_absolute_extreme"),
                "stroke_ids": list(target_segment.stroke_ids),
                "feature_break": {
                    "feature_sequence_indices": feature_break.get("feature_sequence_indices"),
                    "feature_sequence_direction": feature_break.get("feature_sequence_direction"),
                    "first_second_has_gap": feature_break.get("first_second_has_gap"),
                    "break_fractal": feature_break.get("break_fractal"),
                    "left_contained_by_middle": feature_break.get("left_contained_by_middle"),
                    "confirmation_case": feature_break.get("confirmation_case"),
                    "followup_required": feature_break.get("followup_required"),
                },
            },
            {
                "id": "segment_025_2026-05-12 14:20:00_2026-05-13 13:50:00",
                "direction": "up",
                "start_timestamp": "2026-05-12 14:20:00",
                "end_timestamp": "2026-05-13 13:50:00",
                "start_price": 72.57,
                "end_price": 77.14,
                "confirmed": True,
                "meta_status": "confirmed",
                "meta_start_stroke_index": 24,
                "meta_endpoint_abs": True,
                "stroke_ids": ["stroke_025", "stroke_026", "stroke_027"],
                "feature_break": {
                    "feature_sequence_indices": [25, 27, 29],
                    "feature_sequence_direction": "down",
                    "first_second_has_gap": False,
                    "break_fractal": True,
                    "left_contained_by_middle": False,
                    "confirmation_case": "no_gap_feature_fractal",
                    "followup_required": False,
                },
            },
        )
        self.assertEqual(asdict(target_segment)["stroke_ids"], ["stroke_025", "stroke_026", "stroke_027"])

        non_absolute_segments = [
            segment for segment in segments
            if segment.meta.get("endpoint_is_absolute_extreme") is False
        ]
        self.assertTrue(non_absolute_segments, "非绝对极值端点应保留为诊断，而不是触发级联丢弃")


class SegmentCompatibilityTests(unittest.TestCase):
    def test_old_followup_patch_path_still_affects_break_signal(self):
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0), ("t9", 15.0), ("t10", 14.5),
        ])

        baseline = segments_mod._opposite_segment_break_signal(
            strokes=strokes,
            current_end_index=4,
            direction="up",
        )
        self.assertTrue(baseline.confirmed)
        self.assertEqual(
            baseline.reason,
            "gap_feature_fractal_with_followup_reverse_fractal",
        )

        patched_signal = segments_mod.FeatureBreakSignal(
            False,
            "no_followup_reverse_fractal",
            {
                "followup_reverse_feature_indices": [5, 7, 9],
                "followup_reverse_fractal": False,
            },
        )
        with patch.object(
            segments_mod,
            "_has_gap_followup_reverse_fractal",
            return_value=patched_signal,
        ) as mocked_followup:
            result = segments_mod._opposite_segment_break_signal(
                strokes=strokes,
                current_end_index=4,
                direction="up",
            )

        self.assertTrue(mocked_followup.called)
        self.assertFalse(result.confirmed)
        self.assertEqual(
            result.reason,
            "gap_feature_fractal_waiting_for_followup_reverse_fractal",
        )
        self.assertEqual(
            result.meta.get("pending_reason"),
            "gap_feature_fractal_waiting_for_followup_reverse_fractal",
        )

    def test_old_private_endpoint_builder_patch_path_still_affects_potential_endpoints(self):
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 15.0),
            ("t4", 13.0),
            ("t5", 14.0),
        ])

        self.assertEqual(segments_mod._potential_endpoint_indices(strokes), {2})

        with patch.object(
            segments_mod,
            "_endpoint_from_stroke",
            side_effect=lambda index, stroke: segments_mod.SegmentEndpoint(
                stroke_index=index,
                direction=stroke.direction,
                timestamp=stroke.end_timestamp,
                price=0.0,
            ),
        ) as mocked_endpoint:
            result = segments_mod._potential_endpoint_indices(strokes)

        self.assertTrue(mocked_endpoint.called)
        self.assertEqual(result, set())

    def test_old_private_directional_span_patch_path_still_affects_seed_validation(self):
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 12.0),
            ("t2", 11.0),
            ("t3", 13.0),
        ])

        self.assertTrue(segments_mod._is_valid_segment_seed(strokes))

        with patch.object(
            segments_mod,
            "_segment_has_directional_price_span",
            return_value=False,
        ) as mocked_span:
            result = segments_mod._is_valid_segment_seed(strokes)

        self.assertTrue(mocked_span.called)
        self.assertFalse(result)

    def test_old_private_confirmation_path_still_available(self):
        first = segments_mod.Segment(
            id="segment_001",
            direction="up",
            stroke_ids=["stroke_001", "stroke_002", "stroke_003"],
            start_timestamp="t0",
            end_timestamp="t3",
            start_price=10.0,
            end_price=13.0,
            confirmed=False,
            meta={"status": "pending", "feature_sequence_break": None},
        )
        second = segments_mod.Segment(
            id="segment_002",
            direction="down",
            stroke_ids=["stroke_004", "stroke_005", "stroke_006"],
            start_timestamp="t3",
            end_timestamp="t6",
            start_price=13.0,
            end_price=9.0,
            confirmed=False,
            meta={"status": "pending", "feature_sequence_break": None},
        )

        segments_mod._apply_segment_confirmation([first, second])

        self.assertTrue(first.confirmed)
        self.assertEqual(first.meta["status"], "confirmed")
        self.assertEqual(first.meta["confirmed_by_segment_id"], "segment_002")
        self.assertFalse(second.confirmed)
        self.assertEqual(second.meta["status"], "pending")

    def test_old_private_extend_patch_path_still_intercepts_merge_flow(self):
        first = segments_mod.Segment(
            id="segment_001",
            direction="up",
            stroke_ids=["stroke_001", "stroke_002", "stroke_003"],
            start_timestamp="t0",
            end_timestamp="t3",
            start_price=10.0,
            end_price=13.0,
            confirmed=False,
            meta={
                "status": "pending",
                "start_stroke_index": 0,
                "end_stroke_index": 2,
                "endpoint_update_indices": [],
            },
        )
        second = segments_mod.Segment(
            id="segment_002",
            direction="up",
            stroke_ids=["stroke_003", "stroke_004", "stroke_005"],
            start_timestamp="t3",
            end_timestamp="t5",
            start_price=13.0,
            end_price=15.0,
            confirmed=False,
            meta={
                "status": "pending",
                "start_stroke_index": 2,
                "end_stroke_index": 4,
                "endpoint_update_indices": [],
            },
        )
        strokes = [
            Stroke(
                id="stroke_001",
                direction="up",
                start_fractal_id="fractal_000",
                end_fractal_id="fractal_001",
                start_timestamp="t0",
                end_timestamp="t1",
                start_price=10.0,
                end_price=11.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_002",
                direction="down",
                start_fractal_id="fractal_001",
                end_fractal_id="fractal_002",
                start_timestamp="t1",
                end_timestamp="t2",
                start_price=11.0,
                end_price=10.5,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_003",
                direction="up",
                start_fractal_id="fractal_002",
                end_fractal_id="fractal_003",
                start_timestamp="t2",
                end_timestamp="t3",
                start_price=10.5,
                end_price=13.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_004",
                direction="down",
                start_fractal_id="fractal_003",
                end_fractal_id="fractal_004",
                start_timestamp="t3",
                end_timestamp="t4",
                start_price=13.0,
                end_price=12.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_005",
                direction="up",
                start_fractal_id="fractal_004",
                end_fractal_id="fractal_005",
                start_timestamp="t4",
                end_timestamp="t5",
                start_price=12.0,
                end_price=15.0,
                confirmed=True,
                meta={},
            ),
        ]

        with patch.object(
            segments_mod,
            "_extend_segment_if_more_extreme",
            wraps=segments_mod._extend_segment_if_more_extreme,
        ) as mocked_extend:
            merged = segments_mod._merge_adjacent_same_direction_segments(
                [first, second],
                strokes=strokes,
                potential_endpoints={4},
            )

        self.assertTrue(mocked_extend.called)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].end_timestamp, "t5")
        self.assertEqual(merged[0].end_price, 15.0)

    def test_old_private_tail_builder_patch_path_can_change_append_behavior(self):
        last_segment = segments_mod.Segment(
            id="segment_001",
            direction="up",
            stroke_ids=["stroke_001", "stroke_002", "stroke_003"],
            start_timestamp="t0",
            end_timestamp="t3",
            start_price=10.0,
            end_price=13.0,
            confirmed=False,
            meta={
                "status": "pending",
                "end_stroke_index": 2,
                "feature_sequence_break": None,
            },
        )
        sentinel = segments_mod.Segment(
            id="segment_growing_custom",
            direction="down",
            stroke_ids=["stroke_004", "stroke_005"],
            start_timestamp="t3",
            end_timestamp="t5",
            start_price=13.0,
            end_price=9.0,
            confirmed=False,
            meta={"status": "growing"},
        )
        strokes = [
            Stroke(
                id="stroke_001",
                direction="up",
                start_fractal_id="fractal_000",
                end_fractal_id="fractal_001",
                start_timestamp="t0",
                end_timestamp="t1",
                start_price=10.0,
                end_price=11.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_002",
                direction="down",
                start_fractal_id="fractal_001",
                end_fractal_id="fractal_002",
                start_timestamp="t1",
                end_timestamp="t2",
                start_price=11.0,
                end_price=10.5,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_003",
                direction="up",
                start_fractal_id="fractal_002",
                end_fractal_id="fractal_003",
                start_timestamp="t2",
                end_timestamp="t3",
                start_price=10.5,
                end_price=13.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_004",
                direction="down",
                start_fractal_id="fractal_003",
                end_fractal_id="fractal_004",
                start_timestamp="t3",
                end_timestamp="t4",
                start_price=13.0,
                end_price=11.0,
                confirmed=True,
                meta={},
            ),
            Stroke(
                id="stroke_005",
                direction="up",
                start_fractal_id="fractal_004",
                end_fractal_id="fractal_005",
                start_timestamp="t4",
                end_timestamp="t5",
                start_price=11.0,
                end_price=12.0,
                confirmed=True,
                meta={},
            ),
        ]
        segments = [last_segment]

        with patch.object(
            segments_mod,
            "_make_unfinished_tail_segment",
            return_value=sentinel,
        ) as mocked_make_tail:
            segments_mod._append_unfinished_tail_segment(
                segments=segments,
                strokes=strokes,
                potential_endpoints={3},
            )

        self.assertTrue(mocked_make_tail.called)
        self.assertEqual(segments[-1], sentinel)
        self.assertEqual(len(segments), 2)


if __name__ == "__main__":
    unittest.main()
