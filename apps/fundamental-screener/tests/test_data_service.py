"""Phase 6 data_service 测试：保证 Streamlit 通过 core 调用得到非空结果。

不直接启动 Streamlit；仅验证调用边界：
- ``load_snapshot`` 读取默认 fixture。
- ``build_sector_board`` 返回排好序的板块和 chart_series。
- ``build_sector_detail`` 返回公司排名 + 财务 / 估值 / flags 数据。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
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
    load_snapshot_from_db,
    sectors_to_rows,
    valuations_to_rows,
)
from fundamentalscreener.sqlite_schema import connect, init_db  # noqa: E402


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


def _populate_minimal_sqlite(conn, analysis_date: str = "2026-06-19") -> None:
    """填充最小 SQLite：2 板块 + 65 交易日 + 3 公司，足以通过质量检查生成快照。

    不含财务/估值（per-code 任务），quality status 会是 ``degraded`` 但不阻断。
    """

    days = []
    d = date.fromisoformat(analysis_date)
    while len(days) < 65:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    days = list(reversed(days))

    src, run, ts = "akshare_em", "fetch-test-001", "2026-06-19"

    for sid, name in [("BK0001", "半导体"), ("BK0002", "工程机械")]:
        conn.execute(
            "INSERT INTO sectors (sector_id, classification_system, sector_name, "
            "source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, 'em_industry', ?, ?, ?, ?, ?, ?)",
            (sid, name, src, run, ts, ts, ts),
        )
        for day in days:
            conn.execute(
                "INSERT INTO sector_daily_bars (sector_id, classification_system, "
                "trade_date, close, turnover_amount, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (?, 'em_industry', ?, 100.0, 1e9, ?, ?, ?, ?, ?)",
                (sid, day, src, run, ts, ts, ts),
            )

    for sid, code in [("BK0001", "002371"), ("BK0001", "600584"),
                      ("BK0002", "000001")]:
        conn.execute(
            "INSERT INTO sector_constituents (sector_id, classification_system, "
            "code, as_of_date, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) "
            "VALUES (?, 'em_industry', ?, ?, ?, ?, ?, ?, ?)",
            (sid, code, analysis_date, src, run, ts, ts, ts),
        )

    for day in days:
        conn.execute(
            "INSERT INTO benchmark_daily_bars (benchmark, trade_date, close, "
            "turnover_amount, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) "
            "VALUES ('hs300', ?, 3500.0, 1e11, ?, ?, ?, ?, ?)",
            (day, src, run, ts, ts, ts),
        )

    for code, name in [("002371", "北方华创"), ("600584", "长电科技"),
                       ("000001", "平安银行")]:
        conn.execute(
            "INSERT INTO stocks (code, name, market, listing_status, as_of_date, "
            "source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, 'SZ', 'L', ?, ?, ?, ?, ?, ?)",
            (code, name, analysis_date, src, run, ts, ts, ts),
        )
        for day in days:
            conn.execute(
                "INSERT INTO company_daily_snapshot (code, trade_date, close, "
                "turnover_amount, turnover_rate, market_cap, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (?, ?, 10.0, 1e8, 0.02, 1e10, ?, ?, ?, ?, ?)",
                (code, day, src, run, ts, ts, ts),
            )
    conn.commit()


class SqliteDataSourceTests(unittest.TestCase):
    """Phase 7: SQLite 数据源接入测试（不依赖真实网络）。"""

    def setUp(self) -> None:
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(self.db_fd)
        conn = connect(self.db_path)
        try:
            init_db(conn)
            _populate_minimal_sqlite(conn)
        finally:
            conn.close()

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_load_snapshot_from_db_returns_snapshot_and_metadata(self) -> None:
        result = load_snapshot_from_db(self.db_path, "2026-06-19")
        self.assertIsNone(
            result.quality_error, f"unexpected quality_error: {result.quality_error}"
        )
        self.assertIsNotNone(result.snapshot, "snapshot should not be None")
        self.assertTrue(result.snapshot.sectors, "sectors should not be empty")
        self.assertIsNotNone(result.metadata, "metadata should not be None")
        self.assertIsNotNone(
            result.quality_report, "quality_report should not be None"
        )
        # 血缘字段
        self.assertTrue(
            result.metadata.fetch_run_id, "fetch_run_id should be non-empty"
        )
        self.assertTrue(
            result.metadata.source_set.to_dict(),
            "source_set should be non-empty",
        )
        self.assertIn(
            result.metadata.data_quality_status,
            ("ok", "degraded", "stale"),
            f"expected non-invalid status, got {result.metadata.data_quality_status}",
        )

    def test_build_sector_board_propagates_lineage_from_sqlite(self) -> None:
        result = load_snapshot_from_db(self.db_path, "2026-06-19")
        board = build_sector_board(
            result.snapshot,
            metadata=result.metadata,
            quality_report=result.quality_report,
            top=5,
        )
        self.assertTrue(board.sectors, "sectors should not be empty")
        # Phase 7 DoD: 血缘字段透传到 board
        self.assertTrue(
            board.data_quality_status, "data_quality_status should be set"
        )
        self.assertTrue(board.source_set, "source_set should be propagated")
        self.assertTrue(board.fetch_run_id, "fetch_run_id should be propagated")
        # 质量问题应被透传（缺财务/估值会产生 warning issues）
        self.assertIsInstance(board.quality_issues, list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
