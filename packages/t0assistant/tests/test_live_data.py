from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import (
    LiveDataPreparator,
    LiveDataUnavailableError,
    LivePreparationConfig,
    SessionSpec,
    SessionType,
)


def _bar(
    timestamp: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    amount: float,
    *,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "date": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


class _FakeMarketData:
    def __init__(self, store: dict[tuple[str, str | None], list[dict[str, Any]]]) -> None:
        self.store = store
        self.calls: list[dict[str, Any]] = []

    def get_klines_result(
        self,
        code: str,
        end_date: str,
        *,
        market: str | None = None,
        timeframe: str,
        start_date: str | None = None,
        limit: int = 120,
        request_priority=None,
        session_validator=None,
        request_timeout: float | None = None,
    ) -> object:
        self.calls.append(
            {
                "code": code,
                "end_date": end_date,
                "market": market,
                "timeframe": timeframe,
                "start_date": start_date,
                "limit": limit,
                "validator": session_validator,
            }
        )
        rows = list(self.store.get((timeframe, start_date), ()))
        return SimpleNamespace(data=rows)


class _FakeQuoteReader:
    def __init__(self, payload: dict[str, Any] | BaseException | None) -> None:
        self.payload = payload
        self.calls: list[tuple[object, object]] = []

    def realtime_result(self, codes, markets=None) -> object:
        self.calls.append((codes, markets))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return SimpleNamespace(data=self.payload)


class LiveDataPreparatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market_context = MarketContextService(
            ["2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-22",
            coverage_end="2026-07-24",
        )
        self.spec = SessionSpec(
            session_id="live-1",
            session_type=SessionType.LIVE,
            symbol="sh.600000",
            generation=1,
        )

    def test_prepare_builds_first_live_warmup_and_forwards_session_validator(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 14:45:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                    _bar("2026-07-23 14:50:00", 10.02, 10.08, 10.0, 10.05, 120, 1206),
                    _bar("2026-07-23 14:55:00", 10.05, 10.1, 10.0, 10.08, 140, 1411.2),
                    _bar("2026-07-23 15:00:00", 10.08, 10.12, 10.04, 10.1, 160, 1616),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 14:45:00", 9.8, 9.9, 9.7, 9.82, 100, 982),
                    _bar("2026-07-22 14:50:00", 9.82, 9.95, 9.8, 9.9, 120, 1188),
                    _bar("2026-07-22 14:55:00", 9.9, 10.0, 9.88, 9.96, 140, 1394.4),
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-22", 9.8, 10.0, 9.7, 10.0, 5200, 52000),
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        quote_reader = _FakeQuoteReader(
            {
                "timestamp": "2026-07-24 09:31:30",
                "latest_price": 10.13,
                "change_percent": 0.297,
                "open": 10.1,
                "high": 10.13,
                "low": 10.05,
                "previous_close": 10.1,
                "volume": 120.0,
                "amount": 1215.6,
                "volume_ratio": 1.2,
                "order_imbalance": None,
                "turnover_rate": 0.03,
            }
        )
        validator_calls = []
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=quote_reader,
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
            session_validator_factory=lambda spec: lambda: validator_calls.append(
                spec.session_id
            )
            or True,
            config=LivePreparationConfig(
                daily_history_days=10,
                intraday_limit=20,
            ),
        )

        prepared = preparator.prepare(self.spec, minimum_preheat_5m=6)
        market_input = prepared.market_input_port.read(prepared.target_time)

        self.assertEqual(prepared.market_session.trade_date.isoformat(), "2026-07-24")
        self.assertEqual(prepared.target_time, datetime(2026, 7, 24, 9, 31, 30))
        self.assertEqual(len(market_input.preheat_5m_bars), 6)
        self.assertEqual(
            [bar["timestamp"] for bar in market_input.preheat_5m_bars],
            [
                "2026-07-22 14:55:00",
                "2026-07-22 15:00:00",
                "2026-07-23 14:45:00",
                "2026-07-23 14:50:00",
                "2026-07-23 14:55:00",
                "2026-07-23 15:00:00",
            ],
        )
        self.assertEqual(market_input.previous_close, 10.1)
        self.assertEqual(market_input.bars_1m[-1]["timestamp"], "2026-07-24 09:31:00")
        self.assertEqual(
            market_input.quote_snapshots[-1]["timestamp"],
            "2026-07-24 09:31:30",
        )
        self.assertEqual(quote_reader.calls, [("600000", ["sh"])])

        validators = [call["validator"] for call in market_data.calls]
        self.assertTrue(validators)
        self.assertTrue(all(callable(validator) for validator in validators))
        for validator in validators:
            self.assertTrue(validator())
        preheat_calls = [
            call
            for call in market_data.calls
            if call["timeframe"] == "5m" and call["start_date"] != "2026-07-24"
        ]
        self.assertTrue(preheat_calls)
        self.assertTrue(all(call["limit"] == 6 for call in preheat_calls))
        self.assertEqual(
            validator_calls,
            [
                "live-1",
                "live-1",
                "live-1",
                "live-1",
                "live-1",
            ],
        )

    def test_refresh_reads_only_requested_normalized_branch(self) -> None:
        market_data = _FakeMarketData(
            {
                ("1m", "2026-07-24"): [
                    _bar(
                        "2026-07-24 09:32:00",
                        10.1,
                        10.2,
                        10.0,
                        10.15,
                        100,
                        1015,
                    )
                ],
                ("5m", "2026-07-24"): [
                    _bar(
                        "2026-07-24 09:35:00",
                        10.1,
                        10.2,
                        10.0,
                        10.15,
                        500,
                        5075,
                    )
                ],
            }
        )
        quote_reader = _FakeQuoteReader(
            {
                "timestamp": "2026-07-24 09:32:10",
                "latest_price": 10.16,
                "change_percent": 0.6,
                "open": 10.1,
                "high": 10.2,
                "low": 10.0,
                "previous_close": 10.1,
                "volume": 200,
                "amount": 2032,
                "volume_ratio": 1.1,
                "order_imbalance": None,
                "turnover_rate": 0.02,
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=quote_reader,
            clock=lambda: datetime(2026, 7, 24, 9, 32, 10),
        )

        one_minute = preparator.load_refresh_bars(self.spec, timeframe="1m")
        self.assertEqual(one_minute[0]["timestamp"], "2026-07-24 09:32:00")
        self.assertEqual([call["timeframe"] for call in market_data.calls], ["1m"])

        quotes = preparator.load_refresh_quotes(self.spec)
        self.assertEqual(quotes[0]["timestamp"], "2026-07-24 09:32:10")
        self.assertEqual([call["timeframe"] for call in market_data.calls], ["1m"])
        self.assertEqual(quote_reader.calls, [("600000", ["sh"])])

    def test_refresh_quote_failure_is_visible_to_independent_scheduler(self) -> None:
        preparator = LiveDataPreparator(
            _FakeMarketData({}),
            self.market_context,
            quote_reader=_FakeQuoteReader(RuntimeError("quote unavailable")),
            clock=lambda: datetime(2026, 7, 24, 9, 32, 10),
        )

        with self.assertRaisesRegex(RuntimeError, "quote unavailable"):
            preparator.load_refresh_quotes(self.spec)

    def test_prepare_uses_observed_now_when_quote_is_unavailable(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        prepared = preparator.prepare(self.spec, minimum_preheat_5m=2)
        self.assertEqual(prepared.target_time, datetime(2026, 7, 24, 9, 31, 30))

    def test_prepare_fails_when_current_day_has_neither_quote_nor_intraday_data(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        with self.assertRaisesRegex(
            LiveDataUnavailableError,
            "requires a quote or intraday bars",
        ):
            preparator.prepare(self.spec, minimum_preheat_5m=2)

    def test_prepare_ignores_quote_outside_current_trade_date(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        quote_reader = _FakeQuoteReader(
            {
                "timestamp": "2026-07-23 15:00:00",
                "latest_price": 10.13,
                "change_percent": 0.297,
                "open": 10.1,
                "high": 10.13,
                "low": 10.05,
                "previous_close": 10.1,
                "volume": 120.0,
                "amount": 1215.6,
                "volume_ratio": 1.2,
                "order_imbalance": None,
                "turnover_rate": 0.03,
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=quote_reader,
            clock=lambda: datetime(2026, 7, 24, 9, 0, 0),
        )

        with self.assertRaisesRegex(
            LiveDataUnavailableError,
            "requires a quote or intraday bars",
        ):
            preparator.prepare(self.spec, minimum_preheat_5m=2)

    def test_prepare_skips_calendar_days_without_market_data(self) -> None:
        market_context = MarketContextService(
            ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"],
            coverage_start="2026-07-21",
            coverage_end="2026-07-24",
        )
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 14:55:00", 9.9, 10.0, 9.8, 9.95, 100, 995),
                    _bar("2026-07-22 15:00:00", 9.95, 10.02, 9.9, 10.0, 120, 1200),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        prepared = preparator.prepare(self.spec, minimum_preheat_5m=2)
        market_input = prepared.market_input_port.read(prepared.target_time)

        self.assertEqual(
            [bar["timestamp"] for bar in market_input.preheat_5m_bars],
            ["2026-07-22 14:55:00", "2026-07-22 15:00:00"],
        )
        requested_days = [call["start_date"] for call in market_data.calls if call["timeframe"] == "5m"]
        self.assertEqual(requested_days[:2], ["2026-07-23", "2026-07-22"])

    def test_prepare_backfills_until_effective_preheat_count_reaches_minimum(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                    {"date": "2026-07-23 14:50:00"},
                    _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 100, 1002),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 14:55:00", 9.9, 10.0, 9.88, 9.96, 140, 1394.4),
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        prepared = preparator.prepare(self.spec, minimum_preheat_5m=3)
        market_input = prepared.market_input_port.read(prepared.target_time)

        self.assertEqual(
            [bar["timestamp"] for bar in market_input.preheat_5m_bars],
            [
                "2026-07-22 14:55:00",
                "2026-07-22 15:00:00",
                "2026-07-23 14:55:00",
            ],
        )
        requested_days = [
            call["start_date"]
            for call in market_data.calls
            if call["timeframe"] == "5m"
        ]
        self.assertEqual(requested_days[:2], ["2026-07-23", "2026-07-22"])

    def test_prepare_skips_non_closed_preheat_bars_when_counting_minimum(self) -> None:
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar(
                        "2026-07-23 14:50:00",
                        10.0,
                        10.1,
                        9.9,
                        10.02,
                        100,
                        1002,
                        closed=False,
                    ),
                    _bar("2026-07-23 14:55:00", 10.02, 10.08, 10.0, 10.05, 120, 1206),
                ],
                ("5m", "2026-07-22"): [
                    _bar("2026-07-22 14:55:00", 9.9, 10.0, 9.88, 9.96, 140, 1394.4),
                    _bar("2026-07-22 15:00:00", 9.96, 10.0, 9.94, 10.0, 160, 1600),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            self.market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        prepared = preparator.prepare(self.spec, minimum_preheat_5m=2)
        market_input = prepared.market_input_port.read(prepared.target_time)

        self.assertEqual(
            [bar["timestamp"] for bar in market_input.preheat_5m_bars],
            ["2026-07-22 15:00:00", "2026-07-23 14:55:00"],
        )
        requested_days = [
            call["start_date"]
            for call in market_data.calls
            if call["timeframe"] == "5m"
        ]
        self.assertEqual(requested_days[:2], ["2026-07-23", "2026-07-22"])

    def test_prepare_fails_when_calendar_coverage_exhausted_before_minimum_preheat(self) -> None:
        market_context = MarketContextService(
            ["2026-07-23", "2026-07-24"],
            coverage_start="2026-07-23",
            coverage_end="2026-07-24",
        )
        market_data = _FakeMarketData(
            {
                ("5m", "2026-07-23"): [
                    _bar("2026-07-23 15:00:00", 10.08, 10.12, 10.04, 10.1, 160, 1616),
                ],
                ("1m", "2026-07-24"): [
                    _bar("2026-07-24 09:31:00", 10.1, 10.12, 10.05, 10.11, 80, 808.8),
                ],
                ("day", None): [
                    _bar("2026-07-23", 10.0, 10.1, 9.9, 10.1, 5400, 54540),
                ],
            }
        )
        preparator = LiveDataPreparator(
            market_data,
            market_context,
            quote_reader=_FakeQuoteReader(None),
            clock=lambda: datetime(2026, 7, 24, 9, 31, 30),
        )

        with self.assertRaisesRegex(
            LiveDataUnavailableError,
            "requires at least 2 closed 5m preheat bars",
        ):
            preparator.prepare(self.spec, minimum_preheat_5m=2)


if __name__ == "__main__":
    unittest.main()
