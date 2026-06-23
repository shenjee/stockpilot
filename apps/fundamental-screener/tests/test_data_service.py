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
    load_latest_snapshot,
    load_or_refresh_snapshot,
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

    src, run, ts = "akshare_ths", "fetch-test-001", "2026-06-19"

    for sid, name in [("BK0001", "半导体"), ("BK0002", "工程机械")]:
        conn.execute(
            "INSERT INTO sectors (sector_id, classification_system, sector_name, "
            "source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, 'ths_industry', ?, ?, ?, ?, ?, ?)",
            (sid, name, src, run, ts, ts, ts),
        )
        for day in days:
            conn.execute(
                "INSERT INTO sector_daily_bars (sector_id, classification_system, "
                "trade_date, close, turnover_amount, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (?, 'ths_industry', ?, 100.0, 1e9, ?, ?, ?, ?, ?)",
                (sid, day, src, run, ts, ts, ts),
            )

    for sid, code in [("BK0001", "002371"), ("BK0001", "600584"),
                      ("BK0002", "000001")]:
        conn.execute(
            "INSERT INTO sector_constituents (sector_id, classification_system, "
            "code, as_of_date, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) "
            "VALUES (?, 'ths_industry', ?, ?, ?, ?, ?, ?, ?)",
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


# ---------------------------------------------------------------------------
# 产品级入口测试（前端计划 Step 2/5）
# ---------------------------------------------------------------------------


class _FakeRefreshSource:
    """最小 fake 数据源，用于测试 refresh_market_data。

    实现 sector 层 4 个方法（返回 2 板块 + 65 交易日 + hs300），
    公司层返回空列表（Phase 6C 以 0 行成功）。

    ``empty=True`` 模式：所有方法返回空列表且不抛异常，模拟 THS 空返回
    （failure_count==0 但 required tasks 写入 0 行）。
    """

    def __init__(self, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.name = "akshare_ths"

    def _gen_days(self, n: int, end_iso: str = "2026-06-19"):
        days = []
        d = date.fromisoformat(end_iso)
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d -= timedelta(days=1)
        return list(reversed(days))

    def list_sectors(self, classification_system: str):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        return [
            {"sector_id": "BK0001", "sector_name": "半导体",
             "classification_system": classification_system, "source_updated_at": "2026-06-19"},
            {"sector_id": "BK0002", "sector_name": "工程机械",
             "classification_system": classification_system, "source_updated_at": "2026-06-19"},
        ]

    def get_sector_constituents(self, sector_id, classification_system, as_of_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        return [
            {"sector_id": sector_id, "classification_system": classification_system,
             "code": "002371", "as_of_date": as_of_date, "source_updated_at": as_of_date},
            {"sector_id": sector_id, "classification_system": classification_system,
             "code": "600584", "as_of_date": as_of_date, "source_updated_at": as_of_date},
        ]

    def get_sector_daily(self, sector_id, classification_system, start_date, end_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        days = self._gen_days(65, end_date)
        return [
            {"sector_id": sector_id, "classification_system": classification_system,
             "trade_date": d, "close": 100.0 + i, "turnover_amount": 1e9,
             "source_updated_at": d}
            for i, d in enumerate(days)
        ]

    def get_benchmark_daily(self, benchmark, start_date, end_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        days = self._gen_days(65, end_date)
        return [
            {"benchmark": benchmark, "trade_date": d, "close": 3500.0 + i,
             "turnover_amount": 1e11, "source_updated_at": d}
            for i, d in enumerate(days)
        ]

    def get_stock_universe(self, as_of_date):
        if self.fail or self.empty:
            return []
        return [
            {"code": "002371", "name": "北方华创", "market": "SZ",
             "listing_status": "L", "as_of_date": as_of_date,
             "source_updated_at": as_of_date},
            {"code": "600584", "name": "长电科技", "market": "SH",
             "listing_status": "L", "as_of_date": as_of_date,
             "source_updated_at": as_of_date},
        ]

    def get_company_daily_snapshot(self, trade_date):
        if self.fail or self.empty:
            return []
        return [
            {"code": "002371", "trade_date": trade_date, "close": 10.0,
             "turnover_amount": 1e8, "turnover_rate": 0.02, "market_cap": 1e10,
             "source_updated_at": trade_date},
            {"code": "600584", "trade_date": trade_date, "close": 20.0,
             "turnover_amount": 2e8, "turnover_rate": 0.01, "market_cap": 2e10,
             "source_updated_at": trade_date},
        ]

    def get_company_valuation_history(self, codes, start_date, end_date):
        return []

    def get_financial_metrics(self, codes, as_of_date):
        return []


class LoadLatestSnapshotTests(unittest.TestCase):
    """load_latest_snapshot 产品级入口测试。"""

    def test_no_db_returns_no_cache(self) -> None:
        """数据库文件不存在时返回 status=no_cache，不抛异常。"""
        result = load_latest_snapshot(db_path="/tmp/__nonexistent_fs_test.sqlite")
        self.assertEqual(result.status, "no_cache")
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.message)

    def test_empty_db_returns_no_cache(self) -> None:
        """数据库存在但表为空时返回 no_cache。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
            finally:
                conn.close()
            result = load_latest_snapshot(db_path=db_path)
            self.assertEqual(result.status, "no_cache")
            self.assertIsNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_populated_db_returns_snapshot(self) -> None:
        """有缓存时返回 snapshot + status=ok/degraded。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                _populate_minimal_sqlite(conn)
            finally:
                conn.close()
            result = load_latest_snapshot(db_path=db_path)
            self.assertIn(result.status, ("ok", "degraded", "stale"))
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.snapshot.sectors)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_explicit_date_no_db_returns_no_cache(self) -> None:
        """显式传入 analysis_date 但 DB 不存在时返回 no_cache，不是 invalid。"""
        result = load_latest_snapshot(
            db_path="/tmp/__nonexistent_fs_test.sqlite",
            analysis_date="2026-06-19",
        )
        self.assertEqual(result.status, "no_cache")
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.message)

    def test_explicit_date_empty_db_returns_no_cache(self) -> None:
        """显式传入 analysis_date 但 DB 为空时返回 no_cache，不是 invalid。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
            finally:
                conn.close()
            result = load_latest_snapshot(
                db_path=db_path, analysis_date="2026-06-19"
            )
            self.assertEqual(result.status, "no_cache")
            self.assertIsNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_explicit_date_not_in_cache_returns_no_cache(self) -> None:
        """显式传入的日期早于所有缓存数据时返回 no_cache。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                _populate_minimal_sqlite(conn, analysis_date="2026-06-19")
            finally:
                conn.close()
            # 缓存最早约 2026-03，请求 2026-01-01（trade_date <= 早于所有缓存）
            result = load_latest_snapshot(
                db_path=db_path, analysis_date="2026-01-01"
            )
            self.assertEqual(result.status, "no_cache")
            self.assertIsNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_explicit_date_after_latest_returns_snapshot(self) -> None:
        """显式传入晚于最新缓存交易日的日期时，repository 仍能组装快照（point-in-time）。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                _populate_minimal_sqlite(conn, analysis_date="2026-06-19")
            finally:
                conn.close()
            # 缓存到 2026-06-19（周五），请求 2026-06-22（下周一，非缓存日）
            result = load_latest_snapshot(
                db_path=db_path, analysis_date="2026-06-22"
            )
            self.assertIn(
                result.status, ("ok", "degraded", "stale"),
                f"expected snapshot, got {result.status}: {result.message}",
            )
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.snapshot.sectors)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_explicit_date_non_trading_day_returns_snapshot(self) -> None:
        """显式传入非交易日（周末）时，repository 用 <= 截断组装快照。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                _populate_minimal_sqlite(conn, analysis_date="2026-06-19")
            finally:
                conn.close()
            # 2026-06-20 是周六，缓存最新交易日是 2026-06-19（周五）
            result = load_latest_snapshot(
                db_path=db_path, analysis_date="2026-06-20"
            )
            self.assertIn(
                result.status, ("ok", "degraded", "stale"),
                f"expected snapshot, got {result.status}: {result.message}",
            )
            self.assertIsNotNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class FindLatestAnalysisDateTests(unittest.TestCase):
    """_find_latest_analysis_date 应按 classification_system 过滤（P2b）。"""

    @staticmethod
    def _insert_bar(conn, sector_id, cls_sys, trade_date):
        conn.execute(
            "INSERT INTO sector_daily_bars (sector_id, classification_system, "
            "trade_date, close, turnover_amount, source, fetch_run_id, "
            "source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 100.0, 1e9, 'akshare', 'r1', ?, ?, ?)",
            (sector_id, cls_sys, trade_date, trade_date, trade_date, trade_date),
        )

    def test_scoped_query_returns_correct_date(self) -> None:
        """em_industry 缓存的较晚日期不应影响 ths_industry 的最新日期查询。"""
        from services.data_service import _find_latest_analysis_date

        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                self._insert_bar(conn, "BK0001", "em_industry", "2026-06-25")
                self._insert_bar(conn, "BK0001", "ths_industry", "2026-06-19")
                conn.commit()
            finally:
                conn.close()

            # 不加过滤：返回全局 MAX = EM 的 2026-06-25
            self.assertEqual(
                _find_latest_analysis_date(Path(db_path)), "2026-06-25"
            )
            # 过滤 ths_industry：返回 THS 的 2026-06-19
            self.assertEqual(
                _find_latest_analysis_date(Path(db_path), "ths_industry"),
                "2026-06-19",
            )
            # 过滤 em_industry：返回 EM 的 2026-06-25
            self.assertEqual(
                _find_latest_analysis_date(Path(db_path), "em_industry"),
                "2026-06-25",
            )
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class RefreshMarketDataTests(unittest.TestCase):
    """refresh_market_data 产品级入口测试（注入 fake source，不联网）。"""

    def test_refresh_success_returns_snapshot(self) -> None:
        """刷新成功后返回 snapshot。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            result = RefreshMarketDataTests._refresh(
                db_path, source=_FakeRefreshSource()
            )
            self.assertIn(result.status, ("ok", "degraded", "stale"))
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.snapshot.sectors)
            self.assertIsNotNone(result.refresh_result)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_refresh_failure_with_old_cache_returns_refresh_failed(self) -> None:
        """刷新失败但有旧缓存时返回 refresh_failed + 旧快照。"""
        from services.data_service import refresh_market_data

        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            # 第一次成功写入缓存
            refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(),
            )
            # 第二次失败
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(fail=True),
            )
            self.assertEqual(result.status, "refresh_failed")
            self.assertIsNotNone(result.snapshot, "should have old cache snapshot")
            self.assertTrue(result.message)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_refresh_failure_no_cache_returns_no_cache(self) -> None:
        """刷新失败且无缓存时返回 no_cache。"""
        from services.data_service import refresh_market_data

        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(fail=True),
            )
            self.assertEqual(result.status, "no_cache")
            self.assertIsNone(result.snapshot)
            self.assertTrue(result.message)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_refresh_empty_data_no_cache_returns_no_cache(self) -> None:
        """sync 返回空数据（failure_count==0 但 required tasks 0 行）且无缓存时返回 no_cache。"""

        from services.data_service import refresh_market_data

        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(empty=True),
            )
            self.assertEqual(result.status, "no_cache")
            self.assertIsNone(result.snapshot)
            self.assertTrue(result.message, "should have a failure message")
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_refresh_empty_data_with_old_cache_returns_refresh_failed(self) -> None:
        """sync 返回空数据但有旧缓存时返回 refresh_failed（不静默展示为成功）。"""

        from services.data_service import refresh_market_data

        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            # 第一次成功写入缓存
            refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(),
            )
            # 第二次空返回（failure_count==0, required_ok==False）
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(empty=True),
            )
            self.assertEqual(result.status, "refresh_failed")
            self.assertIsNotNone(result.snapshot, "should have old cache snapshot")
            self.assertTrue(result.message)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    @staticmethod
    def _refresh(db_path, source):
        from services.data_service import refresh_market_data

        return refresh_market_data(
            db_path=db_path,
            analysis_date="2026-06-19",
            source=source,
        )


class LoadOrRefreshSnapshotTests(unittest.TestCase):
    """load_or_refresh_snapshot 一站式入口测试。"""

    def test_no_refresh_calls_load_latest(self) -> None:
        """refresh=False 时行为与 load_latest_snapshot 一致。"""
        result = load_or_refresh_snapshot(
            refresh=False, db_path="/tmp/__nonexistent_fs_test.sqlite"
        )
        self.assertEqual(result.status, "no_cache")

    def test_refresh_calls_refresh_market_data(self) -> None:
        """refresh=True 时行为与 refresh_market_data 一致。"""
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            result = load_or_refresh_snapshot(
                refresh=True,
                db_path=db_path,
                analysis_date="2026-06-19",
                source=_FakeRefreshSource(),
            )
            self.assertIn(result.status, ("ok", "degraded", "stale"))
            self.assertIsNotNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
