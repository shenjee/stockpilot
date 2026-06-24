"""Phase 6D: 质量检查规则单元测试。

验证 ``run_quality_checks`` 能正确产出 ``ok | degraded | stale | invalid`` 状态，
并覆盖 docs §18 质量检查的主要规则。
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from typing import Any, Dict, List

from packages.fundamentalscreener.quality import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    run_quality_checks,
)
from packages.fundamentalscreener.sqlite_schema import connect, init_db


def _gen_weekdays(n: int, end_iso: str) -> List[str]:
    end = date.fromisoformat(end_iso)
    days: List[str] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(days))


def _insert_sector(conn, sector_id: str, name: str, cs: str = "em_industry") -> None:
    conn.execute(
        "INSERT INTO sectors (sector_id, classification_system, sector_name, source, "
        "fetch_run_id, source_updated_at, created_at, updated_at) "
        "VALUES (?, ?, ?, 'test', 'run1', '2026-06-19', '2026-06-19', '2026-06-19')",
        (sector_id, cs, name),
    )


def _insert_constituent(conn, sector_id: str, code: str, cs: str = "em_industry",
                        as_of: str = "2026-06-19") -> None:
    conn.execute(
        "INSERT INTO sector_constituents (sector_id, classification_system, code, "
        "as_of_date, source, fetch_run_id, source_updated_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'test', 'run1', '2026-06-19', '2026-06-19', '2026-06-19')",
        (sector_id, cs, code, as_of),
    )


def _insert_sector_daily(conn, sector_id: str, dates: List[str],
                          cs: str = "em_industry") -> None:
    for d in dates:
        conn.execute(
            "INSERT INTO sector_daily_bars (sector_id, classification_system, trade_date, "
            "close, turnover_amount, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) VALUES (?, ?, ?, 100.0, 1e9, 'test', 'run1', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (sector_id, cs, d),
        )


def _insert_benchmark(conn, benchmark: str, dates: List[str]) -> None:
    for d in dates:
        conn.execute(
            "INSERT INTO benchmark_daily_bars (benchmark, trade_date, close, "
            "turnover_amount, source, fetch_run_id, source_updated_at, created_at, "
            "updated_at) VALUES (?, ?, 3500.0, 1e11, 'test', 'run1', '2026-06-19', "
            "'2026-06-19', '2026-06-19')",
            (benchmark, d),
        )


def _insert_company_snapshot(conn, code: str, trade_date: str) -> None:
    conn.execute(
        "INSERT INTO company_daily_snapshot (code, trade_date, close, turnover_amount, "
        "turnover_rate, market_cap, source, fetch_run_id, source_updated_at, "
        "created_at, updated_at) VALUES (?, ?, 10.0, 1e8, 0.02, 1e10, 'test', 'run1', "
        "'2026-06-19', '2026-06-19', '2026-06-19')",
        (code, trade_date),
    )


def _insert_financial(conn, code: str, disclosure: str, net_margin: float = 0.1) -> None:
    conn.execute(
        "INSERT INTO financial_metrics (code, report_period, period_end_date, "
        "disclosure_date, period_type, as_of_date, net_margin, source, fetch_run_id, "
        "source_updated_at, created_at, updated_at) VALUES (?, '2026Q1', '2026-03-31', "
        "?, 'quarterly', ?, ?, 'test', 'run1', '2026-06-19', '2026-06-19', '2026-06-19')",
        (code, disclosure, disclosure, net_margin),
    )


def _insert_valuation(
    conn,
    code: str,
    trade_date: str,
    *,
    pe: float | None = 20.0,
    pb: float | None = 2.0,
) -> None:
    conn.execute(
        "INSERT INTO company_valuation_history (code, trade_date, pe, pb, "
        "source, fetch_run_id, source_updated_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'test', 'run1', '2026-06-19', '2026-06-19', '2026-06-19')",
        (code, trade_date, pe, pb),
    )


class QualityCheckTests(unittest.TestCase):
    """验证 run_quality_checks 的状态判定和 issue 级别。"""

    def _setup_db(self):
        conn = connect(":memory:")
        init_db(conn)
        return conn

    def test_empty_db_is_invalid(self) -> None:
        """空数据库：无 benchmark、无板块 → invalid。"""
        conn = self._setup_db()
        try:
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "invalid")
            self.assertGreater(report.counts[LEVEL_ERROR], 0)
        finally:
            conn.close()

    def test_no_benchmark_is_invalid(self) -> None:
        """有板块但无 benchmark → invalid（error）。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "invalid")
            self.assertTrue(any(i.code == "no_benchmark_history" for i in report.issues))
        finally:
            conn.close()

    def test_sector_daily_too_short_is_invalid(self) -> None:
        """板块日线 < 60 → error → invalid。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            days = _gen_weekdays(30, "2026-06-19")  # 只有 30 天
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", _gen_weekdays(65, "2026-06-19"))
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "invalid")
            self.assertTrue(
                any(i.code == "sector_daily_too_short" for i in report.issues)
            )
            self.assertEqual(
                next(i for i in report.issues if i.code == "sector_daily_too_short").level,
                LEVEL_ERROR,
            )
        finally:
            conn.close()

    def test_sector_no_constituents_unloaded_is_ok(self) -> None:
        """§15.9.4: 未加载成分股的板块不报 warning → ok。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            # 不插入成分股（板块未加载）
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "ok")
            self.assertFalse(
                any(i.code == "sector_no_constituents" for i in report.issues)
            )
        finally:
            conn.close()

    def test_sector_constituents_loaded_but_stale_is_degraded(self) -> None:
        """§15.9.4: 已加载成分股的板块但 as_of_date > analysis_date → warning。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            # 插入成分股但 as_of_date 晚于 analysis_date
            _insert_constituent(conn, "BK0001", "002371", as_of="2026-06-20")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            self.assertTrue(
                any(i.code == "sector_no_constituents" for i in report.issues)
            )
        finally:
            conn.close()

    def test_good_data_is_ok(self) -> None:
        """数据齐全：65 天日线 + 成分股 + 成分股行情 + benchmark → ok。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            _insert_financial(conn, "002371", "2026-04-28")
            _insert_valuation(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "ok")
        finally:
            conn.close()

    def test_stale_data_sets_stale_flag(self) -> None:
        """最新行情距 analysis_date 超过阈值 → stale。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            # 行情截止到 2026-06-10，analysis_date = 2026-06-19，差 9 天 > 7
            days = _gen_weekdays(65, "2026-06-10")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 成分股有行情（同样过旧），避免触发 coverage warning 干扰 stale 判定
            _insert_company_snapshot(conn, "002371", "2026-06-10")
            _insert_financial(conn, "002371", "2026-04-28")
            _insert_valuation(conn, "002371", "2026-06-10")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertTrue(report.stale)
            self.assertEqual(report.status, "stale")
        finally:
            conn.close()

    def test_low_financial_coverage_is_warning(self) -> None:
        """财务覆盖率 < 50% → warning → degraded。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 3 家公司有快照，但只有 1 家有财务
            for code in ("002371", "600584", "000001"):
                _insert_company_snapshot(conn, code, "2026-06-19")
            _insert_financial(conn, "002371", "2026-04-28")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            self.assertTrue(
                any(i.code == "low_financial_coverage" for i in report.issues)
            )
        finally:
            conn.close()

    def test_sector_daily_empty_is_error(self) -> None:
        """板块日线为 0 → error → invalid。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            # 不插入日线
            _insert_benchmark(conn, "hs300", _gen_weekdays(65, "2026-06-19"))
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "invalid")
            self.assertTrue(
                any(i.code == "sector_daily_empty" for i in report.issues)
            )
        finally:
            conn.close()

    def test_no_sectors_is_invalid(self) -> None:
        """无板块 → error → invalid。"""
        conn = self._setup_db()
        try:
            _insert_benchmark(conn, "hs300", _gen_weekdays(65, "2026-06-19"))
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "invalid")
            self.assertTrue(any(i.code == "no_sectors" for i in report.issues))
        finally:
            conn.close()

    def test_missing_constituent_quotes_is_warning(self) -> None:
        """个别板块成分缺行情 → missing_constituent_quotes warning → degraded。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 只有 002371 有行情，600584 缺行情
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            missing = [i for i in report.issues if i.code == "missing_constituent_quotes"]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0].level, LEVEL_WARNING)
            self.assertEqual(missing[0].details["missing"], 1)
            self.assertEqual(missing[0].details["total"], 2)
        finally:
            conn.close()

    def test_financial_coverage_denominator_is_all_constituents(self) -> None:
        """财务覆盖率分母为全部板块成分股，包括缺行情的。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 只有 2 家有行情（000001 缺行情）
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            _insert_company_snapshot(conn, "600584", "2026-06-19")
            # 只有 1 家有财务 → 1/3 = 33% < 50% → warning
            _insert_financial(conn, "002371", "2026-04-28")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            cov = [i for i in report.issues if i.code == "low_financial_coverage"]
            self.assertEqual(len(cov), 1)
            # 分母应为 3（全部成分股），而非 2（有行情的）
            self.assertEqual(cov[0].details["total"], 3)
            self.assertEqual(cov[0].details["with_financials"], 1)
        finally:
            conn.close()

    def test_low_valuation_coverage_is_warning(self) -> None:
        """板块成分缺估值 → low_valuation_coverage warning → degraded。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 3 家成分股都有行情和财务，但只有 1 家有估值 → 1/3 = 33% < 50%
            for code in ("002371", "600584", "000001"):
                _insert_company_snapshot(conn, code, "2026-06-19")
                _insert_financial(conn, code, "2026-04-28")
            _insert_valuation(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            cov = [i for i in report.issues if i.code == "low_valuation_coverage"]
            self.assertEqual(len(cov), 1)
            self.assertEqual(cov[0].level, LEVEL_WARNING)
            self.assertEqual(cov[0].details["total"], 3)
            self.assertEqual(cov[0].details["with_usable_valuations"], 1)
        finally:
            conn.close()

    def test_valuation_coverage_denominator_is_all_constituents(self) -> None:
        """估值覆盖率分母为全部板块成分股，包括缺行情的。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 只有 2 家有行情（000001 缺行情）
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            _insert_company_snapshot(conn, "600584", "2026-06-19")
            # 3 家都有财务，避免触发财务覆盖率 warning 干扰
            for code in ("002371", "600584", "000001"):
                _insert_financial(conn, code, "2026-04-28")
            # 只有 1 家有估值 → 1/3 = 33% < 50% → warning
            _insert_valuation(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            cov = [i for i in report.issues if i.code == "low_valuation_coverage"]
            self.assertEqual(len(cov), 1)
            # 分母应为 3（全部成分股），而非 2（有行情的）
            self.assertEqual(cov[0].details["total"], 3)
            self.assertEqual(cov[0].details["with_usable_valuations"], 1)
        finally:
            conn.close()

    def test_valuation_rows_with_missing_key_fields_are_not_covered(self) -> None:
        """有估值行但 pe/pb 关键字段缺失时，不应计入可用估值覆盖率。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            for code in ("002371", "600584", "000001"):
                _insert_company_snapshot(conn, code, "2026-06-19")
                _insert_financial(conn, code, "2026-04-28")

            # 三家公司都有 valuation row，但只有 002371 的 pe/pb 可用。
            _insert_valuation(conn, "002371", "2026-06-19")
            _insert_valuation(conn, "600584", "2026-06-19", pe=None, pb=2.0)
            _insert_valuation(conn, "000001", "2026-06-19", pe=20.0, pb=None)

            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            cov = [i for i in report.issues if i.code == "low_valuation_coverage"]
            self.assertEqual(len(cov), 1)
            self.assertEqual(cov[0].details["total"], 3)
            self.assertEqual(cov[0].details["with_valuation_rows"], 3)
            self.assertEqual(cov[0].details["with_usable_valuations"], 1)
        finally:
            conn.close()

    def test_low_constituent_quote_coverage_is_warning(self) -> None:
        """§15.9: 成分股行情覆盖率低于阈值 → warning → degraded（不阻断首屏）。

        重量层覆盖率不足只降级 snapshot，不让整个板块轮动表不可用。
        """
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            # 3 家成分股，只有 1 家有行情 → 33% < 50% → warning
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            self.assertEqual(report.status, "degraded")
            warn = [i for i in report.issues if i.code == "low_constituent_quote_coverage"]
            self.assertEqual(len(warn), 1)
            self.assertEqual(warn[0].level, LEVEL_WARNING)
            self.assertEqual(warn[0].details["with_quotes"], 1)
            self.assertEqual(warn[0].details["missing"], 2)
            self.assertEqual(warn[0].details["total"], 3)
            # 不应有 error 级 issue（不阻断 snapshot）
            self.assertEqual(report.counts[LEVEL_ERROR], 0)
        finally:
            conn.close()

    def test_individual_missing_quotes_is_warning_not_error(self) -> None:
        """个别成分缺行情（覆盖率 >= 50%）→ warning，不是 error。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            # 3 家成分股，2 家有行情 → 67% >= 50% → warning
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            _insert_company_snapshot(conn, "002371", "2026-06-19")
            _insert_company_snapshot(conn, "600584", "2026-06-19")
            _insert_financial(conn, "002371", "2026-04-28")
            _insert_financial(conn, "600584", "2026-04-28")
            _insert_valuation(conn, "002371", "2026-06-19")
            _insert_valuation(conn, "600584", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            # warning（degraded），不是 error（invalid）
            self.assertEqual(report.status, "degraded")
            warn = [i for i in report.issues if i.code == "missing_constituent_quotes"]
            self.assertEqual(len(warn), 1)
            self.assertEqual(warn[0].level, LEVEL_WARNING)
            self.assertEqual(warn[0].details["missing"], 1)
            self.assertEqual(warn[0].details["total"], 3)
            # 不应有 error 级 issue
            self.assertEqual(report.counts[LEVEL_ERROR], 0)
        finally:
            conn.close()

    def test_code_misalignment_reports_missing_financials_and_valuations(self) -> None:
        """code_misalignment INFO 报告缺财务/缺估值的成分股 code 列表。"""
        conn = self._setup_db()
        try:
            _insert_sector(conn, "BK0001", "半导体")
            _insert_constituent(conn, "BK0001", "002371")
            _insert_constituent(conn, "BK0001", "600584")
            _insert_constituent(conn, "BK0001", "000001")
            days = _gen_weekdays(65, "2026-06-19")
            _insert_sector_daily(conn, "BK0001", days)
            _insert_benchmark(conn, "hs300", days)
            # 全部有行情，避免触发 quote coverage error
            for code in ("002371", "600584", "000001"):
                _insert_company_snapshot(conn, code, "2026-06-19")
            # 只有 002371 有财务和估值
            _insert_financial(conn, "002371", "2026-04-28")
            _insert_valuation(conn, "002371", "2026-06-19")
            report = run_quality_checks(conn, "2026-06-19", "em_industry", "hs300")
            align = [i for i in report.issues if i.code == "code_misalignment"]
            self.assertEqual(len(align), 1)
            self.assertEqual(align[0].level, LEVEL_INFO)
            self.assertSetEqual(
                set(align[0].details["missing_financials"]), {"600584", "000001"}
            )
            self.assertSetEqual(
                set(align[0].details["missing_valuations"]), {"600584", "000001"}
            )
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
