"""Focused #165 regressions: OHLC-ready Replay with nullable volume/amount."""

from __future__ import annotations

import unittest

from packages.t0assistant.replay.api import (
    ReplayDeliveryChannel,
    map_replay_prepare_error_to_replay_error,
)
from packages.t0assistant.runtime._market_bars import aggregate_ohlcva
from packages.t0assistant.runtime.pipeline import WorkbenchPipeline
from packages.t0assistant.runtime.replay_data import (
    ReplayDataInvalidError,
    ReplayDataPreparator,
    ReplayDataUnavailableError,
    ReplayPreparationConfig,
)
from packages.t0assistant.tests.fixtures.replay_fixtures import (
    SYMBOL,
    TRADE_DATE,
    amount_unavailable_replay,
    market_context_service,
    one_minute_replay,
    partial_quantity_gap_replay,
    volume_unavailable_replay,
)
from packages.t0assistant.tests.test_replay_data import (
    FakeMarketDataPort,
    _populate_from_fixture,
)


class NullableQuantityReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = market_context_service()

    def _prepare(self, fixture):
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        return ReplayDataPreparator(port, self.context).prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )

    def test_volume_null_does_not_block_or_degrade_replay(self) -> None:
        prepared = self._prepare(volume_unavailable_replay())
        self.assertEqual(prepared.granularity, "one_minute")
        self.assertTrue(all(bar["volume"] is None for bar in prepared.bars_1m))
        self.assertTrue(all(isinstance(bar["open"], (int, float)) for bar in prepared.bars_1m))
        self.assertNotEqual(prepared.bars_1m[0]["amount"], 0)

        result = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
        ).preview(prepared.market_session.end)
        self.assertGreater(len(result.bars_1m), 0)
        self.assertTrue(all(point["value"] is None for point in result.indicators_1m["volume"]["values"]))
        self.assertTrue(all(point["value"] is None for point in result.indicators_1m["vwap"]))
        self.assertGreater(len(result.indicators_1m["macd"]["dif"]), 0)
        self.assertGreater(len(result.indicators_5m["boll"]["middle"]), 0)

    def test_amount_null_does_not_fabricate_zero(self) -> None:
        prepared = self._prepare(amount_unavailable_replay())
        self.assertEqual(prepared.granularity, "one_minute")
        self.assertTrue(all(bar["amount"] is None for bar in prepared.bars_1m))
        self.assertTrue(all(bar["amount"] is None for bar in prepared.official_5m_bars))
        self.assertTrue(all(bar["amount"] is None for bar in prepared.preheat_5m_bars))

    def test_partial_quantity_gap_keeps_one_minute_granularity(self) -> None:
        prepared = self._prepare(partial_quantity_gap_replay())
        self.assertEqual(prepared.granularity, "one_minute")
        null_bars = [
            bar
            for bar in prepared.bars_1m
            if bar["volume"] is None or bar["amount"] is None
        ]
        self.assertEqual(len(null_bars), 1)

        result = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
        ).preview(prepared.market_session.end)
        self.assertIsNone(result.quote["volume"])
        self.assertIsNone(result.quote["amount"])

    def test_invalid_1m_with_legal_5m_degrades(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad["high"] = bad["low"] - 1
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad

        prepared = ReplayDataPreparator(port, self.context).prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(prepared.bars_1m, ())
        self.assertGreater(len(prepared.official_5m_bars), 0)

    def test_missing_1m_timestamp_degrades_to_legal_5m(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad.pop("timestamp", None)
        bad.pop("date", None)
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad

        prepared = ReplayDataPreparator(port, self.context).prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(prepared.bars_1m, ())
        self.assertGreater(len(prepared.official_5m_bars), 0)

    def test_malformed_1m_timestamp_degrades_to_legal_5m(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad["timestamp"] = "not-a-timestamp"
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad

        prepared = ReplayDataPreparator(port, self.context).prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(prepared.bars_1m, ())
        self.assertGreater(len(prepared.official_5m_bars), 0)

    def test_missing_1m_timestamp_without_5m_is_replay_data_invalid(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad.pop("timestamp", None)
        bad.pop("date", None)
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad
        port.store[("5m", TRADE_DATE.isoformat())] = []
        port.missing_overrides[("5m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]

        with self.assertRaises(ReplayDataInvalidError) as raised:
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )
        self.assertEqual(raised.exception.details["timeframe"], "1m")
        self.assertEqual(raised.exception.details["affected_field"], "timestamp")
        self.assertEqual(raised.exception.details["invalid_count"], 1)

    def test_malformed_1m_timestamp_without_5m_is_replay_data_invalid(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad["timestamp"] = "2026/07/24 09:31:00"
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad
        port.store[("5m", TRADE_DATE.isoformat())] = []
        port.missing_overrides[("5m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]

        with self.assertRaises(ReplayDataInvalidError) as raised:
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )
        self.assertEqual(raised.exception.details["timeframe"], "1m")
        self.assertEqual(raised.exception.details["affected_field"], "timestamp")
        self.assertEqual(raised.exception.details["invalid_count"], 1)
        self.assertEqual(
            raised.exception.details["timestamp"], "2026/07/24 09:31:00"
        )

    def test_invalid_1m_without_reliable_5m_raises_replay_data_invalid(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bad = dict(port.store[("1m", TRADE_DATE.isoformat())][0])
        bad["high"] = bad["low"] - 1
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad
        port.store[("5m", TRADE_DATE.isoformat())] = []
        port.missing_overrides[("5m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]

        with self.assertRaises(ReplayDataInvalidError) as raised:
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )
        self.assertEqual(raised.exception.details["timeframe"], "1m")
        self.assertEqual(raised.exception.details["affected_field"], "high")
        self.assertEqual(raised.exception.details["invalid_count"], 1)

    def test_both_granularities_invalid_raises_replay_data_invalid(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        for timeframe in ("1m", "5m"):
            bad = dict(port.store[(timeframe, TRADE_DATE.isoformat())][0])
            bad["high"] = bad["low"] - 1
            port.store[(timeframe, TRADE_DATE.isoformat())][0] = bad

        with self.assertRaises(ReplayDataInvalidError) as raised:
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )
        self.assertEqual(raised.exception.details["timeframe"], "1m")
        self.assertEqual(raised.exception.details["affected_field"], "high")
        self.assertEqual(raised.exception.details["invalid_count"], 1)

    def test_invalid_details_count_all_illegal_bars(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bars = port.store[("1m", TRADE_DATE.isoformat())]
        first = dict(bars[0])
        first["high"] = first["low"] - 1
        second = dict(bars[1])
        second["high"] = -1.0
        port.store[("1m", TRADE_DATE.isoformat())][0] = first
        port.store[("1m", TRADE_DATE.isoformat())][1] = second
        port.store[("5m", TRADE_DATE.isoformat())] = []
        port.missing_overrides[("5m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]

        with self.assertRaises(ReplayDataInvalidError) as raised:
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )
        self.assertEqual(raised.exception.details["timeframe"], "1m")
        self.assertEqual(raised.exception.details["affected_field"], "high")
        self.assertEqual(raised.exception.details["invalid_count"], 2)

    def test_both_granularities_missing_raises_price_unavailable(self) -> None:
        port = FakeMarketDataPort()
        for timeframe in ("1m", "5m"):
            port.missing_overrides[(timeframe, TRADE_DATE.isoformat())] = [
                (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
            ]
        with self.assertRaises(ReplayDataUnavailableError):
            ReplayDataPreparator(port, self.context).prepare(
                SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
            )

    def test_price_unavailable_still_maps_to_new_error_code(self) -> None:
        error, channel = map_replay_prepare_error_to_replay_error(
            ReplayDataUnavailableError("neither")
        )
        self.assertEqual(error.error_code, "replay_price_data_unavailable")
        self.assertEqual(channel, ReplayDeliveryChannel.ASYNCHRONOUS)
        self.assertNotEqual(error.error_code, "service_unavailable")

    def test_aggregate_ohlcva_propagates_null_quantity(self) -> None:
        bars = [
            {
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": None,
            },
            {
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.2,
                "volume": 50,
                "amount": 500.0,
            },
        ]
        aggregated = aggregate_ohlcva(
            "2026-07-24 09:35:00",
            bars,
            closed=False,
        )
        self.assertEqual(aggregated["volume"], 150)
        self.assertIsNone(aggregated["amount"])
        self.assertEqual(aggregated["open"], 10.0)
        self.assertEqual(aggregated["close"], 10.2)


if __name__ == "__main__":
    unittest.main()
