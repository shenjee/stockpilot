"""Phase 3: financial_quality 单元测试。

覆盖：
- 分量分（profitability / growth / cashflow / leverage / efficiency）的方向。
- 总分按 §7.3 权重；缺失分量按可用权重重新归一。
- abnormal_flags 至少 5 个：weak_cashflow / receivable_growth_risk /
  inventory_growth_risk / high_debt / weak_core_profit；
  以及可选的 gross_margin_decline。
- 缺失字段写入 entry warnings，不写顶层错误。
- code 不存在时顶层 warnings 报 ``code_not_found``，已有 code 仍计算。
- compute_financial_quality 不依赖 cohort 大小（单 code 仍能给出 score）。
- sort_financials 在 score / debt_to_asset 下的方向正确，None 排末尾。
"""

from __future__ import annotations

import unittest
from typing import List

from packages.fundamentalscreener.financial_quality import (
    compute_financial_quality,
    sort_financials,
)
from packages.fundamentalscreener.repositories import (
    BenchmarkData,
    DailyBar,
    FinancialData,
    MarketSnapshot,
)
from packages.fundamentalscreener.schema import FinancialEntry


def _bench() -> BenchmarkData:
    return BenchmarkData(
        id="hs300",
        name="hs300",
        daily=[DailyBar(date="2026-01-01", close=100.0, turnover_amount=0.0)],
    )


def _make_snapshot(financials: List[FinancialData]) -> MarketSnapshot:
    return MarketSnapshot(
        date="2026-06-19",
        classification_system="concept",
        benchmark=_bench(),
        sectors=[],
        companies=[],
        financials=financials,
    )


class ComputeFinancialQualityTests(unittest.TestCase):
    def test_score_independent_of_cohort_size(self) -> None:
        # 同样的财务数据，单条查询和批量查询应得到同样的 score（阈值打分不依赖 cohort）。
        f = FinancialData(
            code="A",
            name="A",
            revenue_yoy=0.18,
            net_profit_yoy=0.25,
            deducted_net_profit_yoy=0.21,
            gross_margin=0.36,
            net_margin=0.12,
            roe=0.14,
            operating_cashflow_to_profit=1.2,
            free_cashflow=1_000_000.0,
            debt_to_asset=0.42,
            interest_bearing_debt_ratio=0.18,
            accounts_receivable_yoy=0.10,
            inventory_yoy=0.08,
        )
        snap_solo = _make_snapshot([f])
        snap_pair = _make_snapshot(
            [
                f,
                FinancialData(code="B", name="B", revenue_yoy=-0.05),
            ]
        )
        score_solo = compute_financial_quality(snap_solo, ["A"]).companies[0].score
        score_pair = next(
            e
            for e in compute_financial_quality(snap_pair, ["A", "B"]).companies
            if e.code == "A"
        ).score
        self.assertIsNotNone(score_solo)
        self.assertEqual(score_solo, score_pair)

    def test_strong_profile_outranks_weak_profile(self) -> None:
        strong = FinancialData(
            code="S",
            name="S",
            revenue_yoy=0.30,
            net_profit_yoy=0.40,
            deducted_net_profit_yoy=0.38,
            gross_margin=0.48,
            net_margin=0.18,
            roe=0.18,
            operating_cashflow_to_profit=1.4,
            free_cashflow=1_000_000.0,
            debt_to_asset=0.30,
            interest_bearing_debt_ratio=0.05,
            accounts_receivable_yoy=0.20,
            inventory_yoy=0.20,
        )
        weak = FinancialData(
            code="W",
            name="W",
            revenue_yoy=0.02,
            net_profit_yoy=-0.05,
            deducted_net_profit_yoy=-0.08,
            gross_margin=0.18,
            net_margin=0.02,
            roe=0.03,
            operating_cashflow_to_profit=0.30,
            free_cashflow=-500_000.0,
            debt_to_asset=0.65,
            interest_bearing_debt_ratio=0.30,
            accounts_receivable_yoy=0.30,
            inventory_yoy=0.40,
        )
        snap = _make_snapshot([strong, weak])
        result = compute_financial_quality(snap, ["S", "W"])
        s = next(e for e in result.companies if e.code == "S").score
        w = next(e for e in result.companies if e.code == "W").score
        self.assertGreater(s, w)
        self.assertGreaterEqual(s, 60.0)
        self.assertLessEqual(w, 40.0)

    def test_missing_component_renormalizes_weights(self) -> None:
        # 全部分量都缺：score 应为 None。
        empty = FinancialData(code="E", name="E")
        snap = _make_snapshot([empty])
        result = compute_financial_quality(snap, ["E"])
        self.assertIsNone(result.companies[0].score)
        # 应有 missing_field warnings。
        self.assertTrue(
            any("missing_field" in w for w in result.companies[0].warnings)
        )

    def test_unknown_code_is_warned_top_level(self) -> None:
        f = FinancialData(code="A", name="A", revenue_yoy=0.1)
        snap = _make_snapshot([f])
        result = compute_financial_quality(snap, ["A", "ZZZ"])
        codes = [e.code for e in result.companies]
        self.assertEqual(codes, ["A"])
        self.assertTrue(any("code_not_found: ZZZ" in w for w in result.warnings))

    def test_no_codes_returns_warning(self) -> None:
        snap = _make_snapshot([])
        result = compute_financial_quality(snap, [])
        self.assertEqual(result.companies, [])
        self.assertIn("no_codes_provided", result.warnings)


class AbnormalFlagsTests(unittest.TestCase):
    def _entry(self, **kwargs) -> FinancialEntry:
        f = FinancialData(code="A", name="A", **kwargs)
        snap = _make_snapshot([f])
        return compute_financial_quality(snap, ["A"]).companies[0]

    def test_weak_cashflow_flag(self) -> None:
        e = self._entry(net_profit_yoy=0.20, operating_cashflow_to_profit=0.30)
        self.assertIn("weak_cashflow", e.abnormal_flags)

    def test_weak_cashflow_not_triggered_when_profit_negative(self) -> None:
        e = self._entry(net_profit_yoy=-0.10, operating_cashflow_to_profit=0.30)
        self.assertNotIn("weak_cashflow", e.abnormal_flags)

    def test_receivable_growth_risk_flag(self) -> None:
        e = self._entry(revenue_yoy=0.05, accounts_receivable_yoy=0.40)
        self.assertIn("receivable_growth_risk", e.abnormal_flags)

    def test_inventory_growth_risk_flag(self) -> None:
        e = self._entry(revenue_yoy=0.10, inventory_yoy=0.40)
        self.assertIn("inventory_growth_risk", e.abnormal_flags)

    def test_high_debt_flag(self) -> None:
        e = self._entry(debt_to_asset=0.75)
        self.assertIn("high_debt", e.abnormal_flags)

    def test_high_debt_threshold_strict(self) -> None:
        # 阈值 0.7，等于 0.7 不触发（严格大于）。
        e = self._entry(debt_to_asset=0.70)
        self.assertNotIn("high_debt", e.abnormal_flags)

    def test_weak_core_profit_flag(self) -> None:
        e = self._entry(net_profit_yoy=0.50, deducted_net_profit_yoy=0.05)
        self.assertIn("weak_core_profit", e.abnormal_flags)

    def test_gross_margin_decline_only_when_field_provided(self) -> None:
        # 提供了 yoy 字段且 < 0 → 触发。
        e1 = self._entry(gross_margin=0.30, gross_margin_yoy_change=-0.05)
        self.assertIn("gross_margin_decline", e1.abnormal_flags)
        # 没提供 yoy 字段 → 不触发，也不报错。
        e2 = self._entry(gross_margin=0.30)
        self.assertNotIn("gross_margin_decline", e2.abnormal_flags)


class SortFinancialsTests(unittest.TestCase):
    def _entry(self, code: str, score: float | None = None, debt: float | None = None) -> FinancialEntry:
        return FinancialEntry(code=code, name=code, score=score, debt_to_asset=debt)

    def test_sort_by_score_descending_with_none_last(self) -> None:
        e1 = self._entry("A", score=80.0)
        e2 = self._entry("B", score=40.0)
        e3 = self._entry("C", score=None)
        ordered = sort_financials([e2, e3, e1], "score")
        self.assertEqual([e.code for e in ordered], ["A", "B", "C"])

    def test_sort_by_debt_to_asset_ascending(self) -> None:
        e1 = self._entry("A", debt=0.5)
        e2 = self._entry("B", debt=0.3)
        e3 = self._entry("C", debt=None)
        ordered = sort_financials([e1, e2, e3], "debt_to_asset")
        self.assertEqual([e.code for e in ordered], ["B", "A", "C"])

    def test_unknown_sort_field_preserves_order(self) -> None:
        e1 = self._entry("A", score=10.0)
        e2 = self._entry("B", score=90.0)
        ordered = sort_financials([e1, e2], "not_a_field")
        self.assertEqual([e.code for e in ordered], ["A", "B"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
