"""Phase 5: screening 编排单元测试。

覆盖：
- 板块 Top N 截断 + 排序方向（默认按 return_1d 降序）。
- 板块内 Top N 截断（按 combined_score 降序）。
- 候选公司带回 sector_id / sector_name 上下文。
- 硬约束：估值 ``label=not_applicable`` 必须把候选降到 ``cautious``。
- 空数据时不抛错，仅写 warnings。
"""

from __future__ import annotations

import unittest
from typing import List, Sequence

from packages.fundamentalscreener.repositories import (
    BenchmarkData,
    CompanyData,
    DailyBar,
    FinancialData,
    MarketSnapshot,
    SectorData,
    ValuationData,
)
from packages.fundamentalscreener.screening import run_screening


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


def _good_financial(code: str) -> FinancialData:
    return FinancialData(
        code=code,
        revenue_yoy=0.20,
        net_profit_yoy=0.25,
        deducted_net_profit_yoy=0.22,
        gross_margin=0.35,
        net_margin=0.12,
        roe=0.15,
        operating_cashflow_to_profit=1.2,
        free_cashflow=1.0,
        debt_to_asset=0.40,
        interest_bearing_debt_ratio=0.15,
        accounts_receivable_yoy=0.10,
        inventory_yoy=0.08,
    )


def _good_valuation(code: str) -> ValuationData:
    return ValuationData(
        code=code,
        pe=20.0,
        pb=2.5,
        peg=1.0,
        dividend_yield=0.02,
        pe_percentile=0.50,
        pb_percentile=0.55,
        industry_valuation_position="mid",
    )


def _not_applicable_valuation(code: str) -> ValuationData:
    # 关键字段全缺，但 industry/dividend 还在 → label=not_applicable，
    # 但 score 仍能从兜底分量算出。
    return ValuationData(
        code=code,
        pe=None,
        pb=None,
        pe_percentile=None,
        pb_percentile=None,
        industry_valuation_position="mid",
        dividend_yield=0.02,
    )


class ScreeningSelectionTests(unittest.TestCase):
    def test_sector_top_truncation_applied(self) -> None:
        # 3 个板块，sector_top=2 应只保留 Top 2。
        sectors = [
            SectorData(
                sector_id=sid,
                sector_name=sid,
                constituents=[f"{sid}_A"],
                daily=_bars(closes),
            )
            for sid, closes in [
                ("S1", [10.0, 11.0]),
                ("S2", [10.0, 10.5]),
                ("S3", [10.0, 9.5]),
            ]
        ]
        companies = [
            CompanyData(f"{sid}_A", "x", sid, 500.0, _bars([10.0, 10.1]))
            for sid in ("S1", "S2", "S3")
        ]
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=sectors,
            companies=companies,
        )
        result = run_screening(snap, sector_top=2, company_top=5)
        # 默认按 return_1d 降序，S1 涨幅最高、S3 最低，应剔除 S3。
        ids = [s.sector_id for s in result.selected_sectors]
        self.assertEqual(ids, ["S1", "S2"])

    def test_company_top_truncation_per_sector(self) -> None:
        # 单板块 3 家公司，company_top=2 应只保留 Top 2 candidates。
        companies = [
            CompanyData("C1", "c1", "S", 1000.0, _bars([10.0, 10.1])),
            CompanyData("C2", "c2", "S", 500.0, _bars([10.0, 10.05])),
            CompanyData("C3", "c3", "S", 100.0, _bars([10.0, 10.02])),
        ]
        sector = SectorData(
            sector_id="S",
            sector_name="S",
            constituents=[c.code for c in companies],
            daily=_bars([10.0, 10.5]),
        )
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[sector],
            companies=companies,
        )
        result = run_screening(snap, sector_top=10, company_top=2)
        total = sum(len(v) for v in result.candidates.values())
        self.assertEqual(total, 2)

    def test_candidate_keeps_sector_context(self) -> None:
        companies = [CompanyData("C1", "c1", "S", 1000.0, _bars([10.0, 10.1]))]
        sector = SectorData(
            sector_id="S",
            sector_name="板块S",
            constituents=["C1"],
            daily=_bars([10.0, 10.5]),
        )
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[sector],
            companies=companies,
        )
        result = run_screening(snap)
        flat = (
            result.candidates["priority"]
            + result.candidates["watch"]
            + result.candidates["cautious"]
        )
        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["sector_id"], "S")
        self.assertEqual(flat[0]["sector_name"], "板块S")
        # CompanyEntry 关键字段透传。
        self.assertEqual(flat[0]["code"], "C1")
        self.assertIn("combined_score", flat[0])


class ScreeningHardConstraintTests(unittest.TestCase):
    """docs §17 Supplement：valuation not_applicable 必须降级到 cautious。"""

    def _build_two_company_snapshot(
        self, second_valuation: ValuationData
    ) -> MarketSnapshot:
        # 两家公司：A 数据齐全（fin+val 齐），B 估值关键字段缺失。
        c_a = CompanyData(
            "A", "a", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        c_b = CompanyData(
            "B", "b", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        sector = SectorData(
            sector_id="S",
            sector_name="S",
            constituents=["A", "B"],
            daily=_bars([10.0, 10.5]),
        )
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[sector],
            companies=[c_a, c_b],
            financials=[_good_financial("A"), _good_financial("B")],
            valuations=[_good_valuation("A"), second_valuation],
        )
        return snap

    def test_not_applicable_company_drops_to_cautious(self) -> None:
        snap = self._build_two_company_snapshot(_not_applicable_valuation("B"))
        result = run_screening(snap)
        priority_codes = {c["code"] for c in result.candidates["priority"]}
        watch_codes = {c["code"] for c in result.candidates["watch"]}
        cautious_codes = {c["code"] for c in result.candidates["cautious"]}
        # 即便 B 的 combined_score 仍可能算出来，硬约束要求 B 进 cautious。
        self.assertIn("B", cautious_codes)
        self.assertNotIn("B", priority_codes)
        self.assertNotIn("B", watch_codes)
        # candidate 自身 group 字段必须与所在桶一致，否则 JSON 消费方会
        # 在桶名和字段之间反复横跳。
        b_in_cautious = next(
            c for c in result.candidates["cautious"] if c["code"] == "B"
        )
        self.assertEqual(b_in_cautious["group"], "cautious")

    def test_full_valuation_keeps_company_in_priority_or_watch(self) -> None:
        # 控制组：B 估值也齐全，B 应留在 priority/watch（视分数而定）。
        snap = self._build_two_company_snapshot(_good_valuation("B"))
        result = run_screening(snap)
        cautious_codes = {c["code"] for c in result.candidates["cautious"]}
        self.assertNotIn("B", cautious_codes)


class ScreeningWarningsTests(unittest.TestCase):
    def test_empty_sectors_produces_no_sectors_selected_warning(self) -> None:
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[],
            companies=[],
        )
        result = run_screening(snap)
        self.assertEqual(result.selected_sectors, [])
        self.assertTrue(any("no_sectors_selected" in w for w in result.warnings))
        self.assertEqual(result.candidates["priority"], [])
        self.assertEqual(result.candidates["watch"], [])
        self.assertEqual(result.candidates["cautious"], [])

    def test_sector_with_no_companies_records_top_level_warning(self) -> None:
        sector = SectorData(
            sector_id="S",
            sector_name="S",
            constituents=[],
            daily=_bars([10.0, 10.5]),
        )
        snap = MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[sector],
            companies=[],
        )
        result = run_screening(snap)
        joined = " ".join(result.warnings)
        self.assertIn("sector=S", joined)
        self.assertIn("no_companies_in_sector", joined)


def _weak_financial(code: str) -> FinancialData:
    """触发 weak_cashflow / receivable_growth_risk / high_debt 三个异常。"""
    return FinancialData(
        code=code,
        revenue_yoy=0.05,
        net_profit_yoy=0.20,
        deducted_net_profit_yoy=0.18,
        gross_margin=0.28,
        net_margin=0.05,
        roe=0.07,
        operating_cashflow_to_profit=0.30,  # weak_cashflow
        free_cashflow=-1.0,
        debt_to_asset=0.75,  # high_debt
        interest_bearing_debt_ratio=0.25,
        accounts_receivable_yoy=0.40,  # receivable_growth_risk (>revenue+0.2)
        inventory_yoy=0.10,
    )


class ScreeningTraceabilityTests(unittest.TestCase):
    """docs §17 DoD：所有分数可追溯。

    candidate 必须暴露：
    - ``flags``: 由 financial_quality.abnormal_flags 透传过来。
    - ``financial`` 子对象：完整 FinancialEntry.to_dict()，含原始指标 + score
      + abnormal_flags + warnings。
    - ``valuation`` 子对象：完整 ValuationEntry.to_dict()，含 pe/pb/percentile
      + label + score + warnings。
    """

    def _build_snapshot(self, fin: FinancialData, val: ValuationData) -> MarketSnapshot:
        c = CompanyData(
            "A", "a", "S", 1_000.0,
            _bars([10.0, 10.1], turnover=2_000_000_000.0, turnover_rate=0.05),
        )
        sector = SectorData(
            sector_id="S",
            sector_name="S",
            constituents=["A"],
            daily=_bars([10.0, 10.5]),
        )
        return MarketSnapshot(
            date="2026-06-19",
            classification_system="concept",
            benchmark=BenchmarkData("hs300", "沪深300", _bars([100.0, 100.5])),
            sectors=[sector],
            companies=[c],
            financials=[fin],
            valuations=[val],
        )

    def test_candidate_flags_carry_abnormal_flags(self) -> None:
        snap = self._build_snapshot(_weak_financial("A"), _good_valuation("A"))
        result = run_screening(snap)
        flat = (
            result.candidates["priority"]
            + result.candidates["watch"]
            + result.candidates["cautious"]
        )
        self.assertEqual(len(flat), 1)
        flags = set(flat[0]["flags"])
        self.assertIn("weak_cashflow", flags)
        self.assertIn("receivable_growth_risk", flags)
        self.assertIn("high_debt", flags)

    def test_candidate_includes_financial_subobject(self) -> None:
        snap = self._build_snapshot(_weak_financial("A"), _good_valuation("A"))
        result = run_screening(snap)
        flat = (
            result.candidates["priority"]
            + result.candidates["watch"]
            + result.candidates["cautious"]
        )
        fin = flat[0]["financial"]
        # 原始财务指标必须可直接读到；分数和 flags 与 FinancialEntry 对齐。
        self.assertEqual(fin["code"], "A")
        self.assertEqual(fin["accounts_receivable_yoy"], 0.40)
        self.assertEqual(fin["debt_to_asset"], 0.75)
        self.assertIsNotNone(fin["score"])
        self.assertIn("weak_cashflow", fin["abnormal_flags"])

    def test_candidate_includes_valuation_subobject(self) -> None:
        snap = self._build_snapshot(_weak_financial("A"), _good_valuation("A"))
        result = run_screening(snap)
        flat = (
            result.candidates["priority"]
            + result.candidates["watch"]
            + result.candidates["cautious"]
        )
        val = flat[0]["valuation"]
        self.assertEqual(val["code"], "A")
        self.assertEqual(val["pe"], 20.0)
        self.assertEqual(val["pb"], 2.5)
        self.assertEqual(val["label"], "fair")
        self.assertIsNotNone(val["score"])

    def test_not_applicable_valuation_label_visible_in_subobject(self) -> None:
        # 估值关键字段缺失时，candidate.valuation.label 必须为 not_applicable，
        # 让 UI / skill 能解释"为什么进 cautious"。
        snap = self._build_snapshot(_good_financial("A"), _not_applicable_valuation("A"))
        result = run_screening(snap)
        flat = result.candidates["cautious"]
        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["valuation"]["label"], "not_applicable")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
