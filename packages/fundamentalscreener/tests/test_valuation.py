"""Phase 4: valuation 单元测试。

覆盖：
- 5 个 label：fair / low_need_quality_check / expensive / expensive_but_supported /
  not_applicable，及 label 优先级。
- score 阈值打分对 cohort 大小不敏感。
- 缺失字段产生 entry warnings，整命令不崩。
- 未知 code → 顶层 warnings。
- sort_valuations 各字段方向（pe/pb/peg/pe_pct 升序，score/dividend_yield 降序）。
- 不实现 DCF：模块没有 DCF 函数（结构化断言）。
"""

from __future__ import annotations

import unittest
from typing import List

from packages.fundamentalscreener.repositories import (
    BenchmarkData,
    DailyBar,
    MarketSnapshot,
    ValuationData,
)
from packages.fundamentalscreener.schema import ValuationEntry
from packages.fundamentalscreener.valuation import (
    compute_valuation,
    sort_valuations,
)


def _bench() -> BenchmarkData:
    return BenchmarkData(
        id="hs300",
        name="hs300",
        daily=[DailyBar(date="2026-01-01", close=100.0, turnover_amount=0.0)],
    )


def _make_snapshot(valuations: List[ValuationData]) -> MarketSnapshot:
    return MarketSnapshot(
        date="2026-06-19",
        classification_system="concept",
        benchmark=_bench(),
        sectors=[],
        companies=[],
        financials=[],
        valuations=valuations,
    )


class LabelRuleTests(unittest.TestCase):
    def _label(self, **kwargs) -> str:
        v = ValuationData(code="A", name="A", **kwargs)
        snap = _make_snapshot([v])
        return compute_valuation(snap, ["A"]).companies[0].label

    def _label_entry(self, **kwargs) -> ValuationEntry:
        v = ValuationData(code="A", name="A", **kwargs)
        snap = _make_snapshot([v])
        return compute_valuation(snap, ["A"]).companies[0]

    def test_fair_label(self) -> None:
        self.assertEqual(
            self._label(
                pe=20.0, pb=2.5, pe_percentile=0.50, pb_percentile=0.55, peg=1.0
            ),
            "fair",
        )

    def test_low_need_quality_check_label(self) -> None:
        self.assertEqual(
            self._label(
                pe=10.0, pb=1.2, pe_percentile=0.20, pb_percentile=0.30, peg=None
            ),
            "low_need_quality_check",
        )

    def test_expensive_label_when_peg_high(self) -> None:
        self.assertEqual(
            self._label(
                pe=60.0, pb=8.5, pe_percentile=0.90, pb_percentile=0.85, peg=2.5
            ),
            "expensive",
        )

    def test_expensive_label_when_peg_missing(self) -> None:
        # 高分位 + PEG 缺失 → expensive（不退化成 expensive_but_supported）。
        self.assertEqual(
            self._label(
                pe=60.0, pb=8.5, pe_percentile=0.90, pb_percentile=0.85, peg=None
            ),
            "expensive",
        )

    def test_expensive_but_supported_label(self) -> None:
        self.assertEqual(
            self._label(
                pe=35.0, pb=5.5, pe_percentile=0.85, pb_percentile=0.78, peg=1.2
            ),
            "expensive_but_supported",
        )

    def test_not_applicable_when_key_fields_missing(self) -> None:
        v = ValuationData(code="A", name="A")  # 全为 None
        snap = _make_snapshot([v])
        e = compute_valuation(snap, ["A"]).companies[0]
        self.assertEqual(e.label, "not_applicable")
        self.assertTrue(any("missing_field" in w or "not_applicable" in w for w in e.warnings))

    def test_not_applicable_when_pe_missing(self) -> None:
        # pe 缺失，其它三个关键字段齐全 → 仍应 not_applicable，避免
        # 数据不完整的公司被当作"合理估值"。
        e = self._label_entry(
            pe=None, pb=2.0, pe_percentile=0.5, pb_percentile=0.5, peg=1.0
        )
        self.assertEqual(e.label, "not_applicable")
        self.assertIn("missing_field: pe", e.warnings)

    def test_not_applicable_when_pe_percentile_missing(self) -> None:
        # 用户反馈的具体反例：pe=None, pe_percentile=None, pb_percentile=0.5
        # 之前会输出 label=fair；修复后必须 not_applicable。
        e = self._label_entry(
            pe=None, pb=2.0, pe_percentile=None, pb_percentile=0.5
        )
        self.assertEqual(e.label, "not_applicable")
        self.assertIn("missing_field: pe", e.warnings)
        self.assertIn("missing_field: pe_percentile", e.warnings)

    def test_not_applicable_when_pb_percentile_missing(self) -> None:
        e = self._label_entry(
            pe=20.0, pb=2.0, pe_percentile=0.5, pb_percentile=None
        )
        self.assertEqual(e.label, "not_applicable")
        self.assertIn("missing_field: pb_percentile", e.warnings)

    def test_priority_expensive_beats_low(self) -> None:
        # pe_pct=0.85（高）+ pb_pct=0.20（低）：同时命中 expensive 与
        # low_need_quality_check，按优先级应取 expensive（PEG 高于 1.5）。
        self.assertEqual(
            self._label(
                pe=40.0, pb=1.2, pe_percentile=0.85, pb_percentile=0.20, peg=2.0
            ),
            "expensive",
        )

    def test_priority_expensive_but_supported_beats_low(self) -> None:
        # 同上但 PEG=1.0：expensive_but_supported 优先于 low_need_quality_check。
        self.assertEqual(
            self._label(
                pe=40.0, pb=1.2, pe_percentile=0.85, pb_percentile=0.20, peg=1.0
            ),
            "expensive_but_supported",
        )

    def test_priority_expensive_but_supported_beats_expensive(self) -> None:
        # docs §16 优先级：expensive_but_supported 高于 expensive。
        # 实际规则下两者互斥（PEG 二选一），优先级仅作兜底裁决；
        # 这里直接构造同时含两种候选的列表，验证 _resolve_label 行为。
        from packages.fundamentalscreener.valuation import _resolve_label
        self.assertEqual(
            _resolve_label(["expensive", "expensive_but_supported"]),
            "expensive_but_supported",
        )


class ComputeValuationTests(unittest.TestCase):
    def test_score_independent_of_cohort_size(self) -> None:
        v = ValuationData(
            code="A",
            name="A",
            pe=20.0,
            pb=2.5,
            ps=3.0,
            peg=1.0,
            dividend_yield=0.02,
            pe_percentile=0.50,
            pb_percentile=0.55,
            industry_valuation_position="mid",
        )
        snap_solo = _make_snapshot([v])
        snap_pair = _make_snapshot(
            [v, ValuationData(code="B", name="B", pe=60.0, pb=8.0,
                              pe_percentile=0.95, pb_percentile=0.95)]
        )
        s_solo = compute_valuation(snap_solo, ["A"]).companies[0].score
        s_pair = next(
            e
            for e in compute_valuation(snap_pair, ["A", "B"]).companies
            if e.code == "A"
        ).score
        self.assertIsNotNone(s_solo)
        self.assertEqual(s_solo, s_pair)

    def test_low_pct_outranks_high_pct_in_score(self) -> None:
        low = ValuationData(
            code="L", name="L", pe=10.0, pb=1.0,
            pe_percentile=0.10, pb_percentile=0.15,
            peg=0.8, dividend_yield=0.04,
            industry_valuation_position="low",
        )
        high = ValuationData(
            code="H", name="H", pe=80.0, pb=10.0,
            pe_percentile=0.95, pb_percentile=0.92,
            peg=3.0, dividend_yield=0.001,
            industry_valuation_position="high",
        )
        snap = _make_snapshot([low, high])
        result = compute_valuation(snap, ["L", "H"])
        s_low = next(e for e in result.companies if e.code == "L").score
        s_high = next(e for e in result.companies if e.code == "H").score
        self.assertGreater(s_low, s_high)

    def test_unknown_code_top_level_warning(self) -> None:
        snap = _make_snapshot(
            [ValuationData(code="A", name="A", pe=15.0, pb=2.0,
                           pe_percentile=0.4, pb_percentile=0.5)]
        )
        result = compute_valuation(snap, ["A", "ZZZ"])
        self.assertEqual([e.code for e in result.companies], ["A"])
        self.assertTrue(any("code_not_found: ZZZ" in w for w in result.warnings))

    def test_no_codes_returns_warning(self) -> None:
        snap = _make_snapshot([])
        result = compute_valuation(snap, [])
        self.assertEqual(result.companies, [])
        self.assertIn("no_codes_provided", result.warnings)

    def test_does_not_implement_dcf(self) -> None:
        # 结构化断言：valuation 模块不应提供 DCF 入口（docs §16 DoD）。
        from packages.fundamentalscreener import valuation as mod

        self.assertFalse(
            hasattr(mod, "dcf") or hasattr(mod, "compute_dcf"),
            "valuation 模块不应实现 DCF（docs §16 第一版禁止）",
        )


class SortValuationsTests(unittest.TestCase):
    def _entry(self, code: str, **kwargs) -> ValuationEntry:
        return ValuationEntry(code=code, name=code, **kwargs)

    def test_sort_by_score_descending(self) -> None:
        e1 = self._entry("A", score=80.0)
        e2 = self._entry("B", score=40.0)
        e3 = self._entry("C", score=None)
        ordered = sort_valuations([e2, e3, e1], "score")
        self.assertEqual([e.code for e in ordered], ["A", "B", "C"])

    def test_sort_by_pe_ascending(self) -> None:
        e1 = self._entry("A", pe=50.0)
        e2 = self._entry("B", pe=10.0)
        e3 = self._entry("C", pe=None)
        ordered = sort_valuations([e1, e2, e3], "pe")
        self.assertEqual([e.code for e in ordered], ["B", "A", "C"])

    def test_sort_by_pe_percentile_ascending(self) -> None:
        e1 = self._entry("A", pe_percentile=0.9)
        e2 = self._entry("B", pe_percentile=0.3)
        ordered = sort_valuations([e1, e2], "pe_percentile")
        self.assertEqual([e.code for e in ordered], ["B", "A"])

    def test_sort_by_dividend_yield_descending(self) -> None:
        e1 = self._entry("A", dividend_yield=0.005)
        e2 = self._entry("B", dividend_yield=0.030)
        ordered = sort_valuations([e1, e2], "dividend_yield")
        self.assertEqual([e.code for e in ordered], ["B", "A"])

    def test_unknown_sort_field_preserves_order(self) -> None:
        e1 = self._entry("A", score=10.0)
        e2 = self._entry("B", score=90.0)
        ordered = sort_valuations([e1, e2], "not_a_field")
        self.assertEqual([e.code for e in ordered], ["A", "B"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
