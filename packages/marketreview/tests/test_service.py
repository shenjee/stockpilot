"""Tests for market review orchestration helpers."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.trading_calendar import TradingCalendar

from packages.marketreview.repository import MarketReviewRepository
from packages.marketreview.service import (
    auto_patch_indices,
    fetch_index_atoms,
    missing_atomic_fields,
    resolve_review_trade_date,
)
from packages.marketreview.validation import resolve_trade_date

_CHINA = ZoneInfo("Asia/Shanghai")


class FakeIndexProvider:
    def __init__(self, rows: dict[tuple[str, str], list[dict]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, str, str | None]] = []

    def get_kline(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        ktype: str = "day",
        market: str | None = None,
        security_type: str | None = None,
    ) -> list[dict]:
        self.calls.append((code, start_date, end_date, security_type))
        return self.rows.get((code, start_date), [])


class TestMarketReviewService(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = TradingCalendar()

    def test_resolve_review_trade_date_defaults_to_latest_closed(self) -> None:
        resolved = resolve_review_trade_date(
            self.calendar,
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        self.assertEqual(resolved, "2026-08-24")

    def test_resolve_review_trade_date_rejects_weekend(self) -> None:
        with self.assertRaises(Exception):
            resolve_trade_date(
                self.calendar,
                requested="2026-08-22",
                now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
            )

    def test_fetch_index_atoms_success(self) -> None:
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": 3200.5}],
                ("000001", "2026-08-20"): [{"close": 3180.25}],
                ("399001", "2026-08-21"): [{"close": 10250.75}],
                ("399001", "2026-08-20"): [{"close": 10100.0}],
                ("399006", "2026-08-21"): [{"close": 2105.4}],
                ("399006", "2026-08-20"): [{"close": 2080.0}],
            }
        )
        result = fetch_index_atoms(provider, self.calendar, "2026-08-21")
        self.assertEqual(result.fields["sh_index_close"], 3200.5)
        self.assertEqual(result.fields["sh_index_prev_close"], 3180.25)
        self.assertEqual(result.failures, ())
        self.assertIn("sh_index_close", result.provenance)
        self.assertEqual(len(provider.calls), 6)
        self.assertTrue(all(call[3] == "index" for call in provider.calls))

    def test_fetch_index_atoms_with_tencent_provider_uses_index_security_type(self) -> None:
        index_closes = {
            ("sh000001", "2026-08-21"): 3200.5,
            ("sh000001", "2026-08-20"): 3180.25,
            ("sz399001", "2026-08-21"): 10250.75,
            ("sz399001", "2026-08-20"): 10100.0,
            ("sz399006", "2026-08-21"): 2105.4,
            ("sz399006", "2026-08-20"): 2080.0,
        }

        def mock_fetch(url: str, decode: str = "utf-8") -> str:
            symbol, _, start_date, end_date, _, autype = url.split("param=")[1].split(",")
            close = index_closes[(symbol, start_date)]
            row = [start_date, str(close - 5), str(close), str(close + 5), str(close - 10), "100", {}, "0.1", "30000.00"]
            return json.dumps({"code": 0, "data": {symbol: {"day": [row]}}})

        with patch.object(
            TencentStockDataProvider,
            "_fetch_with_retry",
            side_effect=mock_fetch,
        ) as fetch:
            result = fetch_index_atoms(
                TencentStockDataProvider,
                self.calendar,
                "2026-08-21",
            )

        self.assertEqual(result.failures, ())
        self.assertEqual(result.fields["sh_index_close"], 3200.5)
        self.assertEqual(result.fields["sh_index_prev_close"], 3180.25)
        self.assertEqual(result.fields["sz_index_close"], 10250.75)
        self.assertEqual(result.fields["cy_index_close"], 2105.4)
        self.assertEqual(len(fetch.call_args_list), 6)
        for call in fetch.call_args_list:
            url = call.args[0]
            self.assertNotIn("qfq", url)

    def test_auto_patch_indices_preserves_existing_on_partial_failure(self) -> None:
        repo = MarketReviewRepository(":memory:")
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": 3200.5}],
                ("000001", "2026-08-20"): [{"close": 3180.25}],
            }
        )
        repo.patch_review(
            "2026-08-21",
            fields={"sz_index_close": 999.0, "sz_index_prev_close": 900.0},
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        result = auto_patch_indices(
            repo,
            provider,
            self.calendar,
            "2026-08-21",
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        view = repo.get_review("2026-08-21")
        assert view is not None
        self.assertEqual(view.atoms.sh_index_close, 3200.5)
        self.assertEqual(view.atoms.sz_index_close, 999.0)
        self.assertIn("cy_index_close", result.failures)
        repo.close()

    def test_fetch_index_atoms_records_invalid_close_without_aborting_batch(self) -> None:
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": "bad"}],
                ("000001", "2026-08-20"): [{"close": 3180.25}],
                ("399001", "2026-08-21"): [{"close": 10250.75}],
                ("399001", "2026-08-20"): [{"close": 10100.0}],
                ("399006", "2026-08-21"): [{"close": float("nan")}],
                ("399006", "2026-08-20"): [{"close": 2080.0}],
            }
        )
        result = fetch_index_atoms(provider, self.calendar, "2026-08-21")
        self.assertNotIn("sh_index_close", result.fields)
        self.assertIn("sz_index_close", result.fields)
        self.assertNotIn("cy_index_close", result.fields)
        self.assertEqual(len(result.failures), 2)
        self.assertTrue(any("sh_index_close" in failure for failure in result.failures))
        self.assertTrue(any("cy_index_close" in failure for failure in result.failures))

    def test_auto_patch_indices_succeeds_when_one_index_invalid(self) -> None:
        repo = MarketReviewRepository(":memory:")
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": "bad"}],
                ("000001", "2026-08-20"): [{"close": 3180.25}],
                ("399001", "2026-08-21"): [{"close": 10250.75}],
                ("399001", "2026-08-20"): [{"close": 10100.0}],
                ("399006", "2026-08-21"): [{"close": 2105.4}],
                ("399006", "2026-08-20"): [{"close": 2080.0}],
            }
        )
        result = auto_patch_indices(
            repo,
            provider,
            self.calendar,
            "2026-08-21",
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        view = repo.get_review("2026-08-21")
        assert view is not None
        self.assertIsNone(view.atoms.sh_index_close)
        self.assertEqual(view.atoms.sz_index_close, 10250.75)
        self.assertEqual(view.atoms.cy_index_close, 2105.4)
        self.assertTrue(any("sh_index_close" in failure for failure in result.failures))
        repo.close()

    def test_auto_patch_indices_preserves_existing_when_prev_close_invalid(self) -> None:
        repo = MarketReviewRepository(":memory:")
        repo.patch_review(
            "2026-08-21",
            fields={"sh_index_close": 3200.5, "sh_index_prev_close": 3180.25},
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": 3333.0}],
                ("000001", "2026-08-20"): [{"close": "bad"}],
                ("399001", "2026-08-21"): [{"close": 10250.75}],
                ("399001", "2026-08-20"): [{"close": 10100.0}],
                ("399006", "2026-08-21"): [{"close": 2105.4}],
                ("399006", "2026-08-20"): [{"close": 2080.0}],
            }
        )
        result = auto_patch_indices(
            repo,
            provider,
            self.calendar,
            "2026-08-21",
            now=datetime(2026, 8, 24, 16, 0, tzinfo=_CHINA),
        )
        view = repo.get_review("2026-08-21")
        assert view is not None
        self.assertEqual(view.atoms.sh_index_close, 3200.5)
        self.assertEqual(view.atoms.sh_index_prev_close, 3180.25)
        self.assertEqual(view.atoms.sz_index_close, 10250.75)
        self.assertEqual(view.atoms.cy_index_close, 2105.4)
        self.assertTrue(any("sh_index_prev_close" in failure for failure in result.failures))
        repo.close()

    def test_fetch_index_atoms_skips_index_when_prev_close_invalid(self) -> None:
        provider = FakeIndexProvider(
            {
                ("000001", "2026-08-21"): [{"close": 3333.0}],
                ("000001", "2026-08-20"): [{"close": "bad"}],
                ("399001", "2026-08-21"): [{"close": 10250.75}],
                ("399001", "2026-08-20"): [{"close": 10100.0}],
            }
        )
        result = fetch_index_atoms(provider, self.calendar, "2026-08-21")
        self.assertNotIn("sh_index_close", result.fields)
        self.assertNotIn("sh_index_prev_close", result.fields)
        self.assertIn("sz_index_close", result.fields)
        self.assertTrue(any("sh_index_prev_close" in failure for failure in result.failures))

    def test_missing_atomic_fields_includes_index_fields(self) -> None:
        repo = MarketReviewRepository(":memory:")
        missing = missing_atomic_fields(repo, "2026-08-21")
        self.assertIn("sh_index_close", missing)
        self.assertIn("cy_index_prev_close", missing)
        repo.close()

    def test_missing_atomic_fields(self) -> None:
        repo = MarketReviewRepository(":memory:")
        repo.patch_review("2026-08-21", fields={"effective_limit_up": 10})
        missing = missing_atomic_fields(repo, "2026-08-21")
        self.assertIn("closed_limit_down", missing)
        self.assertIn("ladder_snapshot", missing)
        self.assertNotIn("effective_limit_up", missing)
        repo.close()


if __name__ == "__main__":
    unittest.main()
