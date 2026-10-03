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

    def _current_financial_rows(self, basis):
        return [
            dict(code=code, report_period="2026Q1", period_type="quarterly",
                 period_end_date="2026-03-31", disclosure_date="2026-04-30",
                 as_of_date="2026-06-19", _disclosure_date_basis=basis,
                 revenue_yoy=.3, net_profit_yoy=.3, deducted_net_profit_yoy=.3,
                 gross_margin=.5, net_margin=.3, roe=.3,
                 operating_cashflow_to_profit=1.5, free_cashflow=1e9,
                 # Current AkShare source has no equivalent debt field (#183).
                 debt_to_asset=.1, interest_bearing_debt_ratio=None,
                 accounts_receivable_yoy=.05, inventory_yoy=.05,
                 gross_margin_yoy_change=.05)
            for code in ("002371", "600584", "000001")
        ]

    def _replace_with_current_financials(self, basis):
        from unittest.mock import patch
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        conn = connect(str(self._db_path))
        try:
            with conn, patch("packages.fundamentalscreener.sync_persistence._ts",
                             return_value="2026-06-19T16:00:00+08:00"):
                conn.execute("DELETE FROM financial_metrics")
                build_financial_metrics_persist(conn, source_name="akshare_em", fetch_run_id="pit-current",
                                                analysis_date="2026-06-19")(self._current_financial_rows(basis))
                # Ensure the cohort has real candidates above the priority threshold.
                conn.execute("UPDATE company_valuation_history SET pe=10, pb=1 WHERE trade_date='2026-06-19'")
        finally:
            conn.close()

    def _sync_financial_rows(self, day, rows):
        from unittest.mock import patch
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        conn = connect(str(self._db_path))
        try:
            with conn, patch("packages.fundamentalscreener.sync_persistence._ts",
                             return_value=day + "T16:00:00+08:00"):
                build_financial_metrics_persist(conn, source_name="akshare_em", fetch_run_id="pit-" + day,
                                                analysis_date=day)(rows)
        finally:
            conn.close()

    def _historical_screen(self):
        return _run(["screen", "--db", str(self._db_path), "--date", "2026-06-19", "--format", "json"])

    def test_older_report_revision_does_not_degrade_selected_newer_report(self):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        self._replace_with_current_financials("estimated")
        old = dict(self._current_financial_rows("estimated")[0], report_period="2025A",
                   period_type="annual", period_end_date="2025-12-31",
                   disclosure_date="2026-04-15", as_of_date="2026-05-01", roe=.2)
        self._sync_financial_rows("2026-05-01", [old])
        before = self._historical_screen()
        self.assertTrue(before["candidates"]["priority"])
        self._sync_financial_rows("2026-09-30", [dict(old, roe=.08)])
        after = self._historical_screen()
        self.assertEqual(after["snapshot"]["data_quality_status"], "ok")
        self.assertEqual(after["candidates"], before["candidates"])
        self.assertEqual(after["snapshot"]["source_set"], before["snapshot"]["source_set"])
        self.assertEqual(after["snapshot"]["fetch_run_id"], before["snapshot"]["fetch_run_id"])
        self.assertFalse(any("financial_pit_history_unavailable" in w for w in after["warnings"]))
        repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
        self.assertEqual(next(f.roe for f in repo.load_snapshot().financials if f.code == "002371"), .3)

    def test_selected_report_revision_still_warns_when_falling_back(self):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        self._replace_with_current_financials("estimated")
        old = dict(self._current_financial_rows("estimated")[0], report_period="2025A",
                   period_type="annual", period_end_date="2025-12-31",
                   disclosure_date="2026-04-15", as_of_date="2026-05-01", roe=.2)
        self._sync_financial_rows("2026-05-01", [old])
        self._sync_financial_rows("2026-09-30", [dict(self._current_financial_rows("estimated")[0], roe=.08)])
        repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
        snapshot = repo.load_snapshot()
        self.assertEqual(next(f.roe for f in snapshot.financials if f.code == "002371"), .2)
        issue = next(i for i in repo.quality_report.issues if i.code == "financial_pit_history_unavailable")
        self.assertEqual(issue.details["report_period"], "2026Q1")
        self.assertEqual(issue.level, "warning")
        data = self._historical_screen()
        self.assertEqual(data["snapshot"]["data_quality_status"], "degraded")
        self.assertEqual(data["candidates"]["priority"], [])
        self.assertTrue(any("financial_pit_history_unavailable" in w for w in data["warnings"]))

    def test_missing_report_uses_same_report_type_order_as_repository(self):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        for retained_type, missing_type, expected_status in (
            ("annual", "quarterly", "ok"), ("quarterly", "annual", "degraded"),
        ):
            with self.subTest(retained=retained_type, missing=missing_type):
                self._replace_with_current_financials("estimated")
                conn = connect(str(self._db_path))
                with conn:
                    conn.execute("DELETE FROM financial_metrics")
                conn.close()
                current = [dict(r, report_period="2025A" if retained_type == "annual" else "2025Q4",
                                period_type=retained_type, period_end_date="2025-12-31")
                           for r in self._current_financial_rows("estimated")]
                self._sync_financial_rows("2026-06-19", current)
                missing = dict(current[0], report_period="2025A" if missing_type == "annual" else "2025Q4",
                               period_type=missing_type, disclosure_date="2026-04-15",
                               as_of_date="2026-05-01", roe=.2)
                self._sync_financial_rows("2026-05-01", [missing])
                self._sync_financial_rows("2026-09-30", [dict(missing, roe=.08)])
                repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
                repo.load_snapshot()
                self.assertEqual(repo.quality_report.status, expected_status)
                gaps = [i for i in repo.quality_report.issues if i.code == "financial_pit_history_unavailable"]
                self.assertEqual(bool(gaps), missing_type == "annual")

    def test_period_end_correction_compares_lost_versions_order(self):
        for old_end, new_end, expected_status in (
            ("2025-12-31", "2026-04-30", "ok"),
            ("2026-04-30", "2025-12-31", "degraded"),
        ):
            with self.subTest(old_end=old_end):
                self._replace_with_current_financials("estimated")
                row = dict(self._current_financial_rows("estimated")[0], report_period="corrected",
                           period_end_date=old_end, as_of_date="2026-05-01", roe=.2)
                self._sync_financial_rows("2026-05-01", [row])
                self._sync_financial_rows("2026-09-30", [dict(row, period_end_date=new_end)])
                data = self._historical_screen()
                self.assertEqual(data["snapshot"]["data_quality_status"], expected_status)
                self.assertEqual(bool(data["candidates"]["priority"]), expected_status == "ok")

    def test_prior_evidence_gap_survives_another_revision(self):
        from packages.fundamentalscreener.financial_pit import read_evidence
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        self._replace_with_current_financials("estimated")
        row = self._current_financial_rows("estimated")[0]
        self._sync_financial_rows("2026-09-30", [dict(row, roe=.08)])
        # Simulate evidence from the prior release, before ordering was saved.
        conn = connect(str(self._db_path))
        with conn:
            financial = conn.execute("SELECT * FROM financial_metrics WHERE code='002371'").fetchone()
            details = read_evidence(conn, financial)
            details.pop("unavailable_selections")
            conn.execute("UPDATE data_fetch_log SET details=? WHERE task LIKE 'financial_pit:%' AND fetch_run_id=?",
                         (json.dumps(details), financial["fetch_run_id"]))
        conn.close()
        self._sync_financial_rows("2026-10-01", [dict(row, roe=.09)])
        repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
        repo.load_snapshot()
        self.assertTrue(any(i.code == "financial_pit_history_unavailable" for i in repo.quality_report.issues))

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

    def test_db_legacy_financials_report_unverified_pit(self) -> None:
        d = _run([
            "sectors", "--db", str(self._db_path),
            "--date", "2026-06-19", "--format", "json",
        ])
        self.assertEqual(d["snapshot"]["data_quality_status"], "degraded")
        self.assertTrue(any("financial_pit_disclosure_unverified" in w for w in d["warnings"]))

    def test_all_cli_consumers_preserve_pit_warnings(self):
        commands = [
            ["sectors"], ["sector-detail", "--sector", "BK0001"],
            ["companies", "--sector", "BK0001"],
            ["financials", "--codes", "002371"],
            ["valuations", "--codes", "002371"], ["screen"],
        ]
        for command in commands:
            with self.subTest(command=command):
                data = _run(command + ["--db", str(self._db_path), "--date", "2026-06-19", "--format", "json"])
                self.assertTrue(any("financial_pit_disclosure_unverified" in w for w in data["warnings"]))
                self.assertEqual(data["snapshot"]["data_quality_status"], "degraded")
                self.assertTrue(data["snapshot"]["quality_report_id"])

    def test_ignored_debt_preserves_priority_but_other_warnings_still_degrade(self):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        self._replace_with_current_financials("estimated")
        before = self._historical_screen()
        self.assertEqual(before["snapshot"]["data_quality_status"], "ok")
        self.assertTrue(before["candidates"]["priority"])
        conn = connect(str(self._db_path))
        try:
            with conn:
                conn.execute("UPDATE financial_metrics SET interest_bearing_debt_ratio=.3 "
                             "WHERE code='002371'")
            repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
            repo.load_snapshot()
            ignored = [i for i in repo.quality_report.issues
                       if i.code == "interest_bearing_debt_cache_ignored"]
            self.assertEqual(len(ignored), 1)
            self.assertEqual(ignored[0].level, "info")
            self.assertIn("杠杆分量权重仍为15%", ignored[0].message)
            after = self._historical_screen()
            self.assertEqual(after["snapshot"]["data_quality_status"], "ok")
            self.assertEqual(after["candidates"], before["candidates"])
            self.assertTrue(any("interest_bearing_debt_cache_ignored" in w
                                for w in after["warnings"]))
            # A separate coverage warning must still downgrade the full snapshot.
            with conn:
                conn.execute("DELETE FROM financial_metrics WHERE code != '002371'")
            degraded = self._historical_screen()
            self.assertEqual(degraded["snapshot"]["data_quality_status"], "degraded")
            self.assertEqual(degraded["candidates"]["priority"], [])
            self.assertTrue(any("interest_bearing_debt_cache_ignored" in w
                                for w in degraded["warnings"]))
        finally:
            conn.close()

    def test_current_disclosure_info_preserves_priority_and_all_cli_notices(self):
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        commands = [
            ["sectors"], ["sector-detail", "--sector", "BK0001"],
            ["companies", "--sector", "BK0001"],
            ["financials", "--codes", "002371"],
            ["valuations", "--codes", "002371"], ["screen"],
        ]
        for basis, issue_code in (
            ("estimated", "financial_pit_disclosure_estimated"),
            (None, "financial_pit_disclosure_unverified"),
        ):
            self._replace_with_current_financials(basis)
            repo = SqliteFundamentalRepository(self._db_path, "2026-06-19")
            repo.load_snapshot()
            issues = [i for i in repo.quality_report.issues if i.code == issue_code]
            self.assertEqual(len(issues), 3)
            self.assertTrue(all(i.level == "info" for i in issues))
            self.assertEqual(repo.quality_report.status, "ok")
            for command in commands:
                with self.subTest(basis=basis, command=command):
                    data = _run(command + ["--db", str(self._db_path), "--date", "2026-06-19", "--format", "json"])
                    self.assertEqual(data["snapshot"]["data_quality_status"], "ok")
                    self.assertTrue(any(issue_code in w for w in data["warnings"]))
                    self.assertFalse(any("data_quality_degraded" in w for w in data["warnings"]))
                    if command == ["screen"]:
                        self.assertTrue(data["candidates"]["priority"])

    def test_legacy_warning_persists_after_repeated_adoption(self):
        from unittest.mock import patch
        from packages.fundamentalscreener.sqlite_repository import SqliteFundamentalRepository
        from packages.fundamentalscreener.sync_task_builders import build_financial_metrics_persist
        self._replace_with_current_financials("estimated")
        conn = connect(str(self._db_path))
        try:
            with conn:
                conn.execute("DELETE FROM data_fetch_log WHERE task LIKE 'financial_pit:%'")
            for day in ("2026-06-20", "2026-06-21"):
                with conn, patch("packages.fundamentalscreener.sync_persistence._ts",
                                 return_value=day + "T16:00:00+08:00"):
                    build_financial_metrics_persist(conn, source_name="akshare_em", fetch_run_id="pit-" + day,
                                                    analysis_date=day)(self._current_financial_rows("estimated"))
                repo = SqliteFundamentalRepository(conn, "2026-06-19")
                repo.load_snapshot()
                issues = [i for i in repo.quality_report.issues if i.code == "financial_pit_disclosure_estimated"]
                self.assertEqual(len(issues), 3)
                self.assertTrue(all(i.level == "warning" and i.details["legacy_unverified"] for i in issues))
                self.assertEqual(repo.quality_report.status, "degraded")
                data = _run(["screen", "--db", str(self._db_path), "--date", "2026-06-19", "--format", "json"])
                self.assertEqual(data["candidates"]["priority"], [])
        finally:
            conn.close()

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
