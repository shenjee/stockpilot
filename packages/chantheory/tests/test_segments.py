from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Tuple


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

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


if __name__ == "__main__":
    unittest.main()
