"""Unit tests for the historical snapshot API boundary."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_ROOT.parents[1] / "packages"))

from backend.historical_snapshot_api import (  # noqa: E402
    HistoricalSnapshotApi,
    HistoricalDataUnavailableError,
    HistoricalSnapshotError,
    _build_live_market_context,
    create_historical_snapshot_api,
)
from packages.marketdata.market_data import TencentStockDataProvider  # noqa: E402
from packages.marketdata.provider_result import (  # noqa: E402
    MarketDataResult,
    ProviderIssue,
)
from packages.marketdata.repositories.kline_store import KLineStore  # noqa: E402
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)


class _ObservingProvider:
    """Fake Tencent provider that records every ``get_kline_result`` call.

    Returns empty successful results with a ``replay_reliability_evidence``
    issue so the store records the requested dates as complete.  This is enough
    to prove the real factory-to-provider path was exercised, without needing
    to synthesize a full valid snapshot.
    """

    provider_id = "observing"

    def __init__(self) -> None:
        self.calls: list[
            tuple[str, str, str, str, str | None, str | None]
        ] = []

    def get_kline_result(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        ktype: str = "day",
        autype: str = "qfq",
        market: str | None = None,
        security_type: str | None = None,
    ) -> MarketDataResult[list]:
        self.calls.append((code, start_date, end_date, ktype, market, security_type))
        issue = ProviderIssue(
            level="warning",
            reason_code="replay_reliability_evidence",
            message="test reliability evidence",
            context={
                "default_status": "complete",
                "trade_date_statuses": {end_date: "complete"},
            },
        )
        return MarketDataResult(success=True, data=[], issues=[issue])

    def get_kline(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> list:
        raise AssertionError("get_kline should not be called when get_kline_result exists")


class HistoricalSnapshotApiTests(unittest.TestCase):
    def _market_context(self) -> MarketContextService:
        return MarketContextService(
            trading_days=["2026-07-20", "2026-07-21", "2026-07-22"],
            coverage_start="2026-07-20",
            coverage_end="2026-07-22",
        )

    def test_constructor_requires_market_context_service(self) -> None:
        store = MagicMock(spec=KLineStore)
        provider = MagicMock()
        with self.assertRaises(TypeError):
            HistoricalSnapshotApi(
                service_generation=1,
                store=store,
                provider=provider,
                market_context="not-a-calendar",  # type: ignore[arg-type]
            )

    def test_accepts_injected_market_context(self) -> None:
        store = MagicMock(spec=KLineStore)
        provider = MagicMock()
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=store,
            provider=provider,
            market_context=self._market_context(),
        )
        self.assertEqual(api.service_generation, 1)

    def test_does_not_derive_calendar_from_requested_symbol(self) -> None:
        """The API must rely on the injected market-wide calendar.

        It must never call store.trade_dates for the requested symbol, because a
        suspended symbol can have no bars on an otherwise open market day.
        """

        store = MagicMock(spec=KLineStore)
        provider = MagicMock()
        store.trade_dates.return_value = []

        api = HistoricalSnapshotApi(
            service_generation=1,
            store=store,
            provider=provider,
            market_context=self._market_context(),
        )

        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.return_value = {
                "timezone": "Asia/Shanghai",
                "session": {
                    "session_id": "historical:sh.600000:2026-07-21",
                    "session_type": "historical",
                    "symbol": "sh.600000",
                    "trade_date": "2026-07-21",
                    "state": "ready",
                    "revision": 0,
                },
                "market": {
                    "bars_1m": [],
                    "bars_5m": [],
                    "daily_bars": [],
                    "quote": None,
                },
                "indicators": {
                    "five_minute": {
                        "ma": {
                            "ma5": [],
                            "ma10": [],
                            "ma20": [],
                            "ma30": [],
                            "ma60": [],
                        },
                        "volume": {"values": [], "ma5": [], "ma10": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                    "one_minute": {
                        "vwap": [],
                        "volume": {"values": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                },
                "chan_analysis": {"strokes": [], "pivot_zones": []},
            }
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )

        self.assertTrue(response["accepted"])
        store.trade_dates.assert_not_called()
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        self.assertEqual(kwargs["symbol"], "sh.600000")
        self.assertEqual(kwargs["trade_date"], "2026-07-21")
        self.assertIsInstance(kwargs["market_context"], MarketContextService)

    def test_rejects_invalid_symbol(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="invalid",
            trade_date="2026-07-21",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(response["error"]["error_code"], "invalid_request")
        self.assertEqual(response["error"]["affected_capability"], "historical_chart")

    def test_rejects_invalid_trade_date(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="sh.600000",
            trade_date="not-a-date",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(response["error"]["error_code"], "invalid_request")

    def test_rejects_non_calendar_trade_date(self) -> None:
        """A format-valid but calendar-invalid date must be invalid_request."""
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="sh.600000",
            trade_date="2026-02-30",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(response["error"]["error_code"], "invalid_request")
        self.assertEqual(response["error"]["category"], "validation")
        self.assertFalse(response["error"]["retryable"])

    def test_maps_historical_data_unavailable_error(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.side_effect = HistoricalDataUnavailableError("no data")
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )
        self.assertFalse(response["accepted"])
        self.assertEqual(
            response["error"]["error_code"], "historical_data_unavailable"
        )
        self.assertTrue(response["error"]["retryable"])

    def test_invalid_request_category_is_validation(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="invalid",
            trade_date="2026-07-21",
        )
        self.assertEqual(response["error"]["category"], "validation")

    def test_historical_data_unavailable_category_is_data(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.side_effect = HistoricalDataUnavailableError("no data")
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )
        self.assertEqual(response["error"]["category"], "data")

    def test_service_unavailable_category_is_service(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.side_effect = HistoricalSnapshotError("context failed")
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )
        self.assertEqual(response["error"]["category"], "service")

    def test_unknown_error_category_defaults_to_service(self) -> None:
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.side_effect = RuntimeError("unexpected")
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )
        self.assertEqual(response["error"]["category"], "service")

    def test_date_outside_cached_symbol_range_fetches_from_provider(self) -> None:
        """A market-wide calendar wider than the target symbol's cache must not
        reject the request before the provider can fetch the symbol's bars.
        """
        store = MagicMock(spec=KLineStore)
        provider = MagicMock()
        market_context = MarketContextService(
            trading_days=["2026-07-20", "2026-07-21", "2026-07-22"],
            coverage_start="2026-07-20",
            coverage_end="2026-07-22",
        )
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=store,
            provider=provider,
            market_context=market_context,
        )
        with patch(
            "backend.historical_snapshot_api.build_historical_snapshot"
        ) as mock_build:
            mock_build.return_value = {
                "timezone": "Asia/Shanghai",
                "session": {
                    "session_id": "historical:sh.600000:2026-07-21",
                    "session_type": "historical",
                    "symbol": "sh.600000",
                    "trade_date": "2026-07-21",
                    "state": "ready",
                    "revision": 0,
                },
                "market": {
                    "bars_1m": [],
                    "bars_5m": [],
                    "daily_bars": [],
                    "quote": None,
                },
                "indicators": {
                    "five_minute": {
                        "ma": {
                            "ma5": [],
                            "ma10": [],
                            "ma20": [],
                            "ma30": [],
                            "ma60": [],
                        },
                        "volume": {"values": [], "ma5": [], "ma10": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                    "one_minute": {
                        "vwap": [],
                        "volume": {"values": []},
                        "macd": {
                            "fast_period": 12,
                            "slow_period": 26,
                            "signal_period": 9,
                            "dif": [],
                            "dea": [],
                            "histogram": [],
                        },
                    },
                },
                "chan_analysis": {"strokes": [], "pivot_zones": []},
            }
            response = api.get_historical_snapshot(
                request_id="req-1",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )
        self.assertTrue(response["accepted"])
        mock_build.assert_called_once()


class CreateHistoricalSnapshotApiTests(unittest.TestCase):
    def _clock(self) -> date:
        return date(2026, 7, 22)

    def _insert_bars(self, store: KLineStore, code: str, market: str, bars: list) -> None:
        store.upsert_many(
            code,
            market,
            bars,
            source="test",
            timeframe="day",
        )

    def test_factory_creates_api_with_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            provider = MagicMock(spec=TencentStockDataProvider)
            api = create_historical_snapshot_api(
                service_generation=1,
                db_path=db_path,
                provider=provider,
                clock=self._clock,
            )
            self.assertEqual(api.service_generation, 1)
            # Factory must not perform network I/O during startup.
            provider.get_kline_result.assert_not_called()
            provider.get_kline.assert_not_called()
            # Weekday fallback keeps the API usable.
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-21", "sh")
            )

    def test_factory_uses_benchmark_calendar_when_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            store = KLineStore(db_path)
            bars = [
                {
                    "date": "2026-07-20 00:00:00",
                    "open": 3000.0,
                    "close": 3010.0,
                    "high": 3020.0,
                    "low": 2990.0,
                    "volume": 1000000,
                    "amount": 1_000_000_000.0,
                },
                {
                    "date": "2026-07-21 00:00:00",
                    "open": 3010.0,
                    "close": 3020.0,
                    "high": 3030.0,
                    "low": 3000.0,
                    "volume": 1100000,
                    "amount": 1_100_000_000.0,
                },
            ]
            self._insert_bars(store, "000001", "sh", bars)
            provider = MagicMock(spec=TencentStockDataProvider)
            api = create_historical_snapshot_api(
                service_generation=1,
                db_path=db_path,
                provider=provider,
                clock=self._clock,
            )
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-21", "sh")
            )
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-20", "sh")
            )
            provider.get_kline_result.assert_not_called()

    def test_factory_falls_back_to_all_trade_dates_when_no_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            store = KLineStore(db_path)
            bars = [
                {
                    "date": "2026-07-20 00:00:00",
                    "open": 10.0,
                    "close": 11.0,
                    "high": 12.0,
                    "low": 9.0,
                    "volume": 100000,
                    "amount": 1_000_000.0,
                },
            ]
            self._insert_bars(store, "600000", "sh", bars)
            provider = MagicMock(spec=TencentStockDataProvider)
            api = create_historical_snapshot_api(
                service_generation=1,
                db_path=db_path,
                provider=provider,
                clock=self._clock,
            )
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-20", "sh")
            )
            provider.get_kline_result.assert_not_called()

    def test_factory_falls_back_to_weekday_calendar_when_store_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            provider = MagicMock(spec=TencentStockDataProvider)
            api = create_historical_snapshot_api(
                service_generation=1,
                db_path=db_path,
                provider=provider,
                clock=self._clock,
            )
            # 2026-07-21 is a Tuesday.
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-21", "sh")
            )
            # 2026-07-19 is a Sunday.
            self.assertFalse(
                api._market_context.is_trading_day("2026-07-19", "sh")
            )
            provider.get_kline_result.assert_not_called()

    def test_date_newer_than_cached_benchmark_reaches_provider(self) -> None:
        """A weekday newer than the cached benchmark calendar must not be rejected.

        The provider must be consulted before the request is classified as
        unavailable, even when sh.000001 bars stop before the requested date.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            store = KLineStore(db_path)
            # Benchmark cached only through 2026-07-20 (Monday), inserting only
            # weekdays so the cached dates are valid trading days.
            bars = [
                {
                    "date": f"2026-07-{day:02d} 00:00:00",
                    "open": 3000.0,
                    "close": 3010.0,
                    "high": 3020.0,
                    "low": 2990.0,
                    "volume": 1_000_000,
                    "amount": 1_000_000_000.0,
                }
                for day in range(13, 21)
                if date(2026, 7, day).weekday() < 5
            ]
            self._insert_bars(store, "000001", "sh", bars)
            provider = _ObservingProvider()
            api = create_historical_snapshot_api(
                service_generation=1,
                db_path=db_path,
                provider=provider,
                clock=self._clock,
            )
            # 2026-07-21 is newer than the last cached benchmark bar but is a
            # weekday inside the coverage window, so it must be a potential
            # trading day rather than a holiday.
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-21", "sh")
            )

            response = api.get_historical_snapshot(
                request_id="req-stale-benchmark",
                symbol="sh.600000",
                trade_date="2026-07-21",
            )

            # The provider was consulted (real factory-to-provider path).
            self.assertTrue(provider.calls)
            target_calls = [
                call
                for call in provider.calls
                if call[1] <= "2026-07-21" <= call[2]
            ]
            self.assertTrue(
                target_calls,
                f"expected provider call covering 2026-07-21, got {provider.calls}",
            )
            # Without data the snapshot is unavailable, but the failure must be
            # classified as data (provider returned nothing) not service
            # (calendar rejected the date before the provider ran).
            self.assertFalse(response["accepted"])
            self.assertEqual(
                response["error"]["error_code"],
                "historical_data_unavailable",
            )
            self.assertEqual(response["error"]["category"], "data")


class LiveMarketContextBuilderTests(unittest.TestCase):
    def test_live_builder_does_not_synthesize_weekday_holidays(self) -> None:
        store = MagicMock(spec=KLineStore)
        store.trade_dates.return_value = ["2026-09-29", "2026-09-30"]
        store.all_trade_dates.return_value = ["2026-10-01"]

        context, authoritative_through = _build_live_market_context(
            MagicMock(),
            store,
            date(2026, 10, 2),
        )

        self.assertEqual(authoritative_through, date(2026, 9, 30))
        self.assertTrue(context.is_trading_day("2026-09-30", "sh"))
        self.assertFalse(context.is_trading_day("2026-10-01", "sh"))
        self.assertFalse(context.is_trading_day("2026-10-02", "sh"))
        # Sparse all_trade_dates must not become Live authority.
        store.all_trade_dates.assert_not_called()
        # Coverage still reaches today so Live can classify post-evidence days.
        self.assertEqual(context.coverage_end, date(2026, 10, 2))

    def test_live_builder_empty_cache_is_non_authoritative_scaffold(self) -> None:
        store = MagicMock(spec=KLineStore)
        store.trade_dates.return_value = []
        store.all_trade_dates.return_value = []

        context, authoritative_through = _build_live_market_context(
            MagicMock(),
            store,
            date(2026, 10, 2),
        )

        self.assertIsNone(authoritative_through)
        # Scaffold may contain weekdays for mechanics, but must not be marked
        # authoritative by the Live host (authoritative_through is None).
        self.assertTrue(context.is_trading_day("2026-10-01", "sh"))
        self.assertTrue(context.is_trading_day("2026-10-02", "sh"))


if __name__ == "__main__":
    unittest.main()
