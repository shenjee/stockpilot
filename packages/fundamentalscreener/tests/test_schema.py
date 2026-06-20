"""Phase 0: schema 序列化与字段稳定性测试。"""

from __future__ import annotations

import unittest

from packages.fundamentalscreener.schema import (
    CandidatesPayload,
    ChartSeries,
    ChartSeriesPoint,
    CompaniesPayload,
    CompanyEntry,
    FinancialEntry,
    FinancialsPayload,
    ScreenPayload,
    SectorEntry,
    SectorsPayload,
    ValuationEntry,
    ValuationsPayload,
)


class SectorsPayloadTests(unittest.TestCase):
    def test_to_dict_contains_required_top_level_keys(self) -> None:
        payload = SectorsPayload(
            command="sectors",
            date="2026-06-19",
            classification_system="concept",
            benchmark="hs300",
            sort="return_1d",
            periods=[1, 5, 20, 60],
        )
        d = payload.to_dict()
        for key in (
            "command",
            "date",
            "classification_system",
            "benchmark",
            "sort",
            "periods",
            "sectors",
            "chart_series",
            "warnings",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["command"], "sectors")
        self.assertEqual(d["periods"], [1, 5, 20, 60])
        self.assertEqual(d["sectors"], [])
        self.assertEqual(d["chart_series"], [])
        self.assertEqual(d["warnings"], [])

    def test_sector_entry_preserves_optional_nones(self) -> None:
        entry = SectorEntry(
            sector_id="semiconductor",
            sector_name="半导体",
            classification_system="concept",
        )
        d = entry.to_dict()
        # Phase 0 计算字段允许为 None。
        for key in (
            "return_1d",
            "return_5d",
            "return_20d",
            "return_60d",
            "relative_return",
            "turnover_amount_change",
            "market_turnover_share",
            "rising_stock_ratio",
            "rank_change_5d",
            "state",
            "score",
        ):
            self.assertIn(key, d)
            self.assertIsNone(d[key])
        self.assertEqual(d["warnings"], [])

    def test_chart_series_serialization(self) -> None:
        series = ChartSeries(
            series_id="semiconductor",
            series_name="半导体",
            type="sector",
            points=[ChartSeriesPoint(date="2026-06-19", value=115.2)],
        )
        d = series.to_dict()
        self.assertEqual(d["type"], "sector")
        self.assertEqual(d["points"], [{"date": "2026-06-19", "value": 115.2}])


class CompaniesPayloadTests(unittest.TestCase):
    def test_companies_payload_top_level_keys(self) -> None:
        payload = CompaniesPayload(
            command="companies",
            date="2026-06-19",
            classification_system="concept",
            sector_id="semiconductor",
            sector_name="半导体",
            sort="combined_score",
        )
        d = payload.to_dict()
        for key in (
            "command",
            "date",
            "classification_system",
            "sector_id",
            "sector_name",
            "sort",
            "companies",
            "warnings",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["companies"], [])

    def test_company_entry_optional_scores(self) -> None:
        entry = CompanyEntry(code="002371", name="示例公司")
        d = entry.to_dict()
        self.assertIsNone(d["financial_quality_score"])
        self.assertIsNone(d["valuation_score"])
        self.assertEqual(d["flags"], [])
        self.assertEqual(d["warnings"], [])


class FinancialsValuationsTests(unittest.TestCase):
    def test_financials_payload_has_no_classification_system(self) -> None:
        payload = FinancialsPayload(command="financials", date="2026-06-19")
        d = payload.to_dict()
        self.assertNotIn("classification_system", d)
        for key in ("command", "date", "companies", "warnings"):
            self.assertIn(key, d)

    def test_valuations_payload_has_no_classification_system(self) -> None:
        payload = ValuationsPayload(command="valuations", date="2026-06-19")
        d = payload.to_dict()
        self.assertNotIn("classification_system", d)
        for key in ("command", "date", "companies", "warnings"):
            self.assertIn(key, d)

    def test_financial_entry_default_lists(self) -> None:
        entry = FinancialEntry(code="002371", name="示例公司")
        d = entry.to_dict()
        self.assertEqual(d["abnormal_flags"], [])
        self.assertEqual(d["warnings"], [])

    def test_valuation_entry_default_label(self) -> None:
        entry = ValuationEntry(code="002371", name="示例公司")
        d = entry.to_dict()
        self.assertIsNone(d["label"])
        self.assertIsNone(d["industry_valuation_position"])


class ScreenPayloadTests(unittest.TestCase):
    def test_screen_payload_includes_classification_and_candidates(self) -> None:
        payload = ScreenPayload(
            command="screen",
            date="2026-06-19",
            classification_system="concept",
            benchmark="hs300",
            generated_at="2026-06-20T15:00:00+08:00",
        )
        d = payload.to_dict()
        for key in (
            "command",
            "date",
            "classification_system",
            "benchmark",
            "selected_sectors",
            "candidates",
            "warnings",
            "generated_at",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["candidates"], {"priority": [], "watch": [], "cautious": []})

    def test_candidates_payload_default_groups(self) -> None:
        c = CandidatesPayload()
        d = c.to_dict()
        self.assertEqual(set(d.keys()), {"priority", "watch", "cautious"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
