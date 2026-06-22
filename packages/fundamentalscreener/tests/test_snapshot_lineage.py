"""Phase 6D: CLI snapshot 血缘输出测试。

验证 CLI JSON 顶层 ``snapshot`` 对象在 fixture 和 --db 两条路径下都包含
``snapshot_id`` / ``source_set`` / ``fetch_run_id`` / ``quality_report_id`` /
``data_quality_status`` / ``config_version`` / ``formula_version`` / ``generated_at``
等血缘字段（docs §7 / §18）。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List

from packages.fundamentalscreener.cli import main
from packages.fundamentalscreener.sqlite_schema import connect, init_db

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "minimal_market.json"
)

_SNAPSHOT_KEYS = (
    "snapshot_id",
    "analysis_date",
    "data_cutoff",
    "data_quality_status",
    "source_set",
    "fetch_run_id",
    "quality_report_id",
    "config_version",
    "formula_version",
    "generated_at",
)


def _gen_weekdays(n: int, end_iso: str) -> List[str]:
    end = date.fromisoformat(end_iso)
    days: List[str] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(days))


def _populate_db(conn, analysis_date: str = "2026-06-19") -> None:
    """填充一个最小但完整的测试数据库（65 个交易日）。"""
    days = _gen_weekdays(65, analysis_date)

    for sid, name in [("BK0001", "半导体"), ("BK0002", "工程机械")]:
        conn.execute(
            "INSERT INTO sectors (sector_id, classification_system, sector_name, source, "
            "fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, 'em_industry', ?, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (sid, name),
        )
        for d in days:
            conn.execute(
                "INSERT INTO sector_daily_bars (sector_id, classification_system, "
                "trade_date, close, turnover_amount, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (?, 'em_industry', ?, 100.0, 1e9, 'akshare_em', 'fetch-test-001', "
                "'2026-06-19', '2026-06-19', '2026-06-19')",
                (sid, d),
            )

    for sid, code in [("BK0001", "002371"), ("BK0001", "600584"),
                       ("BK0002", "000001")]:
        conn.execute(
            "INSERT INTO sector_constituents (sector_id, classification_system, code, "
            "as_of_date, source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, 'em_industry', ?, ?, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (sid, code, analysis_date),
        )

    for d in days:
        conn.execute(
            "INSERT INTO benchmark_daily_bars (benchmark, trade_date, close, "
            "turnover_amount, source, fetch_run_id, source_updated_at, created_at, "
            "updated_at) VALUES ('hs300', ?, 3500.0, 1e11, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (d,),
        )

    for code, name in [("002371", "北方华创"), ("600584", "长电科技"),
                        ("000001", "平安银行")]:
        conn.execute(
            "INSERT INTO stocks (code, name, market, listing_status, as_of_date, "
            "source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, 'SZ', 'L', ?, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (code, name, analysis_date),
        )
        for d in days:
            conn.execute(
                "INSERT INTO company_daily_snapshot (code, trade_date, close, "
                "turnover_amount, turnover_rate, market_cap, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (?, ?, 10.0, 1e8, 0.02, 1e10, 'akshare_em', 'fetch-test-001', "
                "'2026-06-19', '2026-06-19', '2026-06-19')",
                (code, d),
            )

    # financial_metrics (≥50% coverage → INFO, not WARNING)
    for code in ["002371", "600584", "000001"]:
        conn.execute(
            "INSERT INTO financial_metrics (code, report_period, period_end_date, "
            "disclosure_date, period_type, as_of_date, revenue_yoy, net_profit_yoy, "
            "deducted_net_profit_yoy, gross_margin, net_margin, roe, "
            "operating_cashflow_to_profit, free_cashflow, debt_to_asset, "
            "interest_bearing_debt_ratio, accounts_receivable_yoy, inventory_yoy, "
            "gross_margin_yoy_change, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) "
            "VALUES (?, '2025Q4', '2025-12-31', '2026-04-15', 'quarter', "
            "'2025-12-31', 10.0, 12.0, 11.0, 30.0, 15.0, 18.0, 0.8, 1e9, 0.4, "
            "0.3, 8.0, 5.0, -1.0, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (code,),
        )

    # company_valuation_history (≥50% coverage → INFO, not WARNING)
    for code in ["002371", "600584", "000001"]:
        for i, d in enumerate(days):
            conn.execute(
                "INSERT INTO company_valuation_history (code, trade_date, pe, pb, "
                "source, fetch_run_id, source_updated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'akshare_em', 'fetch-test-001', '2026-06-19', "
                "'2026-06-19', '2026-06-19')",
                (code, d, 20.0 + i * 0.1, 2.0 + i * 0.01),
            )

    # data_fetch_log
    conn.execute(
        "INSERT INTO data_fetch_log (fetch_run_id, source, task, started_at, "
        "finished_at, success, row_count, used_cache, error, details) "
        "VALUES ('fetch-test-001', 'akshare_em', 'list_sectors', "
        "'2026-06-19T10:00:00+08:00', '2026-06-19T10:01:00+08:00', 1, 2, 0, NULL, NULL)"
    )


def _run(argv: List[str]) -> Dict[str, Any]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"cli exited with {rc}, stdout={buf.getvalue()!r}"
    text = buf.getvalue().strip()
    return json.loads(text)


class FixtureSnapshotLineageTests(unittest.TestCase):
    """--fixture 路径：snapshot 对象应包含完整血缘字段。"""

    def test_sectors_fixture_has_snapshot(self) -> None:
        d = _run(["sectors", "--fixture", str(FIXTURE), "--format", "json"])
        self.assertIn("snapshot", d)
        snap = d["snapshot"]
        for key in _SNAPSHOT_KEYS:
            self.assertIn(key, snap)
        self.assertTrue(snap["snapshot_id"].startswith("snapshot-"))
        self.assertEqual(snap["analysis_date"], "2026-06-19")
        self.assertEqual(snap["data_quality_status"], "ok")
        self.assertIn("fixture", snap["source_set"])

    def test_companies_fixture_has_snapshot(self) -> None:
        d = _run([
            "companies", "--fixture", str(FIXTURE),
            "--sector", "半导体", "--format", "json",
        ])
        self.assertIn("snapshot", d)
        self.assertTrue(d["snapshot"]["snapshot_id"])

    def test_financials_fixture_has_snapshot(self) -> None:
        d = _run([
            "financials", "--fixture", str(FIXTURE),
            "--codes", "002371", "--format", "json",
        ])
        self.assertIn("snapshot", d)
        self.assertTrue(d["snapshot"]["snapshot_id"])

    def test_valuations_fixture_has_snapshot(self) -> None:
        d = _run([
            "valuations", "--fixture", str(FIXTURE),
            "--codes", "002371", "--format", "json",
        ])
        self.assertIn("snapshot", d)

    def test_screen_fixture_has_snapshot(self) -> None:
        d = _run([
            "screen", "--fixture", str(FIXTURE), "--format", "json",
        ])
        self.assertIn("snapshot", d)

    def test_no_data_source_has_snapshot(self) -> None:
        """无 --fixture / --db 时 snapshot 仍应存在（空 source_set）。"""
        d = _run(["sectors", "--format", "json"])
        self.assertIn("snapshot", d)
        self.assertEqual(d["snapshot"]["source_set"], {})


class DbSnapshotLineageTests(unittest.TestCase):
    """--db 路径：snapshot 对象应透传真实血缘。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "test.sqlite"
        conn = connect(str(self._db_path))
        init_db(conn)
        _populate_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sectors_db_has_real_lineage(self) -> None:
        d = _run([
            "sectors", "--db", str(self._db_path),
            "--date", "2026-06-19", "--format", "json",
        ])
        self.assertIn("snapshot", d)
        snap = d["snapshot"]
        for key in _SNAPSHOT_KEYS:
            self.assertIn(key, snap)
        self.assertTrue(snap["snapshot_id"].startswith("snapshot-"))
        self.assertEqual(snap["analysis_date"], "2026-06-19")
        # 真实 source_set 应包含 sector / benchmark
        self.assertIn("sector", snap["source_set"])
        self.assertEqual(snap["source_set"]["sector"], "akshare_em")
        # fetch_run_id 应来自 data_fetch_log
        self.assertTrue(snap["fetch_run_id"])

    def test_db_data_quality_status_ok(self) -> None:
        d = _run([
            "sectors", "--db", str(self._db_path),
            "--date", "2026-06-19", "--format", "json",
        ])
        self.assertEqual(d["snapshot"]["data_quality_status"], "ok")

    def test_screen_db_priority_empty_when_degraded(self) -> None:
        """degraded 状态下 priority 桶应为空。"""
        # 通过删除财务数据使覆盖率 < 50%，触发 degraded（非 invalid）
        conn = connect(str(self._db_path))
        conn.execute("DELETE FROM financial_metrics")
        conn.commit()
        conn.close()

        d = _run([
            "screen", "--db", str(self._db_path),
            "--date", "2026-06-19", "--format", "json",
        ])
        self.assertEqual(d["snapshot"]["data_quality_status"], "degraded")
        # priority 应为空（degraded 降级）
        self.assertEqual(d["candidates"]["priority"], [])
        # 应有降级 warning
        self.assertTrue(
            any("data_quality_degraded" in w for w in d["warnings"])
        )

    def test_db_invalid_returns_error(self) -> None:
        """空数据库 → invalid → CLI 返回 rc=2。"""
        empty_db = Path(self._tmp.name) / "empty.sqlite"
        if empty_db.exists():
            empty_db.unlink()
        conn = connect(str(empty_db))
        init_db(conn)
        conn.close()

        out = io.StringIO()
        err = io.StringIO()
        rc: int
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([
                "sectors", "--db", str(empty_db),
                "--date", "2026-06-19", "--format", "json",
            ])
        self.assertEqual(rc, 2)
        self.assertIn("data_quality_invalid", err.getvalue())
        empty_db.unlink()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
