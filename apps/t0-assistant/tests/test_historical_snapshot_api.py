"""Unit tests for the historical snapshot API boundary."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_ROOT.parents[1] / "packages"))

from backend.historical_snapshot_api import (  # noqa: E402
    HistoricalSnapshotApi,
    HistoricalDataUnavailableError,
)
from packages.marketdata.repositories.kline_store import KLineStore  # noqa: E402
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)


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


if __name__ == "__main__":
    unittest.main()
