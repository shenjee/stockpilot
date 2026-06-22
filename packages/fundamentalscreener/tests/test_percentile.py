"""Phase 6C: PE/PB 历史分位与风险标记单元测试。

使用内存 SQLite + ``init_db`` 创建 schema，直接插入测试数据验证：
- 分位计算正确性（低/中/高位置）。
- 负 PE / 缺失 / 样本不足 → warning + None。
- 配置覆盖（lookback / min_samples）。
- 风险标记：ST / 退市 / 停牌 / 亏损。
- 点-in-time：不读取 analysis_date 之后的数据。
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from typing import List

from packages.fundamentalscreener.config import PERCENTILE_CONFIG
from packages.fundamentalscreener.percentile import (
    PercentileResult,
    compute_company_risk_flags,
    compute_valuation_percentiles,
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


def _insert_valuation_rows(conn, code: str, rows: list) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO company_valuation_history "
            "(code, trade_date, pe, pb, source, fetch_run_id, source_updated_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'test', 'run1', '2026-06-19', "
            "'2026-06-19', '2026-06-19')",
            (code, r[0], r[1], r[2]),
        )


class PercentileComputeTests(unittest.TestCase):
    def _setup_db(self):
        conn = connect(":memory:")
        init_db(conn)
        return conn

    def test_basic_percentile_low_position(self) -> None:
        """当前 PE 是历史最低 → percentile 接近 0。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(100, "2026-06-19")
            # PE 从 30 递减到 20，当前（最后一天）最低
            rows = [(d, 30.0 - i * 0.1, 3.0 - i * 0.01) for i, d in enumerate(days)]
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNotNone(result.pe_percentile)
            self.assertLess(result.pe_percentile, 0.1)
            self.assertIsNotNone(result.pb_percentile)
            self.assertLess(result.pb_percentile, 0.1)
            self.assertEqual(result.config_version, PERCENTILE_CONFIG["version"])
        finally:
            conn.close()

    def test_basic_percentile_high_position(self) -> None:
        """当前 PE 是历史最高 → percentile 接近 1。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(100, "2026-06-19")
            rows = [(d, 20.0 + i * 0.1, 2.0 + i * 0.01) for i, d in enumerate(days)]
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNotNone(result.pe_percentile)
            self.assertGreater(result.pe_percentile, 0.9)
        finally:
            conn.close()

    def test_basic_percentile_mid_position(self) -> None:
        """当前 PE 在中位 → percentile 约 0.5。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(101, "2026-06-19")
            # 前 50 天 PE=10，第 51 天 PE=20（当前），后 50 天 PE=30
            rows = []
            for i, d in enumerate(days):
                if i < 50:
                    rows.append((d, 10.0, 1.0))
                elif i == 50:
                    rows.append((d, 20.0, 2.0))
                else:
                    rows.append((d, 30.0, 3.0))
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNotNone(result.pe_percentile)
            self.assertAlmostEqual(result.pe_percentile, 0.5, delta=0.05)
        finally:
            conn.close()

    def test_negative_pe_returns_warning(self) -> None:
        """当前 PE 为负 → warning + None percentile。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(100, "2026-06-19")
            rows = [(d, 20.0 + i * 0.1, 2.0) for i, d in enumerate(days)]
            # 最后一天 PE 为负
            rows[-1] = (rows[-1][0], -5.0, 2.0)
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNone(result.pe_percentile)
            self.assertIn("negative_pe", result.warnings)
            # PB 仍正常
            self.assertIsNotNone(result.pb_percentile)
        finally:
            conn.close()

    def test_missing_pe_returns_warning(self) -> None:
        """PE 全部为 None → missing_pe warning。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(100, "2026-06-19")
            rows = [(d, None, 2.0 + i * 0.01) for i, d in enumerate(days)]
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNone(result.pe_percentile)
            self.assertIn("missing_pe", result.warnings)
        finally:
            conn.close()

    def test_insufficient_samples_returns_warning(self) -> None:
        """有效样本不足 min_samples → warning + None。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(30, "2026-06-19")  # 只有 30 天 < 60
            rows = [(d, 20.0 + i * 0.1, 2.0 + i * 0.01) for i, d in enumerate(days)]
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            self.assertIsNone(result.pe_percentile)
            self.assertTrue(
                any(w.startswith("insufficient_pe_samples:") for w in result.warnings)
            )
        finally:
            conn.close()

    def test_config_override_min_samples(self) -> None:
        """自定义 min_samples=10 → 30 天样本足够。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(30, "2026-06-19")
            rows = [(d, 20.0 + i * 0.1, 2.0 + i * 0.01) for i, d in enumerate(days)]
            _insert_valuation_rows(conn, "002371", rows)
            custom_config = {
                "version": "test-v1",
                "lookback_days": 1825,
                "min_samples": 10,
                "exclude_non_positive_pe": True,
                "exclude_non_positive_pb": True,
            }
            result = compute_valuation_percentiles(
                conn, "002371", "2026-06-19", config=custom_config
            )
            self.assertIsNotNone(result.pe_percentile)
            self.assertEqual(result.config_version, "test-v1")
        finally:
            conn.close()

    def test_point_in_time_excludes_future_data(self) -> None:
        """trade_date > analysis_date 的行不参与计算。"""
        conn = self._setup_db()
        try:
            # 100 天历史，全部 <= 2026-06-19
            days = _gen_weekdays(100, "2026-06-19")
            rows = [(d, 20.0, 2.0) for d in days]
            # 插入一条 2026-06-20 的数据（未来）
            rows.append(("2026-06-20", 999.0, 999.0))
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            # 当前 PE 应是 20.0（不是 999.0），percentile 应在中间
            self.assertIsNotNone(result.pe_percentile)
            self.assertLess(result.pe_percentile, 0.1)  # 20.0 是最低值
        finally:
            conn.close()

    def test_no_data_returns_missing_warnings(self) -> None:
        """表中无该 code 的数据 → missing_pe + missing_pb。"""
        conn = self._setup_db()
        try:
            result = compute_valuation_percentiles(conn, "999999", "2026-06-19")
            self.assertIsNone(result.pe_percentile)
            self.assertIsNone(result.pb_percentile)
            self.assertIn("missing_pe", result.warnings)
            self.assertIn("missing_pb", result.warnings)
        finally:
            conn.close()

    def test_exclude_non_positive_pe_from_distribution(self) -> None:
        """负 PE 被排除出历史分布，不影响正 PE 的分位计算。"""
        conn = self._setup_db()
        try:
            days = _gen_weekdays(100, "2026-06-19")
            rows = [(d, 20.0 + i * 0.1, 2.0) for i, d in enumerate(days)]
            # 在中间插入一些负 PE
            for i in range(40, 50):
                rows[i] = (rows[i][0], -1.0, 2.0)
            _insert_valuation_rows(conn, "002371", rows)
            result = compute_valuation_percentiles(conn, "002371", "2026-06-19")
            # 当前 PE 是最高正值，负值被排除 → percentile 接近 1
            self.assertIsNotNone(result.pe_percentile)
            self.assertGreater(result.pe_percentile, 0.9)
        finally:
            conn.close()


class RiskFlagsTests(unittest.TestCase):
    def _setup_db(self):
        conn = connect(":memory:")
        init_db(conn)
        return conn

    def _insert_stock(self, conn, code: str, name: str) -> None:
        conn.execute(
            "INSERT INTO stocks (code, name, market, listing_status, as_of_date, "
            "source, fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, 'SH', 'L', '2026-06-19', 'test', 'run1', '2026-06-19', "
            "'2026-06-19', '2026-06-19')",
            (code, name),
        )

    def _insert_snapshot(self, conn, code: str, trade_date: str, close: float = 10.0) -> None:
        conn.execute(
            "INSERT INTO company_daily_snapshot (code, trade_date, close, source, "
            "fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'test', 'run1', '2026-06-19', '2026-06-19', '2026-06-19')",
            (code, trade_date, close),
        )

    def _insert_financial(self, conn, code: str, period_end: str, disclosure: str,
                          net_margin: float = 0.10) -> None:
        conn.execute(
            "INSERT INTO financial_metrics (code, report_period, period_end_date, "
            "disclosure_date, period_type, as_of_date, net_margin, source, "
            "fetch_run_id, source_updated_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'quarterly', '2026-06-19', ?, 'test', 'run1', "
            "'2026-06-19', '2026-06-19', '2026-06-19')",
            (code, "2026Q1", period_end, disclosure, net_margin),
        )

    def test_st_flag(self) -> None:
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "*ST示例")
            self._insert_snapshot(conn, "600001", "2026-06-19")
            flags = compute_company_risk_flags(conn, "600001", "2026-06-19")
            self.assertIn("st", flags)
        finally:
            conn.close()

    def test_delisting_flag(self) -> None:
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "示例退")
            self._insert_snapshot(conn, "600001", "2026-06-19")
            flags = compute_company_risk_flags(conn, "600001", "2026-06-19")
            self.assertIn("delisting_risk", flags)
        finally:
            conn.close()

    def test_suspended_flag(self) -> None:
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "正常")
            # 不插入 snapshot → 停牌
            flags = compute_company_risk_flags(conn, "600001", "2026-06-19")
            self.assertIn("suspended", flags)
        finally:
            conn.close()

    def test_loss_flag(self) -> None:
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "正常")
            self._insert_snapshot(conn, "600001", "2026-06-19")
            self._insert_financial(conn, "600001", "2026-03-31", "2026-04-30", -5.0)
            flags = compute_company_risk_flags(conn, "600001", "2026-06-19")
            self.assertIn("loss", flags)
        finally:
            conn.close()

    def test_no_flags_for_normal_company(self) -> None:
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "正常公司")
            self._insert_snapshot(conn, "600001", "2026-06-19", 10.0)
            self._insert_financial(conn, "600001", "2026-03-31", "2026-04-30", 10.0)
            flags = compute_company_risk_flags(conn, "600001", "2026-06-19")
            self.assertEqual(flags, [])
        finally:
            conn.close()

    def test_loss_flag_point_in_time(self) -> None:
        """analysis_date 早于披露日 → 不读取未披露的亏损数据。"""
        conn = self._setup_db()
        try:
            self._insert_stock(conn, "600001", "正常")
            self._insert_snapshot(conn, "600001", "2026-03-15")
            self._insert_financial(conn, "600001", "2026-03-31", "2026-04-30", -5.0)
            # analysis_date = 2026-03-15，在披露日 2026-04-30 之前
            flags = compute_company_risk_flags(conn, "600001", "2026-03-15")
            self.assertNotIn("loss", flags)
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
