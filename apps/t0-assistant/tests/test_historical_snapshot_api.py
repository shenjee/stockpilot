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


class _FakeIdentity:
    """Minimal fake InstrumentIdentity for resolver tests."""

    def __init__(self, instrument_type: str = "stock") -> None:
        self.instrument_type = instrument_type


def _fake_resolver(instrument_type: str = "stock"):
    """Return a resolve_security callable returning a fake identity."""

    return lambda _symbol: _FakeIdentity(instrument_type)


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
            resolve_security=_fake_resolver("stock"),
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
            resolve_security=_fake_resolver("stock"),
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

    def test_date_outside_calendar_coverage_is_unavailable(self) -> None:
        """A date outside calendar coverage must not fabricate trading days (#133).

        When the year's holiday JSON is missing, the calendar cannot
        authoritatively determine whether a weekday is a trading day.
        The API must return ``historical_data_unavailable`` rather than
        silently treating unknown weekdays as open.
        """
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
        )
        # 2027-01-05 is outside the 2026-07-20..2026-07-22 coverage window.
        response = api.get_historical_snapshot(
            request_id="req-missing-year",
            symbol="sh.600000",
            trade_date="2027-01-05",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(
            response["error"]["error_code"], "historical_data_unavailable"
        )
        self.assertEqual(response["error"]["category"], "data")
        self.assertTrue(response["error"]["retryable"])
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
            resolve_security=_fake_resolver("stock"),
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
            resolve_security=_fake_resolver("stock"),
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

    def test_missing_resolver_rejects_instead_of_fail_open(self) -> None:
        """Issue #151 P2 #5: no resolver must reject, not silently default."""
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
            # resolve_security intentionally omitted
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="sh.600000",
            trade_date="2026-07-21",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(
            response["error"]["error_code"], "service_unavailable"
        )
        self.assertTrue(response["error"]["retryable"])

    def test_resolver_returns_none_rejects_with_security_not_found(self) -> None:
        """Issue #151 P2 #5: unresolved identity must not silently default."""
        api = HistoricalSnapshotApi(
            service_generation=1,
            store=MagicMock(spec=KLineStore),
            provider=MagicMock(),
            market_context=self._market_context(),
            resolve_security=lambda _symbol: None,
        )
        response = api.get_historical_snapshot(
            request_id="req-1",
            symbol="sh.600000",
            trade_date="2026-07-21",
        )
        self.assertFalse(response["accepted"])
        self.assertEqual(
            response["error"]["error_code"], "security_not_found"
        )
        self.assertEqual(response["error"]["category"], "validation")
        self.assertFalse(response["error"]["retryable"])


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
            # Bundled calendar keeps the API usable.
            self.assertTrue(
                api._market_context.is_trading_day("2026-07-21", "sh")
            )

    def test_factory_uses_bundled_calendar_when_cached(self) -> None:
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

    def test_factory_uses_bundled_calendar_when_no_benchmark(self) -> None:
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

    def test_factory_uses_bundled_calendar_when_store_empty(self) -> None:
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

    def test_date_newer_than_cached_bars_reaches_provider(self) -> None:
        """A weekday newer than cached bars must not be rejected.

        The calendar comes from bundled TradingCalendar JSON and covers the
        full year, so a date beyond the last cached bar is still within
        coverage.  The provider must be consulted before the request is
        classified as unavailable.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "market_data.sqlite"
            store = KLineStore(db_path)
            # Cache bars only through 2026-07-20 (Monday), inserting only
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
            # 2026-07-21 is newer than the last cached bar but is a
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
    """Tests for the TradingCalendar-based live market context builder (#133)."""

    def test_live_builder_returns_authoritative_calendar(self) -> None:
        """_build_live_market_context returns a MarketContextService (not a tuple).

        The calendar is built from bundled TradingCalendar JSON, not from
        cached benchmark index dates or weekday scaffolds.
        """

        store = MagicMock(spec=KLineStore)

        result = _build_live_market_context(
            MagicMock(),
            store,
            date(2026, 10, 2),
        )

        # Returns a MarketContextService, not a tuple.
        self.assertIsInstance(result, MarketContextService)
        # National Day holiday (Oct 1-7) is not a trading day.
        self.assertFalse(result.is_trading_day("2026-10-01", "sh"))
        self.assertFalse(result.is_trading_day("2026-10-07", "sh"))
        # Sep 30 is a trading day (not in the holiday range).
        self.assertTrue(result.is_trading_day("2026-09-30", "sh"))
        # No store I/O — calendar JSON is bundled.
        store.trade_dates.assert_not_called()
        store.all_trade_dates.assert_not_called()

    def test_live_and_historical_builders_are_equivalent(self) -> None:
        """Both builders delegate to the same TradingCalendar source (#133)."""

        from backend.historical_snapshot_api import _build_market_context

        store = MagicMock(spec=KLineStore)
        live_ctx = _build_live_market_context(MagicMock(), store, date(2026, 10, 2))
        hist_ctx = _build_market_context(MagicMock(), store, date(2026, 10, 2))

        self.assertEqual(live_ctx.coverage_start, hist_ctx.coverage_start)
        self.assertEqual(live_ctx.coverage_end, hist_ctx.coverage_end)
        self.assertEqual(live_ctx.trading_days, hist_ctx.trading_days)


if __name__ == "__main__":
    unittest.main()
