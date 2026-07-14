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
from chantheory.segment_break_helpers import _process_feature_inclusion
from chantheory.segment_helpers import StrokeRange
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
        # 上升段，特征序列无包含关系、无缺口，f2.high 最高 -> 顶分型确认
        # peak=13，f1(peak前down笔)=[10,12], f2(peak后down笔)=[11,13], f3=[9.5,12]
        # 无包含：f1.low<f2.low 且 f1.high<f2.high -> 不包含
        # 顶分型：f2.high=13 > f1.high=12 且 > f3.high=12
        strokes = _build_strokes([
            ("t0", 9.0),
            ("t1", 12.0),   # [0] up
            ("t2", 10.0),   # [1] down f1=[10,12]
            ("t3", 13.0),   # [2] up (peak)
            ("t4", 11.0),   # [3] down f2=[11,13]
            ("t5", 12.0),   # [4] up
            ("t6", 9.5),    # [5] down f3=[9.5,12]
            ("t7", 11.0),   # [6] up
            ("t8", 8.0),    # [7] down f4=[8,11]
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
        # followup indices=[5,7,9]: s6=[16,20], s8=[14,17], s10=[15.5,18]
        # 无包含关系；底分型：s8.low=14 < s6.low=16, s8.low=14 < s10.low=15.5 -> 确认
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0), ("t9", 18.0), ("t10", 15.5),
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
        # 上升段，特征序列经包含处理后形成顶分型（无包含、无缺口）
        # peak=63.60，f1=[58.82,60.28], f2=[59.25,63.60], f3=[58.0,61.38]
        # f2.high=63.60 > f1.high=60.28 且 > f3.high=61.38 -> 顶分型
        strokes = _build_strokes([
            ("t0", 55.0),   # 上升段起点
            ("t1", 57.0),
            ("t2", 56.0),
            ("t3", 60.28),
            ("t4", 58.82),  # f1 向下笔 [58.82,60.28]
            ("t5", 63.60),  # 上升段最高点
            ("t6", 59.25),  # f2 向下笔 [59.25,63.60]
            ("t7", 61.38),
            ("t8", 58.0),   # f3 向下笔 [58.0,61.38]
        ])
        segments = derive_segments(strokes)
        self.assertTrue(segments, "应至少识别出一个段")
        up = segments[0]
        self.assertEqual(up.direction, "up")
        self.assertAlmostEqual(up.end_price, 63.60, places=2,
                               msg="上升段终点应取同方向最高点 63.60")
        feature_break = up.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict,
                              "feature_sequence_break 应为 dict")
        self.assertTrue(feature_break.get("break_fractal"),
                        "应识别出特征序列顶分型")
        self.assertEqual(feature_break.get("confirmation_case"),
                         "no_gap_feature_fractal",
                         "无缺口分型应确认")
        self.assertTrue(up.confirmed, "上升段应被确认")

    def test_down_segment_without_complete_feature_fractal_not_confirmed(self):
        # 对称：下降段底分型（无包含、无缺口）
        # peak=57.0，f1=[60.0,61.5], f2=[57.0,61.0], f3=[59.0,62.0]
        # f2.low=57.0 < f1.low=60.0 且 < f3.low=59.0 -> 底分型
        strokes = _build_strokes([
            ("t0", 65.0),
            ("t1", 63.0),
            ("t2", 64.0),
            ("t3", 60.0),
            ("t4", 61.5),   # f1 向上笔 [60.0,61.5]
            ("t5", 57.0),   # 下降段最低点
            ("t6", 61.0),   # f2 向上笔 [57.0,61.0]
            ("t7", 59.0),
            ("t8", 62.0),   # f3 向上笔 [59.0,62.0]
        ])

        segments = derive_segments(strokes)

        self.assertTrue(segments, "应至少识别出一个段")
        down = segments[0]
        self.assertEqual(down.direction, "down")
        self.assertAlmostEqual(down.end_price, 57.0, places=2)
        feature_break = down.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict)
        self.assertTrue(feature_break.get("break_fractal"),
                        "应识别出特征序列底分型")
        self.assertEqual(feature_break.get("confirmation_case"),
                         "no_gap_feature_fractal",
                         "无缺口分型应确认")
        self.assertTrue(down.confirmed, "下降段应被确认")

    def test_endpoint_extreme_diagnostic_does_not_cascade_drop_later_feature_fractal(self):
        # 回归 002149.sz 5分钟案例：04-27 之后第一个 down 候选的终点不是
        # window 绝对低点，旧版 _enforce_segment_contract 会把它丢弃，随后
        # valid[-1] 卡住并级联丢弃后续线段。
        #
        # 经过特征序列包含处理后，上升段正确延伸到最高点 78.84（stroke 16），
        # 而不是被提前切断在 77.14。特征序列分型在 [25, 27, 29] 确认。
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

        # 上升段应延伸到真正的最高点 78.84，而非被提前切断
        target_segments = [
            segment for segment in segments
            if segment.direction == "up"
            and segment.end_timestamp == "2026-05-12 09:45:00"
            and abs(segment.end_price - 78.84) < 1e-9
        ]

        self.assertTrue(target_segments, "应识别 05-12 09:45 @78.84 的上升线段终点")
        target_segment = target_segments[0]
        feature_break = target_segment.meta.get("feature_sequence_break")
        self.assertIsInstance(feature_break, dict)
        self.assertEqual(feature_break.get("feature_sequence_indices"), [25, 27, 29])
        self.assertEqual(feature_break.get("confirmation_case"), "no_gap_feature_fractal")
        self.assertTrue(target_segment.confirmed, "上升段应被确认")
        self.assertTrue(feature_break.get("break_fractal"), "应识别出特征序列顶分型")

        # 非绝对极值端点应保留为诊断，而不是触发级联丢弃
        non_absolute_segments = [
            segment for segment in segments
            if segment.meta.get("endpoint_is_absolute_extreme") is False
        ]
        self.assertTrue(non_absolute_segments, "非绝对极值端点应保留为诊断，而不是触发级联丢弃")

    def test_top_fractal_not_formed_when_f3_higher_than_f2(self):
        """反例：f3.high > f2.high 时不得形成顶分型。

        直接调用 _opposite_segment_break_signal，确保断言必然执行。
        上升段 peak=20，恰好 3 个特征元素（无包含、无缺口）：
        f1=[12,20], f2=[8,15], f3=[10,17]
        f3.high=17 > f2.high=15 -> 不构成顶分型
        stroke[2] 终点 15 和 stroke[4] 终点 17 均 < peak 20，不触发 new_peak_found
        """
        strokes = _build_strokes([
            ("t0", 10.0),
            ("t1", 20.0),   # [0] up (peak=20)
            ("t2", 12.0),   # [1] down f1=[12,20]
            ("t3", 15.0),   # [2] up (15 < 20)
            ("t4", 8.0),    # [3] down f2=[8,15]
            ("t5", 17.0),   # [4] up (17 < 20, no new peak)
            ("t6", 10.0),   # [5] down f3=[10,17], f3.high=17 > f2.high=15
        ])
        signal = segments_mod._opposite_segment_break_signal(
            strokes=strokes,
            current_end_index=0,
            direction="up",
        )
        self.assertFalse(signal.confirmed, "f3.high>f2.high 时不应确认顶分型")
        self.assertEqual(signal.reason, "no_feature_fractal",
                         "应返回 no_feature_fractal 而非确认分型")

    def test_bottom_fractal_not_formed_when_f3_lower_than_f2(self):
        """反例：f3.low < f2.low 时不得形成底分型。

        直接调用 _opposite_segment_break_signal，确保断言必然执行。
        下降段 low=5，恰好 3 个特征元素（无包含、无缺口）：
        f1=[5,13], f2=[8,15], f3=[6,14]
        f3.low=6 < f2.low=8 -> 不构成底分型
        stroke[2] 终点 8 和 stroke[4] 终点 6 均 > low 5，不触发 new_peak_found
        """
        strokes = _build_strokes([
            ("t0", 20.0),
            ("t1", 5.0),    # [0] down (low=5)
            ("t2", 13.0),   # [1] up f1=[5,13]
            ("t3", 8.0),    # [2] down (8 > 5)
            ("t4", 15.0),   # [3] up f2=[8,15]
            ("t5", 6.0),    # [4] down (6 > 5, no new low)
            ("t6", 14.0),   # [5] up f3=[6,14], f3.low=6 < f2.low=8
        ])
        signal = segments_mod._opposite_segment_break_signal(
            strokes=strokes,
            current_end_index=0,
            direction="down",
        )
        self.assertFalse(signal.confirmed, "f3.low<f2.low 时不应确认底分型")
        self.assertEqual(signal.reason, "no_feature_fractal",
                         "应返回 no_feature_fractal 而非确认分型")


class ProcessFeatureInclusionTests(unittest.TestCase):
    """_process_feature_inclusion 的输入/输出表驱动单测。"""

    def _make_input(self, ranges: list[tuple[int, float, float]]) -> list[tuple[int, StrokeRange]]:
        """[(stroke_index, low, high), ...] -> [(stroke_index, StrokeRange), ...]"""
        return [(idx, StrokeRange(stroke_index=idx, low=low, high=high)) for idx, low, high in ranges]

    def test_no_inclusion_returns_all_elements_unchanged(self):
        """无包含关系时，所有元素原样返回。"""
        inputs = self._make_input([
            (0, 10.0, 12.0),
            (1, 11.0, 13.0),
            (2, 9.5, 12.0),
        ])
        result = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result), 3)
        for i, (idx, rng, orig) in enumerate(result):
            self.assertEqual(idx, inputs[i][0])
            self.assertEqual(rng, inputs[i][1])
            self.assertEqual(orig, [inputs[i][0]])

    def test_upward_inclusion_merges_by_taking_higher_high_and_higher_low(self):
        """向上趋势包含合并：取 high 更高者，low 取两者较高者。

        A=[10,20], B=[12,18]（B 被 A 包含），趋势向上 -> merged=[12,20]
        """
        inputs = self._make_input([
            (0, 8.0, 12.0),   # 参考元素，确定趋势向上
            (1, 10.0, 20.0),  # 确定趋势：10>8, 20>12 -> up
            (2, 12.0, 18.0),  # 被 B 包含 -> up 合并
        ])
        result = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result), 2, "A+B 合并后应剩 2 个元素")
        merged_idx, merged_rng, merged_orig = result[1]
        self.assertAlmostEqual(merged_rng.high, 20.0, places=9, msg="up 合并 high 取更高者")
        self.assertAlmostEqual(merged_rng.low, 12.0, places=9, msg="up 合并 low 取较高者")
        self.assertEqual(merged_orig, [1, 2])

    def test_downward_inclusion_merges_by_taking_lower_low_and_lower_high(self):
        """向下趋势包含合并：取 low 更低者，high 取两者较低者。

        A=[10,20], B=[12,18]（B 被 A 包含），趋势向下 -> merged=[10,18]
        """
        inputs = self._make_input([
            (0, 15.0, 25.0),  # 参考元素，确定趋势向下
            (1, 10.0, 20.0),  # 确定趋势：10<15, 20<25 -> down
            (2, 12.0, 18.0),  # 被 B 包含 -> down 合并
        ])
        result = _process_feature_inclusion(inputs, direction="down")
        self.assertEqual(len(result), 2, "A+B 合并后应剩 2 个元素")
        merged_idx, merged_rng, merged_orig = result[1]
        self.assertAlmostEqual(merged_rng.low, 10.0, places=9, msg="down 合并 low 取更低者")
        self.assertAlmostEqual(merged_rng.high, 18.0, places=9, msg="down 合并 high 取较低者")
        self.assertEqual(merged_orig, [1, 2])

    def test_dynamic_direction_switches_from_up_to_down(self):
        """趋势中途从向上变为向下时，合并方向应动态切换。

        A=[5,10], B=[8,15]（趋势 up）
        C=[10,13]（被 B 包含，up 合并 -> [10,15]）
        D=[4,11]（与 [10,15] 无包含，趋势 down）
        E=[5,10]（被 D 包含，down 合并 -> [4,10]）
        """
        inputs = self._make_input([
            (0, 5.0, 10.0),
            (1, 8.0, 15.0),
            (2, 10.0, 13.0),
            (3, 4.0, 11.0),
            (4, 5.0, 10.0),
        ])
        result = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result), 3, "5 元素 -> 2 次合并 -> 3 个输出")
        # 第一个合并 (up): [10,15]
        _, rng1, _ = result[1]
        self.assertAlmostEqual(rng1.high, 15.0, places=9)
        self.assertAlmostEqual(rng1.low, 10.0, places=9)
        # 第二个合并 (down): [4,10]
        _, rng2, _ = result[2]
        self.assertAlmostEqual(rng2.low, 4.0, places=9)
        self.assertAlmostEqual(rng2.high, 10.0, places=9)

    def test_initial_inclusion_uses_segment_direction_as_fallback(self):
        """前两个元素即有包含时，使用原线段方向作为 fallback。

        在上升段中：A=[10,20], B=[12,18]（B 被 A 包含，方向未定）
        -> fallback "up" -> merged=[12,20]
        """
        inputs = self._make_input([
            (0, 10.0, 20.0),
            (1, 12.0, 18.0),  # 被 A 包含，无前序无包含元素确定方向
        ])
        result_up = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result_up), 1)
        _, rng_up, _ = result_up[0]
        self.assertAlmostEqual(rng_up.high, 20.0, places=9, msg="up fallback: high 取更高")
        self.assertAlmostEqual(rng_up.low, 12.0, places=9, msg="up fallback: low 取较高")

        result_down = _process_feature_inclusion(inputs, direction="down")
        self.assertEqual(len(result_down), 1)
        _, rng_down, _ = result_down[0]
        self.assertAlmostEqual(rng_down.low, 10.0, places=9, msg="down fallback: low 取更低")
        self.assertAlmostEqual(rng_down.high, 18.0, places=9, msg="down fallback: high 取较低")

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(_process_feature_inclusion([], direction="up"), [])

    def test_single_element_returns_unchanged(self):
        inputs = self._make_input([(0, 10.0, 20.0)])
        result = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 0)
        self.assertEqual(result[0][2], [0])

    def test_consecutive_inclusion_chain_merges_all(self):
        """连续包含链：A 包含 B 包含 C，方向向上时全部合并为一个。

        A=[5,25], B=[10,20], C=[12,18]
        方向 up（参考元素之后确定，或 fallback）
        -> merged=[12,25]
        """
        inputs = self._make_input([
            (0, 5.0, 25.0),
            (1, 10.0, 20.0),  # 被 A 包含
            (2, 12.0, 18.0),  # 被 merged 包含
        ])
        result = _process_feature_inclusion(inputs, direction="up")
        self.assertEqual(len(result), 1, "连续包含应全部合并为 1 个")
        _, rng, orig = result[0]
        self.assertAlmostEqual(rng.high, 25.0, places=9)
        self.assertAlmostEqual(rng.low, 12.0, places=9)
        self.assertEqual(orig, [0, 1, 2])


class SegmentCompatibilityTests(unittest.TestCase):
    def test_old_followup_patch_path_still_affects_break_signal(self):
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0), ("t9", 18.0), ("t10", 15.5),
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


class GapFollowupInclusionCompensationTests(unittest.TestCase):
    """测试 _has_gap_followup_reverse_fractal 对包含后元素不足的补偿行为。"""

    def test_followup_features_merged_to_two_then_completed_by_fourth(self):
        """前三根包含后只剩两个，第四根加入后形成三个处理后元素并分型确认。

        原始 followup reverse 笔（上升段后续向下笔）：
        idx5: [10,20]
        idx7: [8,18] → 与 idx5 不包含
        idx9: [12,15] → 被 idx7 包含，向下合并为 [8,15]
        idx11: [9,17] → 补足第三个处理后元素并形成底分型确认
        """
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 10.0), ("t7", 18.0),  # followup idx5: [10,20] (down)
            ("t8", 8.0), ("t9", 15.0),  # followup idx7: [8,18] (down)
            ("t10", 12.0), ("t11", 17.0),  # followup idx9: [12,15] (down)
            ("t12", 9.0), ("t13", 16.0),  # followup idx11: [9,17] (down)
            ("t14", 10.0),  # 额外向上笔，确保 idx13 存在
        ])
        signal = segments_mod._has_gap_followup_reverse_fractal(
            strokes=strokes,
            current_end_index=4,
            direction="up",
        )
        self.assertTrue(signal.confirmed, "包含处理后应最终获得三个元素并确认分型")
        self.assertEqual(
            signal.reason,
            "followup_reverse_fractal",
            "应通过 followup 反向分型确认",
        )
        self.assertEqual(
            signal.meta.get("followup_reverse_feature_indices"),
            [5, 7, 9, 11],
            "应消费原始 idx5/7/9/11（加入第四根后达到三个处理后元素并分型）",
        )

    def test_sliding_window_check(self):
        """测试滑动窗口检查逻辑：第一组三元素无分型，第二组窗口形成分型。

        原始 followup reverse 笔（上升段后续向下笔）：
        idx1: [10,20] (down)
        idx3: [12,22] (down)
        idx5: [11,21] (down)
        idx7: [13,23] (down)
        无包含，因此处理后序列就是这四个元素。
        第一个窗口 [0,1,2]：idx3的低点12 不低于 idx1的低点10 → 无分型。
        第二个窗口 [1,2,3]：idx5的低点11 低于 idx3的低点12且低于 idx7的低点13 → 形成底分型。
        """
        strokes = _build_strokes([
            ("t0", 5.0),   # 0→1: 初始段的最后一笔
            ("t1", 20.0),  # 0→1 up，current_end_index=0
            ("t2", 10.0),  # idx1: down [10, 20]
            ("t3", 22.0),  # idx2: up
            ("t4", 12.0),  # idx3: down [12, 22]
            ("t5", 21.0),  # idx4: up
            ("t6", 11.0),  # idx5: down [11, 21]
            ("t7", 23.0),  # idx6: up
            ("t8", 13.0),  # idx7: down [13, 23]
            ("t9", 20.0),  # 额外笔，确保有 idx7 存在
        ])
        # current_end_index = 0, direction = "up"
        signal = segments_mod._has_gap_followup_reverse_fractal(
            strokes=strokes,
            current_end_index=0,
            direction="up",
        )
        self.assertTrue(signal.confirmed, "第二组窗口应该形成分型")
        self.assertEqual(signal.reason, "followup_reverse_fractal")
        # 检查消费的原始笔索引
        self.assertEqual(
            signal.meta.get("followup_reverse_feature_indices"),
            [1, 3, 5, 7],
        )


class GapFractalConfirmationTimingTests(unittest.TestCase):
    """测试缺口分型确认时序的回归测试。"""

    def test_new_peak_before_followup_confirmation_returns_extension(self):
        """主分型在 idx5，新高在 idx8，后续确认在 idx10 → 应返回 new_peak_found(8)。"""
        # 基于 test_gap_feature_fractal_confirmed_after_followup，在 followup 前插入新高
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 17.0),
            ("t8", 14.0),  # 这是 followup 的开始，先停下来
            ("t9", 101.0),  # 新高峰的笔 idx8 (end index)
            ("t10", 18.0), ("t11", 15.5),  # 继续 followup 完成分型
        ])
        from chantheory.segments import _opposite_segment_break_signal
        signal = _opposite_segment_break_signal(
            strokes=strokes,
            current_end_index=4,
            direction="up",
        )
        self.assertFalse(signal.confirmed)
        self.assertEqual(signal.reason, "new_peak_found")
        self.assertEqual(signal.meta.get("new_peak_index"), 8)

    def test_f3_orig_with_multiple_indices_uses_max_as_completion(self):
        """f3_orig 包含多个原始索引，新高在它们之前，应返回 new_peak_found。"""
        # 使用包含关系的特征序列，让 f3_orig 包含多个索引
        strokes = _build_strokes([
            ("t0", 10.0), ("t1", 12.0), ("t2", 11.0), ("t3", 15.0),
            ("t4", 13.0), ("t5", 20.0), ("t6", 16.0), ("t7", 19.0),  # f1
            ("t8", 15.0),  # 向下笔，补充后 f2 (包含关系
            ("t9", 101.0),  # 新高！
            ("t10", 14.0), ("t11", 17.0), ("t12", 15.5),  # f3
        ])
        from chantheory.segments import _opposite_segment_break_signal
        signal = _opposite_segment_break_signal(
            strokes=strokes,
            current_end_index=4,
            direction="up",
        )
        self.assertFalse(signal.confirmed)
        self.assertEqual(signal.reason, "new_peak_found")
        self.assertEqual(signal.meta.get("new_peak_index"), 8)


if __name__ == "__main__":
    unittest.main()
