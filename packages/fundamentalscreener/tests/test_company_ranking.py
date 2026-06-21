"""Phase 2: company_ranking 单元测试。

覆盖：
- leader_score 由 market_cap 在板块内 min-max 归一化驱动。
- attention_score 由 turnover_amount + turnover_rate 归一化平均，且任一缺失退化。
- combined_score 按 docs §14 升级公式
  (leader 0.20 / attention 0.20 / financial 0.35 / valuation 0.25)
  加权；fin/val 缺失时按可用权重重新归一，无 fin/val 数据时退化为 leader+attention。
- group 阈值映射：priority / watch / cautious / None。
- sector_return_rank 按 1 日涨跌幅降序。
- compute_company_ranking 在 sector_id 不存在 / 板块无公司时的降级。
- fin/val 流进 companies 命令，且 fin 或 val 缺失某家公司时不污染顶层 warnings。
- sort_companies 在各字段下的方向以及 None 兜底。
"""

from __future__ import annotations

import unittest
from typing import List, Sequence

from packages.fundamentalscreener.company_ranking import (
    compute_company_ranking,
    sort_companies,
)
from packages.fundamentalscreener.repositories import (
    BenchmarkData,
    CompanyData,
    DailyBar,
    MarketSnapshot,
    SectorData,
)


def _bars(
    closes: Sequence[float],
    turnover: float = 1_000_000_000.0,
    turnover_rate: float = 0.02,
) -> List[DailyBar]:
    return [
        DailyBar(
            date=f"2026-01-{i + 1:02d}",
            close=float(c),
            turnover_amount=turnover,
            turnover_rate=turnover_rate,
        )
        for i, c in enumerate(closes)
    ]


def _make_snapshot(
    sector_id: str,
    companies: Sequence[CompanyData],
    benchmark_closes: Sequence[float] = (100.0, 100.5),
) -> MarketSnapshot:
    sector = SectorData(
        sector_id=sector_id,
        sector_name=sector_id,
        constituents=[c.code for c in companies],
        daily=_bars(list(benchmark_closes)),
    )
    return MarketSnapshot(
        date="2026-06-19",
        classification_system="concept",
        benchmark=BenchmarkData(
            id="hs300", name="沪深300", daily=_bars(list(benchmark_closes))
        ),
        sectors=[sector],
        companies=list(companies),
    )


class ComputeCompanyRankingTests(unittest.TestCase):
    def test_leader_score_reflects_market_cap_normalization(self) -> None:
        c_big = CompanyData("BIG", "big", "S", 1_000.0, _bars([10.0, 10.1]))
        c_mid = CompanyData("MID", "mid", "S", 500.0, _bars([10.0, 10.05]))
        c_small = CompanyData("SML", "small", "S", 100.0, _bars([10.0, 10.02]))
        snap = _make_snapshot("S", [c_big, c_mid, c_small])
        result = compute_company_ranking(snap, "S")
        scores = {e.code: e.leader_score for e in result.companies}
        # min-max 归一化：最大 = 100，最小 = 0。
        self.assertEqual(scores["BIG"], 100.0)
        self.assertEqual(scores["SML"], 0.0)
        # 中间值在 0~100 之间。
        self.assertGreater(scores["MID"], 0.0)
        self.assertLess(scores["MID"], 100.0)

    def test_attention_score_combines_turnover_amount_and_rate(self) -> None:
        # 公司 A：成交额最大、换手率最大 → attention = 100。
        # 公司 B：成交额最小、换手率最小 → attention = 0。
        c_a = CompanyData(
            "A",
            "a",
            "S",
            500.0,
            _bars([10.0, 10.1], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        c_b = CompanyData(
            "B",
            "b",
            "S",
            500.0,
            _bars([10.0, 10.05], turnover=500_000_000.0, turnover_rate=0.01),
        )
        snap = _make_snapshot("S", [c_a, c_b])
        result = compute_company_ranking(snap, "S")
        scores = {e.code: e.attention_score for e in result.companies}
        self.assertEqual(scores["A"], 100.0)
        self.assertEqual(scores["B"], 0.0)

    def test_combined_score_uses_phase2_weights(self) -> None:
        # fixture 不带 financials/valuations，新公式下 fin/val 都是 None，
        # combined_score 按可用权重重新归一退化为 leader*0.5 + attention*0.5。
        # A: leader=100, attention=0   → combined = 50
        # B: leader=0,   attention=100 → combined = 50
        c_a = CompanyData(
            "A",
            "a",
            "S",
            1_000.0,
            _bars([10.0, 10.1], turnover=500_000_000.0, turnover_rate=0.01),
        )
        c_b = CompanyData(
            "B",
            "b",
            "S",
            100.0,
            _bars([10.0, 10.05], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        snap = _make_snapshot("S", [c_a, c_b])
        result = compute_company_ranking(snap, "S")
        by_code = {e.code: e for e in result.companies}
        self.assertAlmostEqual(by_code["A"].combined_score, 50.0, places=2)
        self.assertAlmostEqual(by_code["B"].combined_score, 50.0, places=2)
        # fin/val 都缺失时退化为 leader+attention，分量字段仍应为 None。
        self.assertIsNone(by_code["A"].financial_quality_score)
        self.assertIsNone(by_code["A"].valuation_score)
        # group 阈值：priority>=70, watch>=50, 否则 cautious。
        # 双 50 → watch。
        self.assertEqual(by_code["A"].group, "watch")
        self.assertEqual(by_code["B"].group, "watch")

    def test_group_priority_threshold(self) -> None:
        # 三家公司：A 双高（→ priority），B 中等（→ watch），C 双低（→ cautious）。
        c_a = CompanyData(
            "A",
            "a",
            "S",
            1_000.0,
            _bars([10.0, 10.1], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        c_b = CompanyData(
            "B",
            "b",
            "S",
            500.0,
            _bars([10.0, 10.05], turnover=1_000_000_000.0, turnover_rate=0.025),
        )
        c_c = CompanyData(
            "C",
            "c",
            "S",
            100.0,
            _bars([10.0, 10.02], turnover=500_000_000.0, turnover_rate=0.01),
        )
        snap = _make_snapshot("S", [c_a, c_b, c_c])
        result = compute_company_ranking(snap, "S")
        by_code = {e.code: e for e in result.companies}
        self.assertEqual(by_code["A"].group, "priority")
        self.assertEqual(by_code["C"].group, "cautious")

    def test_sector_return_rank_descending_by_return_1d(self) -> None:
        c_strong = CompanyData("S1", "s1", "S", 500.0, _bars([10.0, 11.0]))
        c_weak = CompanyData("W1", "w1", "S", 500.0, _bars([10.0, 9.5]))
        snap = _make_snapshot("S", [c_weak, c_strong])
        result = compute_company_ranking(snap, "S")
        by_code = {e.code: e for e in result.companies}
        self.assertEqual(by_code["S1"].sector_return_rank, 1)
        self.assertEqual(by_code["W1"].sector_return_rank, 2)

    def test_financial_and_valuation_scores_are_null_when_data_missing(self) -> None:
        # snapshot.financials/valuations 都为空，对应公司无法计算分数 → None。
        c = CompanyData("A", "a", "S", 500.0, _bars([10.0, 10.1]))
        snap = _make_snapshot("S", [c])
        result = compute_company_ranking(snap, "S")
        self.assertIsNone(result.companies[0].financial_quality_score)
        self.assertIsNone(result.companies[0].valuation_score)

    def test_combined_score_uses_upgraded_weights_when_all_components_present(self) -> None:
        # 构造 fin/val score 通过 fixture 流进。
        from packages.fundamentalscreener.repositories import (
            FinancialData,
            ValuationData,
        )

        c = CompanyData(
            "A", "a", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=1_000_000_000.0, turnover_rate=0.02),
        )
        snap = _make_snapshot("S", [c])
        # 注入财务/估值数据，确保 fin/val 评分能算出非 None 值。
        snap.financials.append(
            FinancialData(
                code="A",
                revenue_yoy=0.2, net_profit_yoy=0.25, deducted_net_profit_yoy=0.22,
                gross_margin=0.35, net_margin=0.12, roe=0.15,
                operating_cashflow_to_profit=1.2, free_cashflow=1.0,
                debt_to_asset=0.4, interest_bearing_debt_ratio=0.15,
                accounts_receivable_yoy=0.10, inventory_yoy=0.08,
            )
        )
        snap.valuations.append(
            ValuationData(
                code="A",
                pe=20.0, pb=2.5, peg=1.0, dividend_yield=0.02,
                pe_percentile=0.50, pb_percentile=0.55,
                industry_valuation_position="mid",
            )
        )
        result = compute_company_ranking(snap, "S")
        e = result.companies[0]
        # 4 个分量都应被填上。
        self.assertIsNotNone(e.leader_score)
        self.assertIsNotNone(e.attention_score)
        self.assertIsNotNone(e.financial_quality_score)
        self.assertIsNotNone(e.valuation_score)
        # 公式：0.20*leader + 0.20*att + 0.35*fin + 0.25*val。
        expected = (
            0.20 * e.leader_score
            + 0.20 * e.attention_score
            + 0.35 * e.financial_quality_score
            + 0.25 * e.valuation_score
        )
        self.assertAlmostEqual(e.combined_score, round(expected, 2), places=2)

    def test_combined_score_renormalizes_when_valuation_missing(self) -> None:
        # 只缺 valuation；其它 3 个分量都齐 → 按剩余权重重新归一。
        from packages.fundamentalscreener.repositories import FinancialData

        c = CompanyData(
            "A", "a", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=1_000_000_000.0, turnover_rate=0.02),
        )
        snap = _make_snapshot("S", [c])
        snap.financials.append(
            FinancialData(
                code="A",
                revenue_yoy=0.2, net_profit_yoy=0.25, deducted_net_profit_yoy=0.22,
                gross_margin=0.35, net_margin=0.12, roe=0.15,
                operating_cashflow_to_profit=1.2, free_cashflow=1.0,
                debt_to_asset=0.4, interest_bearing_debt_ratio=0.15,
                accounts_receivable_yoy=0.10, inventory_yoy=0.08,
            )
        )
        # 不给 valuations。
        result = compute_company_ranking(snap, "S")
        e = result.companies[0]
        self.assertIsNone(e.valuation_score)
        self.assertIsNotNone(e.financial_quality_score)
        # leader 0.20 + attention 0.20 + financial 0.35 = 0.75 总权重，
        # 按可用权重重新归一。
        denom = 0.20 + 0.20 + 0.35
        expected = (
            0.20 * e.leader_score
            + 0.20 * e.attention_score
            + 0.35 * e.financial_quality_score
        ) / denom
        self.assertAlmostEqual(e.combined_score, round(expected, 2), places=2)

    def test_missing_fin_val_does_not_pollute_top_warnings(self) -> None:
        # 没有 financials/valuations 时，code_not_found 不应冒到 ranking 顶层。
        c = CompanyData("A", "a", "S", 500.0, _bars([10.0, 10.1]))
        snap = _make_snapshot("S", [c])
        result = compute_company_ranking(snap, "S")
        for w in result.warnings:
            self.assertNotIn("code_not_found", w)

    def test_missing_fin_val_emits_entry_warnings(self) -> None:
        # 没有 financials/valuations 时，每家公司应在 entry warnings 上挂摘要
        # 标记，提示调用方 combined_score 是降级算出来的。
        c = CompanyData("A", "a", "S", 500.0, _bars([10.0, 10.1]))
        snap = _make_snapshot("S", [c])
        result = compute_company_ranking(snap, "S")
        e = result.companies[0]
        self.assertIn("missing_financial_quality_score", e.warnings)
        self.assertIn("missing_valuation_score", e.warnings)

    def test_valuation_not_applicable_is_excluded_from_combined(self) -> None:
        # 估值关键字段缺失时，compute_valuation 给出 label=not_applicable，
        # 但仍可能有一个兜底 score（来自其它分量）。companies 视图必须把
        # 这种 score 视为不可信：valuation_score=None、不进 combined_score，
        # 并写入 valuation_not_applicable warning。
        from packages.fundamentalscreener.repositories import (
            FinancialData,
            ValuationData,
        )

        c = CompanyData(
            "A", "a", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=1_000_000_000.0, turnover_rate=0.02),
        )
        snap = _make_snapshot("S", [c])
        snap.financials.append(
            FinancialData(
                code="A",
                revenue_yoy=0.2, net_profit_yoy=0.25, deducted_net_profit_yoy=0.22,
                gross_margin=0.35, net_margin=0.12, roe=0.15,
                operating_cashflow_to_profit=1.2, free_cashflow=1.0,
                debt_to_asset=0.4, interest_bearing_debt_ratio=0.15,
                accounts_receivable_yoy=0.10, inventory_yoy=0.08,
            )
        )
        # 故意只填 industry_valuation_position + dividend_yield，关键字段
        # pe/pb/pe_percentile/pb_percentile 都缺 → label=not_applicable，
        # 但 score 仍能从 industry/dividend 算出来。
        snap.valuations.append(
            ValuationData(
                code="A",
                pe=None, pb=None, pe_percentile=None, pb_percentile=None,
                industry_valuation_position="mid",
                dividend_yield=0.02,
            )
        )
        result = compute_company_ranking(snap, "S")
        e = result.companies[0]
        self.assertIsNone(e.valuation_score)
        self.assertIn("valuation_not_applicable", e.warnings)
        # combined_score 应按 leader/attention/financial 三分量重新归一，
        # 不能再带上 valuation 的兜底分。
        denom = 0.20 + 0.20 + 0.35
        expected = (
            0.20 * e.leader_score
            + 0.20 * e.attention_score
            + 0.35 * e.financial_quality_score
        ) / denom
        self.assertAlmostEqual(e.combined_score, round(expected, 2), places=2)

    def test_missing_market_cap_emits_warning_and_none_leader(self) -> None:
        c_a = CompanyData(
            "A", "a", "S", None, _bars([10.0, 10.1])
        )
        c_b = CompanyData(
            "B", "b", "S", 500.0, _bars([10.0, 10.05])
        )
        snap = _make_snapshot("S", [c_a, c_b])
        result = compute_company_ranking(snap, "S")
        by_code = {e.code: e for e in result.companies}
        self.assertIsNone(by_code["A"].leader_score)
        self.assertIn("market_cap_unavailable", by_code["A"].warnings)

    def test_unknown_sector_returns_warning(self) -> None:
        snap = _make_snapshot("S", [CompanyData("A", "a", "S", 1.0, _bars([10.0, 10.1]))])
        result = compute_company_ranking(snap, "T")
        self.assertEqual(result.companies, [])
        self.assertTrue(any("sector_not_found" in w for w in result.warnings))

    def test_sector_with_no_companies_returns_warning(self) -> None:
        snap = _make_snapshot("S", [])
        result = compute_company_ranking(snap, "S")
        self.assertEqual(result.companies, [])
        self.assertIn("no_companies_in_sector", result.warnings)


class SortCompaniesTests(unittest.TestCase):
    def test_sort_by_combined_score_descending(self) -> None:
        # 通过手工构造 entries 来直接覆盖 sort_companies。
        from packages.fundamentalscreener.schema import CompanyEntry

        e1 = CompanyEntry(code="A", name="a", combined_score=80.0)
        e2 = CompanyEntry(code="B", name="b", combined_score=40.0)
        e3 = CompanyEntry(code="C", name="c", combined_score=None)
        ordered = sort_companies([e2, e3, e1], "combined_score")
        self.assertEqual([e.code for e in ordered], ["A", "B", "C"])

    def test_sort_by_sector_return_rank_ascending(self) -> None:
        from packages.fundamentalscreener.schema import CompanyEntry

        e1 = CompanyEntry(code="A", name="a", sector_return_rank=3)
        e2 = CompanyEntry(code="B", name="b", sector_return_rank=1)
        e3 = CompanyEntry(code="C", name="c", sector_return_rank=None)
        ordered = sort_companies([e1, e2, e3], "sector_return_rank")
        self.assertEqual([e.code for e in ordered], ["B", "A", "C"])

    def test_unknown_sort_field_preserves_order(self) -> None:
        from packages.fundamentalscreener.schema import CompanyEntry

        e1 = CompanyEntry(code="A", name="a", combined_score=10.0)
        e2 = CompanyEntry(code="B", name="b", combined_score=90.0)
        ordered = sort_companies([e1, e2], "not_a_field")
        self.assertEqual([e.code for e in ordered], ["A", "B"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
