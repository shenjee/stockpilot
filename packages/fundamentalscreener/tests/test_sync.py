"""Phase 6A: sync_all 与 CLI init-db 的最小测试。

测试默认不访问任何真实网络；所有数据源都通过 ``FakeFundamentalDataSource``
注入。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List

from packages.fundamentalscreener.data_sources import FakeFundamentalDataSource
from packages.fundamentalscreener.data_sources.fetch_report import (
    CodeFetchReport,
    ReportedRows,
)
from packages.fundamentalscreener.sqlite_schema import connect, init_db
from packages.fundamentalscreener.sync import main, sync_all


def _make_source() -> FakeFundamentalDataSource:
    return FakeFundamentalDataSource(
        name="fake",
        sectors=[
            {
                "sector_id": "BK0001",
                "classification_system": "em_industry",
                "sector_name": "示例行业",
                "source_updated_at": "2026-06-19T10:00:00+08:00",
            }
        ],
        sector_constituents=[
            {
                "sector_id": "BK0001",
                "classification_system": "em_industry",
                "code": "002371",
                "as_of_date": "2026-06-19",
            }
        ],
        sector_daily=[
            {
                "sector_id": "BK0001",
                "classification_system": "em_industry",
                "trade_date": "2026-06-19",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "turnover_amount": 1.0e9,
            }
        ],
        benchmark_daily=[
            {
                "benchmark": "hs300",
                "trade_date": "2026-06-19",
                "close": 3500.0,
                "turnover_amount": 1.0e11,
            }
        ],
        stock_universe=[
            {
                "code": "002371",
                "name": "示例公司",
                "market": "SZ",
                "listing_status": "L",
                "as_of_date": "2026-06-19",
            }
        ],
        company_daily_snapshot=[
            {
                "code": "002371",
                "trade_date": "2026-06-19",
                "close": 10.0,
                "turnover_amount": 1.0e8,
                "turnover_rate": 0.02,
                "market_cap": 1.2e11,
            }
        ],
        company_valuation_history=[
            {
                "code": "002371",
                "trade_date": "2026-06-19",
                "pe": 25.0,
                "pb": 3.0,
            }
        ],
        financial_metrics=[
            {
                "code": "002371",
                "report_period": "2026Q1",
                "period_end_date": "2026-03-31",
                "disclosure_date": "2026-04-28",
                "period_type": "quarterly",
                "as_of_date": "2026-04-28",
                "revenue_yoy": 0.18,
                "net_profit_yoy": 0.22,
            }
        ],
    )


class SyncAllTests(unittest.TestCase):
    def test_sync_all_writes_rows_with_lineage(self) -> None:
        conn = connect(":memory:")
        try:
            source = _make_source()
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                codes=["002371"],
            )
            # 所有 8 个子任务都应成功。
            self.assertEqual(result.failure_count, 0, msg=result.tasks)
            self.assertGreater(result.success_count, 0)

            # 检查 sectors 表带上了血缘列。
            cur = conn.execute(
                "SELECT source, fetch_run_id, created_at, updated_at "
                "FROM sectors WHERE sector_id = ?",
                ("BK0001",),
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "fake")
            self.assertEqual(row[1], result.fetch_run_id)
            self.assertTrue(row[2])
            self.assertTrue(row[3])

            # 财务表：point-in-time 关键字段被写入。
            cur = conn.execute(
                "SELECT report_period, disclosure_date, as_of_date "
                "FROM financial_metrics WHERE code = ?",
                ("002371",),
            )
            row = cur.fetchone()
            self.assertEqual(row[0], "2026Q1")
            self.assertEqual(row[1], "2026-04-28")
            self.assertEqual(row[2], "2026-04-28")

            # data_fetch_log 至少有一条成功记录。
            cur = conn.execute(
                "SELECT task, success FROM data_fetch_log "
                "WHERE fetch_run_id = ?",
                (result.fetch_run_id,),
            )
            log_rows = list(cur.fetchall())
            self.assertGreater(len(log_rows), 0)
            self.assertTrue(all(r[1] == 1 for r in log_rows))
        finally:
            conn.close()

    def test_sync_all_skips_per_code_tasks_without_codes(self) -> None:
        """P2: 未提供 codes 时跳过 per-code 公司层任务（估值历史 + 财务指标），
        避免对全量股票池逐只发起网络请求。batch 任务（股票池 + 日度快照）仍运行。"""
        conn = connect(":memory:")
        try:
            source = _make_source()
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            task_names = {t["task"] for t in result.tasks}
            # batch 公司层任务仍然运行
            self.assertIn("get_stock_universe", task_names)
            self.assertIn("get_company_daily_snapshot", task_names)
            # per-code 公司层任务被跳过
            self.assertNotIn("get_company_valuation_history", task_names)
            self.assertNotIn("get_financial_metrics", task_names)
            # valuation / financial 表不应有数据
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM company_valuation_history").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM financial_metrics").fetchone()[0], 0
            )
        finally:
            conn.close()

    def test_sync_all_failed_source_logs_failure_without_breaking_cache(self) -> None:
        conn = connect(":memory:")
        try:
            # 第一次：成功写入
            source_ok = _make_source()
            ok_result = sync_all(
                conn,
                source_ok,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            self.assertEqual(ok_result.failure_count, 0)

            # 第二次：使用会失败的 source（fail=True），同一 db。
            source_bad = _make_source()
            source_bad.fail = True
            bad_result = sync_all(
                conn,
                source_bad,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            self.assertGreater(bad_result.failure_count, 0)

            # 已有缓存仍然存在（第一次成功的数据没有被失败任务清掉）。
            cur = conn.execute("SELECT COUNT(*) FROM sectors")
            self.assertEqual(cur.fetchone()[0], 1)
            cur = conn.execute("SELECT COUNT(*) FROM company_daily_snapshot")
            self.assertEqual(cur.fetchone()[0], 1)

            # 失败任务应在 data_fetch_log 中留下失败记录。
            cur = conn.execute(
                "SELECT COUNT(*) FROM data_fetch_log "
                "WHERE fetch_run_id = ? AND success = 0",
                (bad_result.fetch_run_id,),
            )
            self.assertGreater(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_sync_all_runs_init_db_implicitly(self) -> None:
        # 调用方不需要先 init_db；sync_all 会自动建表。
        conn = connect(":memory:")
        try:
            source = _make_source()
            sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='sector_daily_bars'"
            )
            self.assertIsNotNone(cur.fetchone())
        finally:
            conn.close()


class SyncCliTests(unittest.TestCase):
    def test_init_db_cli_creates_tables(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["init-db", "--db", str(db_path)])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["command"], "init-db")
            self.assertIn("sectors", payload["tables"])
            self.assertIn("data_fetch_log", payload["tables"])
            # 文件真的写到了磁盘。
            self.assertTrue(db_path.exists())

    def test_init_db_cli_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            for _ in range(3):
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = main(["init-db", "--db", str(db_path)])
                self.assertEqual(rc, 0)
            # 三次后仍然只有标准表。
            conn = connect(str(db_path))
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                self.assertEqual(cur.fetchone()[0], 9)  # 9 张表见 TABLE_NAMES
            finally:
                conn.close()

    def test_sync_cli_without_akshare_returns_error(self) -> None:
        # Phase 6B：CLI sync 不再被禁用，但真实同步需要 akshare。当 akshare 不可用时
        # 应返回 rc=2 并给出明确安装提示（不联网、不崩溃）。用 patch 强制
        # ``_akshare_available`` 返回 False，保证测试不依赖 akshare 是否真的安装、
        # 也不触达网络。
        from packages.fundamentalscreener import sync as sync_mod
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            err = io.StringIO()
            with patch.object(sync_mod, "_akshare_available", return_value=False):
                with redirect_stderr(err):
                    rc = main(
                        [
                            "sync",
                            "--db",
                            str(db_path),
                            "--date",
                            "2026-06-19",
                            "--classification-system",
                            "em_industry",
                        ]
                    )
            self.assertEqual(rc, 2)
            self.assertIn("akshare", err.getvalue().lower())

    def test_sync_cli_rejects_unsupported_classification_system(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "sw_l1",
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn("em_industry", err.getvalue())

    def test_sync_cli_with_injected_source_succeeds(self) -> None:
        # 通过 ``source=`` 注入 fake 数据源，跳过 akshare 探测，验证 CLI sync 接线：
        # 正常运行、输出 JSON、rc=0，且板块层数据真的写入 SQLite。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--benchmark",
                        "hs300",
                    ],
                    source=_make_source(),
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["command"], "sync")
            self.assertEqual(payload["classification_system"], "em_industry")
            self.assertEqual(payload["benchmark"], "hs300")
            self.assertIn("fetch_run_id", payload)
            self.assertGreater(payload["success_count"], 0)
            # 板块行真的写入了磁盘。
            conn = connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT sector_id, source FROM sectors LIMIT 1"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "BK0001")
                self.assertEqual(row[1], "fake")
            finally:
                conn.close()

    def test_sync_cli_returns_rc1_when_required_tasks_write_zero_rows(self) -> None:
        # P1 回归：空数据源让所有任务以 0 行"成功"（failure_count==0），但 Phase 6B
        # 必需的板块层任务没有写入任何行，CLI 必须返回 rc=1 而非 rc=0。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--benchmark",
                        "hs300",
                    ],
                    source=FakeFundamentalDataSource(name="fake"),
                )
            self.assertEqual(rc, 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["failure_count"], 0)
            self.assertEqual(payload["row_count"], 0)

    def test_sync_cli_returns_rc1_when_source_fails(self) -> None:
        # P1 回归：数据源抛错时必需任务失败（failure_count>0），CLI 返回 rc=1。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--benchmark",
                        "hs300",
                    ],
                    source=FakeFundamentalDataSource(name="fake", fail=True),
                )
            self.assertEqual(rc, 1)
            payload = json.loads(out.getvalue())
            self.assertGreater(payload["failure_count"], 0)

    def test_sync_cli_legitimate_empty_company_result_returns_rc0(self) -> None:
        # 请求了证券但源没有区间数据：任务 success，不因 row_count=0 返回 rc=1。
        source = _make_source()
        source.get_company_daily_snapshot = lambda trade_date, codes=None: ReportedRows(
            [],
            CodeFetchReport(requested_count=1, empty_count=1),
        )
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--codes",
                        "002371",
                    ],
                    source=source,
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "success")
            daily = next(t for t in payload["tasks"] if t["task"] == "get_company_daily_snapshot")
            self.assertTrue(daily["success"])
            self.assertEqual(daily["status"], "success")
            self.assertEqual(daily["requested_count"], 1)
            self.assertEqual(daily["empty_count"], 1)
            self.assertEqual(daily["row_count"], 0)
            self.assertIsNone(daily["error"])

    def test_sync_cli_partial_company_failure_returns_rc1_and_keeps_rows(self) -> None:
        # 一只证券抛错、另一只有数据：不能报全成功；已写入行保留。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            conn = connect(str(db_path))
            try:
                first = sync_all(
                    conn,
                    _make_source(),
                    analysis_date="2026-06-19",
                    classification_system="em_industry",
                    codes=["002371"],
                )
            finally:
                conn.close()
            self.assertEqual(first.status, "success")
            conn = connect(str(db_path))
            try:
                cached = conn.execute(
                    "SELECT COUNT(*) FROM company_daily_snapshot"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertGreater(cached, 0)

            source = _make_source()
            source.get_company_daily_snapshot = lambda trade_date, codes=None: ReportedRows(
                [
                    {
                        "code": "600584",
                        "trade_date": trade_date,
                        "close": 20.0,
                        "turnover_amount": 1.0,
                        "turnover_rate": 0.01,
                        "market_cap": 2.0,
                    }
                ],
                CodeFetchReport(
                    requested_count=2,
                    succeeded_count=1,
                    failed_count=1,
                    failures=(
                        {"code": "002371", "error": "RuntimeError: boom"},
                    ),
                ),
            )
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--codes",
                        "002371,600584",
                    ],
                    source=source,
                )
            self.assertEqual(rc, 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "partial")
            self.assertGreater(payload["failure_count"], 0)
            daily = next(t for t in payload["tasks"] if t["task"] == "get_company_daily_snapshot")
            self.assertFalse(daily["success"])
            self.assertEqual(daily["status"], "partial")
            self.assertEqual(daily["failed_count"], 1)
            self.assertIn("002371", daily["failures"][0]["code"])
            self.assertIsNotNone(daily["error"])
            conn = connect(str(db_path))
            try:
                self.assertGreaterEqual(
                    conn.execute("SELECT COUNT(*) FROM company_daily_snapshot").fetchone()[0],
                    cached,
                )
                log = conn.execute(
                    "SELECT success, error, details FROM data_fetch_log "
                    "WHERE fetch_run_id = ? AND task = 'get_company_daily_snapshot'",
                    (payload["fetch_run_id"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(log[0], 0)
            self.assertIsNotNone(log[1])
            logged_details = json.loads(log[2])
            self.assertEqual(logged_details["failures"][0]["code"], "002371")
            self.assertIn("RuntimeError: boom", logged_details["failures"][0]["error"])

    def test_sync_cli_all_requested_codes_failed_returns_rc1(self) -> None:
        source = _make_source()
        source.get_financial_metrics = lambda codes, as_of_date: ReportedRows(
            [],
            CodeFetchReport(
                requested_count=1,
                failed_count=1,
                failures=({"code": "002371", "error": "RuntimeError: boom"},),
            ),
        )
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    [
                        "sync",
                        "--db",
                        str(db_path),
                        "--date",
                        "2026-06-19",
                        "--classification-system",
                        "em_industry",
                        "--codes",
                        "002371",
                    ],
                    source=source,
                )
            self.assertEqual(rc, 1)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["status"], "partial")
            financial = next(t for t in payload["tasks"] if t["task"] == "get_financial_metrics")
            self.assertEqual(financial["status"], "failed")
            self.assertFalse(financial["success"])
            self.assertEqual(financial["error"][:12], "fetch_failed")

    def test_quality_cli_returns_real_report(self) -> None:
        # Phase 6D：quality 命令读取 SQLite 并输出结构化质量报告。
        # 先 sync 一批数据，再运行 quality。
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fundamental.sqlite"
            conn = connect(str(db_path))
            try:
                sync_all(
                    conn,
                    _make_source(),
                    analysis_date="2026-06-19",
                    classification_system="em_industry",
                )
            finally:
                conn.close()

            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(
                    ["quality", "--db", str(db_path), "--date", "2026-06-19"]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIn("quality_report_id", payload)
            self.assertIn(payload["status"], ("ok", "degraded", "stale", "invalid"))
            self.assertEqual(payload["analysis_date"], "2026-06-19")
            # 应有 issues 列表（即使为空）
            self.assertIn("issues", payload)


class SyncPkValidationTests(unittest.TestCase):
    def test_persist_exception_retains_fetch_facts_and_rolls_back(self) -> None:
        from packages.fundamentalscreener.sync_persistence import _run_task

        conn = connect(":memory:")
        self.addCleanup(conn.close)
        init_db(conn)
        conn.execute("CREATE TABLE rollback_probe (value TEXT)")
        with conn:
            conn.execute("INSERT INTO rollback_probe VALUES ('cached')")
        report = CodeFetchReport(
            requested_count=3, succeeded_count=1, empty_count=1, failed_count=1,
            failures=({"code": "000002", "error": "RuntimeError: unavailable"},),
        )

        def persist(rows):
            conn.execute("UPDATE rollback_probe SET value = 'new'")
            raise RuntimeError("write unavailable")

        task = _run_task(
            conn, fetch_run_id="persist-error", source_name="fake",
            task="get_company_daily_snapshot",
            fetch=lambda: ReportedRows([{"code": "000001"}], report),
            persist=persist,
        )
        self.assertEqual(task["status"], "failed")
        self.assertFalse(task["success"])
        self.assertEqual(task["row_count"], 0)
        self.assertIn("persist_failed: write unavailable", task["error"])
        log = conn.execute(
            "SELECT success, row_count, error, details FROM data_fetch_log "
            "WHERE fetch_run_id = 'persist-error'"
        ).fetchone()
        self.assertEqual(tuple(log[:3]), (0, 0, task["error"]))
        details = json.loads(log["details"])
        self.assertEqual(details["status"], "failed")
        for key, value in report.as_dict().items():
            self.assertEqual(task[key], value)
            self.assertEqual(details[key], value)
        self.assertEqual(
            conn.execute("SELECT value FROM rollback_probe").fetchone()[0], "cached",
        )

    """P1 回归：缺失主键的行不能被静默写成 PK='' 的"成功缓存"。"""

    def test_missing_sector_id_rejected_not_silently_written(self) -> None:
        conn = connect(":memory:")
        try:
            source = FakeFundamentalDataSource(
                name="fake",
                sectors=[
                    # 缺 sector_id 的畸形行。
                    {
                        "classification_system": "em_industry",
                        "sector_name": "坏行",
                    }
                ],
            )
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            # list_sectors 任务应因"全部行被拒"而失败。
            sector_task = next(t for t in result.tasks if t["task"] == "list_sectors")
            self.assertFalse(sector_task["success"], msg=sector_task)
            self.assertEqual(sector_task["row_count"], 0)
            self.assertIn("all_rows_rejected", sector_task["error"])
            self.assertGreater(sector_task["rejections"], 0)

            # sectors 表里不应有任何 PK="" 的行。
            cur = conn.execute("SELECT COUNT(*) FROM sectors WHERE sector_id = ''")
            self.assertEqual(cur.fetchone()[0], 0)

            # data_fetch_log 应记录这条失败任务。
            cur = conn.execute(
                "SELECT success, error, details FROM data_fetch_log "
                "WHERE fetch_run_id = ? AND task = 'list_sectors'",
                (result.fetch_run_id,),
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 0)
            self.assertIn("all_rows_rejected", row[1])
            details = json.loads(row[2])
            self.assertGreater(details["rejected_count"], 0)
        finally:
            conn.close()

    def test_partial_rejection_still_succeeds_with_details(self) -> None:
        conn = connect(":memory:")
        try:
            source = FakeFundamentalDataSource(
                name="fake",
                sectors=[
                    {
                        "sector_id": "BK0001",
                        "classification_system": "em_industry",
                        "sector_name": "好行",
                    },
                    # 缺 sector_id 的坏行
                    {"classification_system": "em_industry", "sector_name": "坏行"},
                ],
            )
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            sector_task = next(t for t in result.tasks if t["task"] == "list_sectors")
            # 有效行保留，但部分拒绝不再记为无条件全成功。
            self.assertFalse(sector_task["success"], msg=sector_task)
            self.assertEqual(sector_task["status"], "partial")
            self.assertIn("partial_rows_rejected", sector_task["error"])
            self.assertEqual(sector_task["row_count"], 1)
            self.assertEqual(sector_task["rejections"], 1)

            cur = conn.execute("SELECT COUNT(*) FROM sectors")
            self.assertEqual(cur.fetchone()[0], 1)
            cur = conn.execute("SELECT COUNT(*) FROM sectors WHERE sector_id = ''")
            self.assertEqual(cur.fetchone()[0], 0)

            # data_fetch_log 中 details 字段记录了被拒摘要。
            cur = conn.execute(
                "SELECT details FROM data_fetch_log "
                "WHERE fetch_run_id = ? AND task = 'list_sectors'",
                (result.fetch_run_id,),
            )
            details = json.loads(cur.fetchone()[0])
            self.assertEqual(details["rejected_count"], 1)
            self.assertEqual(details["status"], "partial")
            logged = conn.execute(
                "SELECT success FROM data_fetch_log "
                "WHERE fetch_run_id = ? AND task = 'list_sectors'",
                (result.fetch_run_id,),
            ).fetchone()
            self.assertEqual(logged[0], 0)
        finally:
            conn.close()

    def test_missing_code_in_financial_rejected(self) -> None:
        conn = connect(":memory:")
        try:
            source = FakeFundamentalDataSource(
                name="fake",
                sectors=[
                    {
                        "sector_id": "BK0001",
                        "classification_system": "em_industry",
                        "sector_name": "示例",
                    }
                ],
                sector_constituents=[
                    {
                        "sector_id": "BK0001",
                        "classification_system": "em_industry",
                        "code": "002371",
                        "as_of_date": "2026-06-19",
                    }
                ],
                stock_universe=[
                    {"code": "002371", "name": "示例", "as_of_date": "2026-06-19"}
                ],
                financial_metrics=[
                    # 有 code 能通过 fake source 的 code 过滤，但缺 disclosure_date
                    # （financial_metrics PK 之一）→ 应被 _validate_required 拒绝。
                    {
                        "code": "002371",
                        "report_period": "2026Q1",
                        "period_end_date": "2026-03-31",
                        "period_type": "quarterly",
                    }
                ],
            )
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                codes=["002371"],
            )
            fin_task = next(
                t for t in result.tasks if t["task"] == "get_financial_metrics"
            )
            self.assertFalse(fin_task["success"])
            self.assertIn("all_rows_rejected", fin_task["error"])

            cur = conn.execute("SELECT COUNT(*) FROM financial_metrics")
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_sync_layer_defaults_satisfy_pk_validation(self) -> None:
        """P3 回归：enricher 填入的 sync 层默认值（classification_system /
        as_of_date / benchmark）应满足 PK 校验，源行缺这些字段不应被误拒。"""

        conn = connect(":memory:")
        try:
            source = FakeFundamentalDataSource(
                name="fake",
                sectors=[
                    {
                        "sector_id": "BK0001",
                        "classification_system": "em_industry",
                        "sector_name": "示例",
                    }
                ],
                # 故意只给 sector_id + code，不给 classification_system / as_of_date
                # → enricher 会用 sync context 的 classification_system /
                # analysis_date 填入，PK 校验应通过。
                sector_constituents=[
                    {"sector_id": "BK0001", "code": "002371"},
                ],
                stock_universe=[
                    {"code": "002371", "name": "示例", "as_of_date": "2026-06-19"}
                ],
                # benchmark 行只给 trade_date + close，不给 benchmark → enricher
                # 用 sync context 的 benchmark 参数填入。
                benchmark_daily=[
                    {"trade_date": "2026-06-19", "close": 3500.0},
                ],
            )
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
                benchmark="hs300",
            )

            constituents_task = next(
                t for t in result.tasks if t["task"] == "get_sector_constituents"
            )
            self.assertTrue(constituents_task["success"], msg=constituents_task)
            self.assertEqual(constituents_task["row_count"], 1)
            self.assertEqual(constituents_task["rejections"], 0)

            # 写入的行带上了 sync 层默认值。
            cur = conn.execute(
                "SELECT classification_system, as_of_date FROM sector_constituents "
                "WHERE code = ?",
                ("002371",),
            )
            row = cur.fetchone()
            self.assertEqual(row[0], "em_industry")
            self.assertEqual(row[1], "2026-06-19")

            benchmark_task = next(
                t for t in result.tasks if t["task"] == "get_benchmark_daily"
            )
            self.assertTrue(benchmark_task["success"], msg=benchmark_task)
            self.assertEqual(benchmark_task["rejections"], 0)
            cur = conn.execute(
                "SELECT benchmark FROM benchmark_daily_bars WHERE trade_date = ?",
                ("2026-06-19",),
            )
            self.assertEqual(cur.fetchone()[0], "hs300")
        finally:
            conn.close()


class BenchmarkTableTests(unittest.TestCase):
    """P2 回归：基准日线必须独立写入 benchmark_daily_bars，不污染 sector_daily_bars。"""

    def test_benchmark_rows_in_dedicated_table(self) -> None:
        conn = connect(":memory:")
        try:
            source = _make_source()
            result = sync_all(
                conn,
                source,
                analysis_date="2026-06-19",
                classification_system="em_industry",
            )
            bm_task = next(
                t for t in result.tasks if t["task"] == "get_benchmark_daily"
            )
            self.assertTrue(bm_task["success"], msg=bm_task)
            self.assertEqual(bm_task["row_count"], 1)

            # benchmark_daily_bars 有数据
            cur = conn.execute(
                "SELECT benchmark, close FROM benchmark_daily_bars"
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "hs300")

            # sector_daily_bars 不应被 benchmark 污染（无 classification_system='benchmark' 行）
            cur = conn.execute(
                "SELECT COUNT(*) FROM sector_daily_bars "
                "WHERE classification_system = 'benchmark'"
            )
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()


class FinancialPitSyncTests(unittest.TestCase):
    """#181: sync observations must not rewrite the past."""

    def setUp(self):
        from packages.fundamentalscreener.sqlite_schema import connect, init_db
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.addCleanup(self.conn.close)
        self.row = dict(code="000001", report_period="2026Q1",
                        period_end_date="2026-03-31", disclosure_date="2026-04-30",
                        period_type="quarterly", roe=0.12)

    def persist(self, day, source_name="test", **changes):
        from unittest.mock import patch
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        row = dict(self.row, as_of_date=day, **changes)
        with patch("packages.fundamentalscreener.sync_persistence._ts", return_value=day + "T16:00:00+08:00"):
            with self.conn:
                return build_financial_metrics_persist(
                    self.conn, source_name=source_name, fetch_run_id="fetch-" + day,
                    analysis_date=day)([row])

    def financials(self, day):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        return SqliteFundamentalRepository(self.conn, day)._load_financials(self.conn, {"000001"})

    def test_same_value_repeat_preserves_history_and_refreshes_audit(self):
        self.persist("2026-05-01")
        self.assertEqual(len(self.financials("2026-05-15")), 1)
        self.persist("2026-09-30")
        self.assertEqual(self.financials("2026-05-15")[0].roe, .12)
        self.assertEqual(len(self.financials("2026-09-30")), 1)
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(row["as_of_date"], "2026-05-01")
        self.assertEqual(row["created_at"][:10], "2026-05-01")
        self.assertEqual(row["updated_at"][:10], "2026-09-30")
        self.assertEqual(row["fetch_run_id"], "fetch-2026-09-30")

    def test_revision_cannot_leak_and_loss_survives_repeat(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01")
        self.persist("2026-09-30", roe=.08)
        self.assertEqual(self.financials("2026-05-15"), [])
        self.assertEqual(self.financials("2026-09-30")[0].roe, .08)
        self.persist("2026-10-01", roe=.08)
        self.assertEqual(self.financials("2026-05-15"), [])
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        evidence = read_evidence(self.conn, row)
        self.assertEqual(evidence["unavailable_from"], "2026-05-01")
        self.assertEqual(evidence["unavailable_before"], "2026-09-30")
        self.assertEqual(row["as_of_date"], "2026-09-30")

    def test_source_change_preserves_same_value_visibility(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01", source_name="old-source", _disclosure_date_basis="estimated")
        self.persist("2026-09-30", source_name="new-source")
        self.assertEqual(self.financials("2026-05-15")[0].roe, .12)
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        evidence = read_evidence(self.conn, row)
        self.assertEqual(row["as_of_date"], "2026-05-01")
        self.assertEqual(row["source"], "new-source")
        self.assertEqual(evidence["source_change"]["from_source"], "old-source")
        self.assertEqual(evidence["source_change"]["to_source"], "new-source")
        self.assertEqual(evidence["disclosure_basis"], "estimated")
        self.assertNotIn("revised", evidence)
        self.assertNotIn("unavailable_from", evidence)

    def test_omitted_disclosure_basis_preserves_existing_evidence(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01", _disclosure_date_basis="estimated")
        # Both same values and revised values retain the recorded basis.
        self.persist("2026-09-30")
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(read_evidence(self.conn, row)["disclosure_basis"], "estimated")
        self.persist("2026-10-01", roe=.08, _disclosure_date_basis=None)
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(read_evidence(self.conn, row)["disclosure_basis"], "estimated")

    def test_explicit_disclosure_basis_updates_evidence(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01", _disclosure_date_basis="estimated")
        self.persist("2026-09-30", _disclosure_date_basis="source_provided")
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(row["as_of_date"], "2026-05-01")
        self.assertEqual(read_evidence(self.conn, row)["disclosure_basis"], "source_provided")

    def test_period_end_change_is_a_revision(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01")
        self.persist("2026-09-30", period_end_date="2026-03-30")
        self.assertEqual(self.financials("2026-05-15"), [])
        self.assertEqual(self.financials("2026-09-30")[0].roe, .12)
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(row["period_end_date"], "2026-03-30")
        self.assertEqual(row["as_of_date"], "2026-09-30")
        self.assertTrue(read_evidence(self.conn, row)["revised"])

    def test_revision_with_backdated_source_visibility_uses_observation_day(self):
        from unittest.mock import patch
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        self.persist("2026-05-01")
        with patch("packages.fundamentalscreener.sync_persistence._ts", return_value="2026-09-30T12:00:00+08:00"):
            build_financial_metrics_persist(self.conn, source_name="test", fetch_run_id="revision",
                                            analysis_date="2026-05-01")(
                [dict(self.row, as_of_date="2026-05-01", roe=.08)])
        self.assertEqual(self.financials("2026-05-15"), [])
        self.assertEqual(self.financials("2026-09-30")[0].roe, .08)

    def test_every_value_and_null_change_is_a_revision(self):
        from packages.fundamentalscreener.sync_persistence import _FINANCIAL_COLUMNS
        for column in _FINANCIAL_COLUMNS[6:19]:
            with self.subTest(column=column):
                self.conn.execute("DELETE FROM financial_metrics")
                self.conn.execute("DELETE FROM data_fetch_log")
                self.persist("2026-05-01", **{column: None})
                self.persist("2026-09-30", **{column: .2})
                self.assertEqual(self.financials("2026-05-15"), [])
                self.persist("2026-10-01", **{column: None})
                self.assertEqual(self.financials("2026-09-30"), [])

    def test_future_disclosure_excluded_even_if_observed(self):
        self.persist("2026-05-01", disclosure_date="2026-05-20")
        self.assertEqual(self.financials("2026-05-15"), [])
        self.assertEqual(len(self.financials("2026-05-20")), 1)

    def test_unknown_and_invalid_disclosures_rejected(self):
        for value in (None, "", "unknown", "2026-02-30"):
            with self.subTest(value=value):
                result = self.persist("2026-05-01", disclosure_date=value)
                self.assertEqual(result.written, 0)
                self.assertEqual(result.rejections, 1)
        self.assertEqual(self.financials("2026-05-15"), [])

    def test_legacy_adoption_is_conservative_and_repeatable(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        self.persist("2026-05-01")
        self.conn.execute("DELETE FROM data_fetch_log")
        self.conn.execute("UPDATE financial_metrics SET updated_at = '2026-06-01'")
        self.persist("2026-09-30")
        self.assertEqual(self.financials("2026-05-15"), [])
        self.assertEqual(len(self.financials("2026-06-01")), 1)
        self.persist("2026-10-01")
        row = self.conn.execute("SELECT * FROM financial_metrics").fetchone()
        self.assertEqual(row["as_of_date"], "2026-06-01")
        self.assertTrue(read_evidence(self.conn, row)["legacy_unverified"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM financial_metrics").fetchone()[0], 1)

    def test_evidence_rolls_back_with_value_write(self):
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        with self.assertRaises(RuntimeError):
            with self.conn:
                build_financial_metrics_persist(self.conn, source_name="test", fetch_run_id="rollback",
                                                analysis_date="2026-05-01")([self.row])
                raise RuntimeError("rollback")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM financial_metrics").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM data_fetch_log").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
