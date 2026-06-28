"""Snapshot-service orchestration tests for Fundamental Screener."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from packages.fundamentalscreener.snapshot_service import (
    load_latest_snapshot,
    refresh_market_data,
    refresh_sector_detail_snapshot,
)
from packages.fundamentalscreener.sqlite_schema import connect, init_db


def _populate_minimal_sqlite(conn, analysis_date: str = "2026-06-19") -> None:
    """Populate enough SQLite data to assemble a non-invalid snapshot."""

    days = []
    current = date.fromisoformat(analysis_date)
    while len(days) < 65:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
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

    for sid, code in [
        ("BK0001", "002371"),
        ("BK0001", "600584"),
        ("BK0002", "000001"),
    ]:
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

    for code, name in [
        ("002371", "北方华创"),
        ("600584", "长电科技"),
        ("000001", "平安银行"),
    ]:
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


class _FakeRefreshSource:
    def __init__(self, fail: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.empty = empty
        self.name = "akshare_ths"

    def _gen_days(self, n: int, end_iso: str = "2026-06-19"):
        days = []
        current = date.fromisoformat(end_iso)
        while len(days) < n:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current -= timedelta(days=1)
        return list(reversed(days))

    def list_sectors(self, classification_system: str):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        return [
            {
                "sector_id": "BK0001",
                "sector_name": "半导体",
                "classification_system": classification_system,
                "source_updated_at": "2026-06-19",
            },
            {
                "sector_id": "BK0002",
                "sector_name": "工程机械",
                "classification_system": classification_system,
                "source_updated_at": "2026-06-19",
            },
        ]

    def get_sector_constituents(self, sector_id, classification_system, as_of_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        return [
            {
                "sector_id": sector_id,
                "classification_system": classification_system,
                "code": "002371",
                "as_of_date": as_of_date,
                "source_updated_at": as_of_date,
            },
            {
                "sector_id": sector_id,
                "classification_system": classification_system,
                "code": "600584",
                "as_of_date": as_of_date,
                "source_updated_at": as_of_date,
            },
        ]

    def get_sector_daily(self, sector_id, classification_system, start_date, end_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        days = self._gen_days(65, end_date)
        return [
            {
                "sector_id": sector_id,
                "classification_system": classification_system,
                "trade_date": day,
                "close": 100.0 + index,
                "turnover_amount": 1e9,
                "source_updated_at": day,
            }
            for index, day in enumerate(days)
        ]

    def get_benchmark_daily(self, benchmark, start_date, end_date):
        if self.fail:
            raise RuntimeError("fake network failure")
        if self.empty:
            return []
        days = self._gen_days(65, end_date)
        return [
            {
                "benchmark": benchmark,
                "trade_date": day,
                "close": 3500.0 + index,
                "turnover_amount": 1e11,
                "source_updated_at": day,
            }
            for index, day in enumerate(days)
        ]

    def get_stock_universe(self, as_of_date):
        if self.fail or self.empty:
            return []
        return [
            {
                "code": "002371",
                "name": "北方华创",
                "market": "SZ",
                "listing_status": "L",
                "as_of_date": as_of_date,
                "source_updated_at": as_of_date,
            },
            {
                "code": "600584",
                "name": "长电科技",
                "market": "SH",
                "listing_status": "L",
                "as_of_date": as_of_date,
                "source_updated_at": as_of_date,
            },
        ]

    def get_company_daily_snapshot(self, trade_date, codes=None):
        if self.fail or self.empty:
            return []
        rows = [
            {
                "code": "002371",
                "trade_date": trade_date,
                "close": 10.0,
                "turnover_amount": 1e8,
                "turnover_rate": 0.02,
                "market_cap": 1e10,
                "source_updated_at": trade_date,
            },
            {
                "code": "600584",
                "trade_date": trade_date,
                "close": 20.0,
                "turnover_amount": 2e8,
                "turnover_rate": 0.01,
                "market_cap": 2e10,
                "source_updated_at": trade_date,
            },
        ]
        if codes is None:
            return rows
        wanted = set(codes)
        return [row for row in rows if row["code"] in wanted]

    def get_company_valuation_history(self, codes, start_date, end_date):
        return []

    def get_financial_metrics(self, codes, as_of_date):
        return []


class _FakeRefreshSourceDetailFail(_FakeRefreshSource):
    def get_sector_constituents(self, sector_id, classification_system, as_of_date):
        raise RuntimeError("fake constituents failure")


class _FakeRefreshSourceUniverseFail(_FakeRefreshSource):
    def get_stock_universe(self, as_of_date):
        raise RuntimeError("fake universe failure")


class ApplicationLoadTests(unittest.TestCase):
    def test_load_latest_snapshot_returns_status_from_package_layer(self) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            conn = connect(db_path)
            try:
                init_db(conn)
                _populate_minimal_sqlite(conn)
            finally:
                conn.close()

            result = load_latest_snapshot(
                db_path=db_path,
                analysis_date=None,
                classification_system="ths_industry",
                benchmark="hs300",
            )
            self.assertIn(result.status, ("ok", "degraded", "stale"))
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.snapshot.sectors)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class ApplicationRefreshTests(unittest.TestCase):
    def test_refresh_with_old_cache_returns_refresh_failed(self) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSource(),
            )
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSource(fail=True),
            )
            self.assertEqual(result.status, "refresh_failed")
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.reason)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_non_light_failure_does_not_downgrade_first_screen(self) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            result = refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSourceUniverseFail(),
            )
            self.assertIn(result.status, ("ok", "degraded", "stale"))
            self.assertIsNotNone(result.snapshot)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_default_source_construction_failure_with_old_cache_returns_refresh_failed(
        self,
    ) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSource(),
            )
            with patch(
                "packages.fundamentalscreener.snapshot_service.build_default_source",
                side_effect=RuntimeError("source bootstrap failure"),
            ):
                result = refresh_market_data(
                    db_path=db_path,
                    analysis_date="2026-06-19",
                    classification_system="ths_industry",
                    benchmark="hs300",
                    history_days=90,
                )
            self.assertEqual(result.status, "refresh_failed")
            self.assertIsNotNone(result.snapshot)
            self.assertIn("source bootstrap failure", result.reason)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class ApplicationDetailRefreshTests(unittest.TestCase):
    def test_detail_failure_without_cached_companies_returns_no_cache(self) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            refresh_market_data(
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSource(),
            )
            result = refresh_sector_detail_snapshot(
                "BK0001",
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSourceDetailFail(),
            )
            self.assertEqual(result.status, "no_cache")
            self.assertFalse(result.has_company_data)
            self.assertTrue(result.reason)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_detail_failure_with_cached_companies_returns_refresh_failed(self) -> None:
        db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(db_fd)
        try:
            refresh_sector_detail_snapshot(
                "BK0001",
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSource(),
            )
            result = refresh_sector_detail_snapshot(
                "BK0001",
                db_path=db_path,
                analysis_date="2026-06-19",
                classification_system="ths_industry",
                benchmark="hs300",
                history_days=90,
                source=_FakeRefreshSourceDetailFail(),
            )
            self.assertEqual(result.status, "refresh_failed")
            self.assertTrue(result.has_company_data)
            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.reason)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
