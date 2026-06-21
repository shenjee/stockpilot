"""Phase 6 data_service 测试：保证 Streamlit 通过 core 调用得到非空结果。

不直接启动 Streamlit；仅验证调用边界：
- ``load_snapshot`` 读取默认 fixture。
- ``build_sector_board`` 返回排好序的板块和 chart_series。
- ``build_sector_detail`` 返回公司排名 + 财务 / 估值 / flags 数据。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(APP_DIR))

from services.data_service import (  # noqa: E402
    build_sector_board,
    build_sector_detail,
    collect_company_flags,
    companies_to_rows,
    financials_to_rows,
    load_snapshot,
    sectors_to_rows,
    valuations_to_rows,
)


FIXTURE = (
    ROOT
    / "packages"
    / "fundamentalscreener"
    / "tests"
    / "fixtures"
    / "minimal_market.json"
)


class DataServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = load_snapshot(FIXTURE)

    def test_load_snapshot_returns_fixture_contents(self):
        self.assertTrue(self.snapshot.date)
        self.assertTrue(self.snapshot.sectors)
        self.assertTrue(self.snapshot.benchmark.id)

    def test_build_sector_board_returns_sectors_and_chart_series(self):
        board = build_sector_board(self.snapshot, top=5)
        self.assertTrue(board.sectors, "sectors should not be empty")
        self.assertEqual(board.date, self.snapshot.date)
        self.assertEqual(board.benchmark_id, self.snapshot.benchmark.id)
        # chart_series 应包含基准 + 至少一个板块
        types = {s["type"] for s in board.chart_series}
        self.assertIn("benchmark", types)
        self.assertIn("sector", types)
        rows = sectors_to_rows(board.sectors)
        self.assertEqual(len(rows), len(board.sectors))

    def test_build_sector_board_respects_top(self):
        full = build_sector_board(self.snapshot)
        if len(full.sectors) <= 1:
            self.skipTest("fixture only exposes one sector")
        limited = build_sector_board(self.snapshot, top=1)
        self.assertEqual(len(limited.sectors), 1)
        # chart_series 必须跟 sectors 表保持一致：仅入选板块 + 基准。
        kept_sector_ids = {
            s["series_id"] for s in limited.chart_series if s["type"] == "sector"
        }
        self.assertEqual(kept_sector_ids, {limited.sectors[0].sector_id})
        self.assertTrue(
            any(s["type"] == "benchmark" for s in limited.chart_series),
            "benchmark series should always be retained",
        )

    def test_build_sector_detail_returns_companies(self):
        board = build_sector_board(self.snapshot)
        first_sector_id = board.sectors[0].sector_id
        detail = build_sector_detail(self.snapshot, first_sector_id, top=5)
        self.assertEqual(detail.sector_id, first_sector_id)
        self.assertTrue(detail.companies, "expected at least one company")
        company_rows = companies_to_rows(detail.companies)
        self.assertEqual(len(company_rows), len(detail.companies))
        # 财务 / 估值 / flags 表格能被 UI 直接消费（即便为空也不应抛错）
        financials_to_rows(detail.financials)
        valuations_to_rows(detail.valuations)
        flag_rows = collect_company_flags(
            detail.companies, detail.financials, detail.valuations
        )
        self.assertEqual(len(flag_rows), len(detail.companies))
        for row in flag_rows:
            self.assertIn("code", row)
            self.assertIn("company_flags", row)
            self.assertIn("valuation_label", row)

    def test_build_sector_detail_unknown_sector_reports_warning(self):
        detail = build_sector_detail(self.snapshot, "__not_a_real_sector__")
        self.assertEqual(detail.companies, [])
        self.assertTrue(
            any("sector_not_found" in w for w in detail.warnings),
            f"missing sector_not_found warning: {detail.warnings}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
