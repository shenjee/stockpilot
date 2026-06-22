"""Phase 6D: SqliteFundamentalRepository 单元测试。

验证 repository 能从 SQLite 组装 ``MarketSnapshot``，支持 point-in-time 过滤、
估值分位计算、质量状态透传，且组装结果可被现有 core 消费。
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from typing import List

from packages.fundamentalscreener.percentile import compute_valuation_percentiles
from packages.fundamentalscreener.repositories import MarketSnapshot
from packages.fundamentalscreener.sector_rotation import compute_sector_rotation
from packages.fundamentalscreener.sqlite_repository import (
    QualityInvalidError,
    SqliteFundamentalRepository,
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


def _populate_db(conn, analysis_date: str = "2026-06-19") -> None:
    """填充一个最小但完整的测试数据库（65 个交易日，2 板块 3 公司）。"""
    days = _gen_weekdays(65, analysis_date)

    # 板块
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

    # 成分股
    for sid, code in [("BK0001", "002371"), ("BK0001", "600584"),
                       ("BK0002", "000001")]:
        conn.execute(
            "INSERT INTO sector_constituents (sector_id, classification_system, code, "
            "as_of_date, source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, 'em_industry', ?, ?, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (sid, code, analysis_date),
        )

    # benchmark
    for d in days:
        conn.execute(
            "INSERT INTO benchmark_daily_bars (benchmark, trade_date, close, "
            "turnover_amount, source, fetch_run_id, source_updated_at, created_at, "
            "updated_at) VALUES ('hs300', ?, 3500.0, 1e11, 'akshare_em', 'fetch-test-001', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (d,),
        )

    # 股票池 + 公司日度快照
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

    # 财务指标（point-in-time）
    for code in ("002371", "600584"):
        conn.execute(
            "INSERT INTO financial_metrics (code, report_period, period_end_date, "
            "disclosure_date, period_type, as_of_date, revenue_yoy, net_profit_yoy, "
            "gross_margin, net_margin, roe, debt_to_asset, source, fetch_run_id, "
            "source_updated_at, created_at, updated_at) "
            "VALUES (?, '2026Q1', '2026-03-31', '2026-04-28', 'quarterly', "
            "'2026-04-28', 0.18, 0.22, 0.36, 0.12, 0.14, 0.42, 'akshare_em', "
            "'fetch-test-001', '2026-06-19', '2026-06-19', '2026-06-19')",
            (code,),
        )

    # 估值历史（65 天，用于分位计算）
    for code in ("002371", "600584"):
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
        "VALUES ('fetch-test-001', 'akshare_em', 'list_sectors', '2026-06-19T10:00:00+08:00', "
        "'2026-06-19T10:01:00+08:00', 1, 2, 0, NULL, NULL)"
    )


class SqliteRepositoryTests(unittest.TestCase):
    """验证 SqliteFundamentalRepository 的核心组装能力。"""

    def _setup_db(self):
        conn = connect(":memory:")
        init_db(conn)
        _populate_db(conn)
        return conn

    def test_load_snapshot_assembles_sectors_and_benchmark(self) -> None:
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
                classification_system="em_industry",
                benchmark="hs300",
            )
            snapshot = repo.load_snapshot()
            self.assertIsInstance(snapshot, MarketSnapshot)
            self.assertEqual(snapshot.date, "2026-06-19")
            self.assertEqual(snapshot.classification_system, "em_industry")
            # 2 个板块
            self.assertEqual(len(snapshot.sectors), 2)
            sector_ids = {s.sector_id for s in snapshot.sectors}
            self.assertEqual(sector_ids, {"BK0001", "BK0002"})
            # 板块有日线和成分
            for s in snapshot.sectors:
                self.assertGreaterEqual(len(s.daily), 60)
                self.assertGreaterEqual(len(s.constituents), 1)
            # benchmark 有日线
            self.assertEqual(snapshot.benchmark.id, "hs300")
            self.assertGreaterEqual(len(snapshot.benchmark.daily), 60)
        finally:
            conn.close()

    def test_load_snapshot_assembles_companies(self) -> None:
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            # 3 家公司
            codes = {c.code for c in snapshot.companies}
            self.assertEqual(codes, {"002371", "600584", "000001"})
            # 公司有 sector_id 和 market_cap
            for c in snapshot.companies:
                self.assertIsNotNone(c.sector_id)
                self.assertIsNotNone(c.market_cap)
                self.assertGreaterEqual(len(c.daily), 60)
        finally:
            conn.close()

    def test_financials_point_in_time_filtering(self) -> None:
        """disclosure_date > analysis_date 的财报不被读取。"""
        conn = self._setup_db()
        try:
            # 追加一条 analysis_date 之后才披露的财报
            conn.execute(
                "INSERT INTO financial_metrics (code, report_period, period_end_date, "
                "disclosure_date, period_type, as_of_date, net_margin, source, "
                "fetch_run_id, source_updated_at, created_at, updated_at) "
                "VALUES ('002371', '2026Q2', '2026-06-30', '2026-08-31', "
                "'semiannual', '2026-08-31', 0.15, 'akshare_em', 'fetch-test-001', "
                "'2026-06-19', '2026-06-19', '2026-06-19')"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            fin = {f.code: f for f in snapshot.financials}
            self.assertIn("002371", fin)
            # 应取 Q1（disclosure 2026-04-28），不是 Q2（disclosure 2026-08-31）
            self.assertAlmostEqual(fin["002371"].revenue_yoy or 0, 0.18, places=2)
        finally:
            conn.close()

    def test_valuations_include_computed_percentiles(self) -> None:
        """估值数据包含基于本地历史计算的 pe_percentile / pb_percentile。"""
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            val_codes = {v.code for v in snapshot.valuations}
            self.assertEqual(val_codes, {"002371", "600584"})
            for v in snapshot.valuations:
                self.assertIsNotNone(v.pe)
                self.assertIsNotNone(v.pb)
                self.assertIsNotNone(v.pe_percentile)
                self.assertIsNotNone(v.pb_percentile)
                # 当前 PE 是历史最高 → percentile 接近 1
                self.assertGreater(v.pe_percentile, 0.9)
        finally:
            conn.close()

    def test_metadata_contains_lineage_fields(self) -> None:
        """metadata 包含 snapshot_id / source_set / fetch_run_id 等。"""
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            repo.load_snapshot()
            meta = repo.metadata
            self.assertTrue(meta.snapshot_id.startswith("snapshot-"))
            self.assertEqual(meta.analysis_date, "2026-06-19")
            self.assertTrue(meta.fetch_run_id)
            self.assertTrue(meta.quality_report_id)
            # source_set 应包含 sector / benchmark / quote / financial / valuation
            ss = meta.source_set.to_dict()
            self.assertIn("sector", ss)
            self.assertIn("benchmark", ss)
            self.assertIn("quote", ss)
        finally:
            conn.close()

    def test_quality_report_status_propagates(self) -> None:
        """quality_report 的 status 被写入 snapshot.data_quality_status。"""
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            report = repo.quality_report
            self.assertEqual(report.status, snapshot.data_quality_status)
            self.assertEqual(snapshot.data_quality_status, "ok")
        finally:
            conn.close()

    def test_invalid_data_raises_quality_invalid_error(self) -> None:
        """空数据库 → invalid → QualityInvalidError。"""
        conn = connect(":memory:")
        init_db(conn)
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            with self.assertRaises(QualityInvalidError):
                repo.load_snapshot()
        finally:
            conn.close()

    def test_snapshot_consumable_by_sector_rotation(self) -> None:
        """组装的 MarketSnapshot 可被 compute_sector_rotation 消费。"""
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            result = compute_sector_rotation(snapshot, periods=(1, 5, 20, 60))
            self.assertEqual(len(result.sectors), 2)
            # 板块应有计算出的 return_1d
            for entry in result.sectors:
                self.assertIsNotNone(entry.return_1d)
            # 应有 chart_series（板块 + benchmark）
            types = [c.type for c in result.chart_series]
            self.assertEqual(types.count("benchmark"), 1)
            self.assertEqual(types.count("sector"), 2)
        finally:
            conn.close()

    def test_point_in_time_excludes_future_trade_dates(self) -> None:
        """trade_date > analysis_date 的行情/估值不被读取。"""
        conn = self._setup_db()
        try:
            # 追加一条未来日期的估值行
            conn.execute(
                "INSERT INTO company_valuation_history (code, trade_date, pe, pb, "
                "source, fetch_run_id, source_updated_at, created_at, updated_at) "
                "VALUES ('002371', '2026-06-20', 999.0, 999.0, 'akshare_em', "
                "'fetch-test-001', '2026-06-19', '2026-06-19', '2026-06-19')"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            val = next(v for v in snapshot.valuations if v.code == "002371")
            # 当前 PE 不应是 999.0（未来数据被排除）
            self.assertNotEqual(val.pe, 999.0)
        finally:
            conn.close()


class SqliteRepositoryDegradedTests(unittest.TestCase):
    """验证 short daily bars blocks snapshot generation (error, not degraded)."""

    def test_short_daily_bars_blocks_snapshot(self) -> None:
        conn = connect(":memory:")
        init_db(conn)
        try:
            _populate_db(conn)
            # 删除部分日线，使板块只剩 30 天
            conn.execute(
                "DELETE FROM sector_daily_bars WHERE trade_date < '2026-05-01'"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            with self.assertRaises(QualityInvalidError):
                repo.load_snapshot()
        finally:
            conn.close()


class UniverseScopingRegressionTests(unittest.TestCase):
    """回归测试：验证 companies/financials/valuations 宇宙严格限定于板块成分股。"""

    def _setup_db(self):
        conn = connect(":memory:")
        init_db(conn)
        _populate_db(conn)
        return conn

    # -- Finding 1: financials/valuations 不含板块外的 code --

    def test_unrelated_financials_excluded_from_snapshot(self) -> None:
        """板块外公司的 financial_metrics 行不出现在 snapshot.financials。"""
        conn = self._setup_db()
        try:
            # 插入一家非成分股的财务数据
            conn.execute(
                "INSERT INTO financial_metrics (code, report_period, period_end_date, "
                "disclosure_date, period_type, as_of_date, revenue_yoy, net_profit_yoy, "
                "gross_margin, net_margin, roe, debt_to_asset, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES ('999999', '2025Q4', '2025-12-31', '2026-04-15', 'annual', "
                "'2025-12-31', 0.99, 0.88, 0.77, 0.66, 0.55, 0.44, 'other_source', "
                "'fetch-other', '2026-06-19', '2026-06-19', '2026-06-19')"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            fin_codes = {f.code for f in snapshot.financials}
            self.assertNotIn("999999", fin_codes)
            # 原有成分股财务仍在
            self.assertIn("002371", fin_codes)
        finally:
            conn.close()

    def test_unrelated_valuations_excluded_from_snapshot(self) -> None:
        """板块外公司的估值历史不出现在 snapshot.valuations。"""
        conn = self._setup_db()
        try:
            conn.execute(
                "INSERT INTO company_valuation_history (code, trade_date, pe, pb, "
                "source, fetch_run_id, source_updated_at, created_at, updated_at) "
                "VALUES ('999999', '2026-06-18', 50.0, 5.0, 'other_source', "
                "'fetch-other', '2026-06-19', '2026-06-19', '2026-06-19')"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            val_codes = {v.code for v in snapshot.valuations}
            self.assertNotIn("999999", val_codes)
        finally:
            conn.close()

    # -- Finding 2: quote-only 公司不进入 companies，lineage 不被污染 --

    def test_quote_only_company_excluded_from_companies(self) -> None:
        """有日度快照但非板块成分股的公司不进入 snapshot.companies。"""
        conn = self._setup_db()
        try:
            conn.execute(
                "INSERT INTO company_daily_snapshot (code, trade_date, close, "
                "turnover_amount, turnover_rate, market_cap, source, fetch_run_id, "
                "source_updated_at, created_at, updated_at) "
                "VALUES ('999999', '2026-06-18', 5.0, 1e7, 0.01, 5e9, 'other_source', "
                "'fetch-other', '2026-06-19', '2026-06-19', '2026-06-19')"
            )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            company_codes = {c.code for c in snapshot.companies}
            self.assertNotIn("999999", company_codes)
        finally:
            conn.close()

    def test_unrelated_rows_dont_dominate_lineage(self) -> None:
        """板块外公司的 quote/valuation/financial 行不污染 source_set 和 fetch_run_id。"""
        conn = self._setup_db()
        try:
            # 插入大量非成分股行，使用不同 source / fetch_run_id
            for d in _gen_weekdays(10, "2026-06-19"):
                conn.execute(
                    "INSERT INTO company_daily_snapshot (code, trade_date, close, "
                    "turnover_amount, turnover_rate, market_cap, source, fetch_run_id, "
                    "source_updated_at, created_at, updated_at) "
                    "VALUES ('999999', ?, 5.0, 1e7, 0.01, 5e9, 'dominating_source', "
                    "'fetch-dominating', '2026-06-19', '2026-06-19', '2026-06-19')",
                    (d,),
                )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            repo.load_snapshot()
            meta = repo.metadata
            ss = meta.source_set.to_dict()
            # quote source 应仍为 akshare_em，不被 dominating_source 污染
            self.assertEqual(ss.get("quote"), "akshare_em")
            # fetch_run_id 应仍为 fetch-test-001
            self.assertEqual(meta.fetch_run_id, "fetch-test-001")
        finally:
            conn.close()

    # -- Finding 3: period_type 优先级 quarterly > first_quarter --

    def test_financial_period_type_quarterly_beats_first_quarter(self) -> None:
        """同一 period_end_date 下，quarterly 优先于 first_quarter。"""
        conn = self._setup_db()
        try:
            # 先删除 002371 的原有财务行
            conn.execute("DELETE FROM financial_metrics WHERE code = '002371'")
            # 插入同一 period_end_date 的两条记录
            for ptype, rev_yoy in [("first_quarter", 0.11), ("quarterly", 0.22)]:
                conn.execute(
                    "INSERT INTO financial_metrics (code, report_period, period_end_date, "
                    "disclosure_date, period_type, as_of_date, revenue_yoy, source, "
                    "fetch_run_id, source_updated_at, created_at, updated_at) "
                    "VALUES ('002371', ?, '2026-03-31', '2026-04-28', ?, "
                    "'2026-04-28', ?, 'akshare_em', 'fetch-test-001', "
                    "'2026-06-19', '2026-06-19', '2026-06-19')",
                    (f"2026-{ptype}", ptype, rev_yoy),
                )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            fin = {f.code: f for f in snapshot.financials}
            self.assertIn("002371", fin)
            # quarterly (0.22) 应被选中，而非 first_quarter (0.11)
            self.assertAlmostEqual(fin["002371"].revenue_yoy or 0, 0.22, places=2)
        finally:
            conn.close()

    def test_company_codes_align_across_lists(self) -> None:
        """financials 和 valuations 的 code 集合是 companies 的子集。"""
        conn = self._setup_db()
        try:
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            snapshot = repo.load_snapshot()
            company_codes = {c.code for c in snapshot.companies}
            fin_codes = {f.code for f in snapshot.financials}
            val_codes = {v.code for v in snapshot.valuations}
            self.assertTrue(fin_codes <= company_codes,
                            f"financials has codes not in companies: {fin_codes - company_codes}")
            self.assertTrue(val_codes <= company_codes,
                            f"valuations has codes not in companies: {val_codes - company_codes}")
        finally:
            conn.close()

    # -- Finding 2: financial lineage must use only rn=1 (actually selected) rows --

    def test_financial_lineage_not_polluted_by_superseded_rows(self) -> None:
        """被 ROW_NUMBER 淘汰的财务行不污染 source_set.financial 和 fetch_run_id。"""
        conn = self._setup_db()
        try:
            # 002371 已有一条 akshare_em/fetch-test-001 财务行 (disclosure=2026-04-28)。
            # 插入 5 条同 period_end_date 但更早 disclosure_date 的淘汰行，
            # 使用不同 source/fetch_run_id。若 lineage 未按 rn=1 过滤，
            # polluting_source / fetch-polluting 会以 5:1 占优。
            for i in range(5):
                d = f"2026-04-{27 - i:02d}"
                conn.execute(
                    "INSERT INTO financial_metrics (code, report_period, period_end_date, "
                    "disclosure_date, period_type, as_of_date, net_margin, source, "
                    "fetch_run_id, source_updated_at, created_at, updated_at) "
                    "VALUES ('002371', '2026Q1', '2026-03-31', ?, 'quarterly', "
                    "?, 0.1, 'polluting_source', 'fetch-polluting', "
                    "?, '2026-06-19', '2026-06-19')",
                    (d, d, d),
                )
            repo = SqliteFundamentalRepository(
                conn,  # type: ignore[arg-type]
                analysis_date="2026-06-19",
            )
            repo.load_snapshot()
            meta = repo.metadata
            ss = meta.source_set.to_dict()
            # financial source 应为 akshare_em（rn=1 行），而非 polluting_source
            self.assertEqual(ss.get("financial"), "akshare_em")
            # fetch_run_id 应为 fetch-test-001，而非 fetch-polluting
            self.assertEqual(meta.fetch_run_id, "fetch-test-001")
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
