"""Tests for the shared Live/Replay Workbench Pipeline.

These tests use deterministic fakes for clock, market input, and CZSC analysis
so that the pipeline can be exercised without providers, SQLite, or the czsc
engine.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import (
    ClockPort,
    CzscAnalyzerPort,
    MarketInputPort,
    PipelineMarketInput,
    PipelineResult,
    WorkbenchPipeline,
    WorkbenchPipelineError,
)


_TRADE_DATE = "2026-07-24"
_MARKET = "sh"
_SYMBOL = "sh.600000"


def bar(
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
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


class _FixedClock:
    """A fake clock that always returns the configured target time."""

    def __init__(self, target_time: datetime | str) -> None:
        if isinstance(target_time, str):
            target_time = datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
        self._target_time = target_time

    def now(self) -> datetime:
        return self._target_time


class _RecordingMarketInputPort:
    """A fake market input port that records every read request."""

    def __init__(
        self,
        inputs: dict[datetime | str, PipelineMarketInput],
    ) -> None:
        self._inputs: dict[datetime, PipelineMarketInput] = {}
        for key, value in inputs.items():
            if isinstance(key, str):
                key = datetime.strptime(key, "%Y-%m-%d %H:%M:%S")
            self._inputs[key] = value
        self.requests: list[datetime] = []

    def read(self, target_time: datetime) -> PipelineMarketInput:
        self.requests.append(target_time)
        return self._inputs[target_time]


class _RecordingAnalyzer:
    """A fake CZSC analyzer that records the closed 5m prefix it receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    def __call__(
        self,
        bars: Sequence[Mapping[str, Any]],
        symbol: str,
    ) -> dict[str, Any]:
        copied = [dict(b) for b in bars]
        self.calls.append((symbol, copied))
        return {
            "symbol": symbol,
            "closed_bar_count": len(copied),
            "dynamic_detected": any(not b.get("closed") for b in copied),
        }


class _TimestampOnlyPoisonRow(Mapping):
    """A future input row that fails if any market value is read."""

    def __init__(self, timestamp: str) -> None:
        self.timestamp = timestamp

    def __getitem__(self, key: str) -> Any:
        if key == "timestamp":
            return self.timestamp
        raise AssertionError(f"future field was read: {key}")

    def __iter__(self):
        return iter(("timestamp",))

    def __len__(self) -> int:
        return 1

    def get(self, key: str, default: Any = None) -> Any:
        if key == "timestamp":
            return self.timestamp
        if key == "date":
            return default
        raise AssertionError(f"future field was read: {key}")


class _BasePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        calendar = MarketContextService([_TRADE_DATE, "2026-07-23"])
        self.session = calendar.require_session(_TRADE_DATE, _MARKET)

        self.preheat = [
            bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.05, 5000, 50500),
            bar("2026-07-23 15:00:00", 10.05, 10.2, 10.0, 10.15, 6000, 61200),
        ]
        self.bars_1m = [
            bar("2026-07-24 09:31:00", 10.2, 10.3, 10.15, 10.25, 100, 1025),
            bar("2026-07-24 09:32:00", 10.25, 10.4, 10.2, 10.35, 200, 2070),
            bar("2026-07-24 09:33:00", 10.35, 10.45, 10.3, 10.4, 150, 1560),
        ]
        self.official_5m = [
            bar("2026-07-24 09:35:00", 10.2, 10.45, 10.15, 10.4, 450, 4655),
        ]
        self.target_time = datetime.strptime("2026-07-24 09:33:00", "%Y-%m-%d %H:%M:%S")

        self.market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=[],
            quote_snapshots=[],
        )

    def _make_pipeline(
        self,
        market_input: PipelineMarketInput | None = None,
        target_time: datetime | str | None = None,
        analyzer: CzscAnalyzerPort | None = None,
    ) -> WorkbenchPipeline:
        if market_input is None:
            market_input = self.market_input
        if target_time is None:
            target_time = self.target_time
        port = _RecordingMarketInputPort({target_time: market_input})
        clock = _FixedClock(target_time)
        return WorkbenchPipeline(
            session=self.session,
            market_input_port=port,
            clock_port=clock,
            analyzer=analyzer,
        )


class PortDrivenTests(_BasePipelineTests):
    def test_fake_clock_controls_target_time(self) -> None:
        pipeline = self._make_pipeline()
        result = pipeline.step()

        self.assertEqual(result.target_time, self.target_time)
        self.assertEqual(pipeline.target_time, self.target_time)

    def test_fake_market_input_port_records_requests(self) -> None:
        port = _RecordingMarketInputPort({self.target_time: self.market_input})
        pipeline = WorkbenchPipeline(
            session=self.session,
            market_input_port=port,
            clock_port=_FixedClock(self.target_time),
        )
        pipeline.step()

        self.assertEqual(port.requests, [self.target_time])

    def test_pipeline_does_not_use_system_time(self) -> None:
        # A non-trading wall-clock time would fail if the pipeline fell back to
        # datetime.now(); the fake clock keeps the result deterministic.
        future_target = datetime.strptime(
            "2026-07-24 09:33:00", "%Y-%m-%d %H:%M:%S"
        )
        pipeline = self._make_pipeline(target_time=future_target)
        result = pipeline.step()

        self.assertEqual(result.target_time, future_target)

    def test_step_without_clock_raises(self) -> None:
        port = _RecordingMarketInputPort({self.target_time: self.market_input})
        pipeline = WorkbenchPipeline(
            session=self.session,
            market_input_port=port,
        )

        with self.assertRaisesRegex(WorkbenchPipelineError, "ClockPort"):
            pipeline.step()


class SharedImplementationTests(_BasePipelineTests):
    def test_live_and_replay_are_same_pipeline_class(self) -> None:
        live = self._make_pipeline()
        replay = self._make_pipeline(
            market_input=PipelineMarketInput(
                symbol=_SYMBOL,
                trade_date=_TRADE_DATE,
                previous_close=10.15,
            ),
            target_time="2026-07-24 09:30:00",
        )

        self.assertEqual(type(live), type(replay))
        self.assertIs(type(live), WorkbenchPipeline)

    def test_same_input_prefix_produces_identical_results(self) -> None:
        analyzer = _RecordingAnalyzer()
        first = self._make_pipeline(analyzer=analyzer)
        second = self._make_pipeline(analyzer=analyzer)

        self.assertEqual(first.step().to_dict(), second.step().to_dict())


class InstanceIsolationTests(_BasePipelineTests):
    def test_advancing_live_does_not_change_replay(self) -> None:
        live = self._make_pipeline()
        replay = self._make_pipeline(
            target_time="2026-07-24 09:31:00",
        )

        live.step()
        self.assertIsNone(replay.last_result)
        self.assertIsNone(replay.target_time)

    def test_advancing_replay_does_not_change_live(self) -> None:
        live = self._make_pipeline()
        replay = self._make_pipeline(
            target_time="2026-07-24 09:31:00",
        )

        live.step()
        live_result = live.last_result
        replay.step()

        self.assertIsNotNone(live_result)
        self.assertEqual(live.last_result, live_result)

    def test_instances_do_not_share_aggregator_or_czsc_state(self) -> None:
        analyzer = _RecordingAnalyzer()
        live = self._make_pipeline(analyzer=analyzer)
        replay_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        replay = self._make_pipeline(
            market_input=replay_input,
            target_time="2026-07-24 09:35:00",
            analyzer=analyzer,
        )

        live.step()
        replay.step()

        self.assertNotEqual(
            live.last_result.bars_5m,
            replay.last_result.bars_5m,
        )
        self.assertEqual(len(analyzer.calls), 2)
        self.assertNotEqual(analyzer.calls[0][1], analyzer.calls[1][1])


class FutureDataIsolationTests(_BasePipelineTests):
    def test_future_1m_and_5m_and_quote_values_are_not_read(self) -> None:
        future_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=[
                *self.bars_1m,
                _TimestampOnlyPoisonRow("2026-07-24 09:34:00"),
                _TimestampOnlyPoisonRow("2026-07-24 09:35:00"),
            ],
            official_5m_bars=[
                self.official_5m[0],
                _TimestampOnlyPoisonRow("2026-07-24 09:40:00"),
            ],
            quote_snapshots=[
                _TimestampOnlyPoisonRow("2026-07-24 09:34:30"),
            ],
        )
        pipeline = self._make_pipeline(market_input=future_input)
        result = pipeline.step()

        self.assertEqual(len(result.bars_1m), 3)
        self.assertEqual(result.bars_1m[-1]["timestamp"], "2026-07-24 09:33:00")
        self.assertEqual(len(result.closed_5m_prefix), 2)
        self.assertIsNotNone(result.quote)
        self.assertEqual(result.quote["timestamp"], "2026-07-24 09:33:00")

    def test_result_timestamps_do_not_exceed_target_time(self) -> None:
        pipeline = self._make_pipeline()
        result = pipeline.step()

        for bar in result.bars_1m:
            self.assertLessEqual(
                datetime.strptime(bar["timestamp"], "%Y-%m-%d %H:%M:%S"),
                self.target_time,
            )
        for bar in result.closed_5m_prefix:
            self.assertLessEqual(
                datetime.strptime(bar["timestamp"], "%Y-%m-%d %H:%M:%S"),
                self.target_time,
            )


class DynamicKIsolationTests(_BasePipelineTests):
    def test_dynamic_5m_appears_in_display(self) -> None:
        pipeline = self._make_pipeline()
        result = pipeline.step()

        timestamps = [b["timestamp"] for b in result.bars_5m]
        self.assertIn("2026-07-24 09:35:00", timestamps)
        dynamic = next(b for b in result.bars_5m if b["timestamp"] == "2026-07-24 09:35:00")
        self.assertFalse(dynamic["closed"])

    def test_dynamic_5m_does_not_enter_indicators_or_czsc(self) -> None:
        analyzer = _RecordingAnalyzer()
        pipeline = self._make_pipeline(analyzer=analyzer)
        result = pipeline.step()

        self.assertEqual(len(analyzer.calls), 1)
        _, analyzed_bars = analyzer.calls[0]
        self.assertEqual(analyzed_bars, list(result.closed_5m_prefix))
        self.assertTrue(all(b["closed"] for b in analyzed_bars))
        self.assertNotIn(
            "2026-07-24 09:35:00",
            [b["timestamp"] for b in analyzed_bars],
        )
        self.assertIn(
            "2026-07-24 09:35:00",
            [b["timestamp"] for b in result.bars_5m],
        )


class OfficialKReplacementTests(_BasePipelineTests):
    def test_official_bar_replaces_dynamic(self) -> None:
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        analyzer = _RecordingAnalyzer()
        pipeline = self._make_pipeline(
            market_input=input_with_official,
            target_time="2026-07-24 09:35:00",
            analyzer=analyzer,
        )
        result = pipeline.step()

        timestamps = [b["timestamp"] for b in result.bars_5m]
        self.assertEqual(timestamps.count("2026-07-24 09:35:00"), 1)
        official = next(b for b in result.bars_5m if b["timestamp"] == "2026-07-24 09:35:00")
        self.assertTrue(official["closed"])

        _, analyzed_bars = analyzer.calls[0]
        self.assertEqual(len(analyzed_bars), 3)
        self.assertTrue(all(b["closed"] for b in analyzed_bars))
        self.assertIn("2026-07-24 09:35:00", [b["timestamp"] for b in analyzed_bars])


class IndicatorInputRangeTests(_BasePipelineTests):
    def test_one_minute_indicators_align_with_eligible_1m(self) -> None:
        pipeline = self._make_pipeline()
        result = pipeline.step()

        self.assertEqual(
            len(result.indicators_1m["volume"]["values"]),
            len(result.bars_1m),
        )
        self.assertEqual(
            len(result.indicators_1m["vwap"]),
            len(result.bars_1m),
        )
        self.assertEqual(
            result.indicators_1m["volume"]["values"][-1]["timestamp"],
            "2026-07-24 09:33:00",
        )

    def test_five_minute_indicators_use_preheat_plus_official_only(self) -> None:
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        pipeline = self._make_pipeline(
            market_input=input_with_official,
            target_time="2026-07-24 09:35:00",
        )
        result = pipeline.step()

        values = result.indicators_5m["volume"]["values"]
        self.assertEqual(len(values), 3)
        self.assertTrue(all(b["closed"] for b in result.closed_5m_prefix))
        self.assertEqual(
            [b["timestamp"] for b in result.closed_5m_prefix],
            ["2026-07-23 14:55:00", "2026-07-23 15:00:00", "2026-07-24 09:35:00"],
        )


class CzscRebuildTests(_BasePipelineTests):
    def test_analyzer_receives_full_closed_prefix_each_time(self) -> None:
        analyzer = _RecordingAnalyzer()
        pipeline = self._make_pipeline(analyzer=analyzer)

        first = pipeline.step()
        self.assertEqual(len(analyzer.calls), 1)
        self.assertTrue(all(b["closed"] for b in analyzer.calls[0][1]))

        # Add an official closed bar and recompute at 09:35: the analyzer must
        # be called again with the new closed prefix that now includes 09:35.
        target_with_official = datetime.strptime(
            "2026-07-24 09:35:00", "%Y-%m-%d %H:%M:%S"
        )
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        port = _RecordingMarketInputPort(
            {target_with_official: input_with_official}
        )
        pipeline_with_official = WorkbenchPipeline(
            session=self.session,
            market_input_port=port,
            clock_port=_FixedClock(target_with_official),
            analyzer=analyzer,
        )
        second = pipeline_with_official.step()

        self.assertEqual(len(analyzer.calls), 2)
        self.assertTrue(all(b["closed"] for b in analyzer.calls[1][1]))
        self.assertIn("2026-07-24 09:35:00", [b["timestamp"] for b in analyzer.calls[1][1]])
        self.assertIsInstance(first.chan_analysis, dict)
        self.assertIsInstance(second.chan_analysis, dict)

    def test_default_analyzer_returns_serializable_dict(self) -> None:
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        pipeline = self._make_pipeline(
            market_input=input_with_official,
            target_time="2026-07-24 09:35:00",
        )
        result = pipeline.step()

        self.assertIsInstance(result.chan_analysis, dict)
        self.assertNotIn("_raw", result.chan_analysis)
        self.assertIn("symbol", result.chan_analysis)


class InputValidationTests(_BasePipelineTests):
    def test_non_closed_official_bar_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            preheat_5m_bars=[],
            bars_1m=[],
            official_5m_bars=[
                bar(
                    "2026-07-24 09:35:00",
                    10,
                    10,
                    10,
                    10,
                    1,
                    10,
                    closed=False,
                ),
            ],
        )
        pipeline = self._make_pipeline(
            market_input=bad_input,
            target_time="2026-07-24 09:35:00",
        )

        with self.assertRaises(WorkbenchPipelineError):
            pipeline.step()

    def test_wrong_trade_date_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date="2026-07-23",
            previous_close=10.15,
        )
        pipeline = self._make_pipeline(market_input=bad_input)

        with self.assertRaisesRegex(WorkbenchPipelineError, "trade_date"):
            pipeline.step()

    def test_duplicate_timestamps_are_deduplicated(self) -> None:
        duplicate_bars = [
            bar("2026-07-24 09:31:00", 10.0, 10.0, 10.0, 10.0, 1, 10),
            bar("2026-07-24 09:31:00", 10.2, 10.3, 10.15, 10.25, 100, 1025),
        ]
        market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            bars_1m=duplicate_bars,
        )
        pipeline = self._make_pipeline(market_input=market_input)
        result = pipeline.step()

        self.assertEqual(len(result.bars_1m), 1)
        self.assertEqual(result.bars_1m[0]["close"], 10.25)

    def test_out_of_order_inputs_are_sorted(self) -> None:
        reversed_bars = list(reversed(self.bars_1m))
        market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            bars_1m=reversed_bars,
        )
        pipeline = self._make_pipeline(market_input=market_input)
        result = pipeline.step()

        self.assertEqual(
            [b["timestamp"] for b in result.bars_1m],
            ["2026-07-24 09:31:00", "2026-07-24 09:32:00", "2026-07-24 09:33:00"],
        )

    def test_official_5m_not_on_session_boundary_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            official_5m_bars=[
                bar("2026-07-24 09:34:00", 10, 10, 10, 10, 1, 10),
            ],
        )
        pipeline = self._make_pipeline(
            market_input=bad_input,
            target_time="2026-07-24 09:34:00",
        )

        with self.assertRaises(WorkbenchPipelineError):
            pipeline.step()


class EmptyPrefixTests(_BasePipelineTests):
    def test_empty_prefix_produces_empty_indicators_and_analysis(self) -> None:
        empty_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
        )
        analyzer = _RecordingAnalyzer()
        pipeline = self._make_pipeline(
            market_input=empty_input,
            analyzer=analyzer,
        )
        result = pipeline.step()

        self.assertEqual(result.bars_1m, ())
        self.assertEqual(result.bars_5m, ())
        self.assertEqual(result.closed_5m_prefix, ())
        self.assertIsNone(result.daily_bar)
        self.assertIsNone(result.quote)
        self.assertEqual(result.indicators_1m["volume"]["values"], [])
        self.assertEqual(result.indicators_5m["volume"]["values"], [])
        self.assertEqual(analyzer.calls[0][1], [])


class PreheatFutureDataTests(_BasePipelineTests):
    def test_future_preheat_bar_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=[
                bar("2026-07-24 09:35:00", 10, 10, 10, 10, 1, 10),
            ],
        )
        pipeline = self._make_pipeline(market_input=bad_input)

        with self.assertRaisesRegex(
            WorkbenchPipelineError,
            "preheat 5m bar timestamp must be before the target session start",
        ):
            pipeline.step()

    def test_preheat_at_session_start_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=[
                bar("2026-07-24 09:30:00", 10, 10, 10, 10, 1, 10),
            ],
        )
        pipeline = self._make_pipeline(market_input=bad_input)

        with self.assertRaisesRegex(
            WorkbenchPipelineError,
            "preheat 5m bar timestamp must be before the target session start",
        ):
            pipeline.step()


class AtomicStateUpdateTests(_BasePipelineTests):
    def test_failed_compute_does_not_update_target_time_or_result(self) -> None:
        good_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
        )
        good_time = datetime.strptime("2026-07-24 09:30:00", "%Y-%m-%d %H:%M:%S")
        bad_time = datetime.strptime("2026-07-24 09:35:00", "%Y-%m-%d %H:%M:%S")
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            official_5m_bars=[
                bar(
                    "2026-07-24 09:35:00",
                    10,
                    10,
                    10,
                    10,
                    1,
                    10,
                    closed=False,
                ),
            ],
        )
        port = _RecordingMarketInputPort(
            {good_time: good_input, bad_time: bad_input}
        )
        pipeline = WorkbenchPipeline(
            session=self.session,
            market_input_port=port,
            clock_port=_FixedClock(good_time),
        )

        first = pipeline.step()
        self.assertEqual(pipeline.target_time, good_time)
        self.assertEqual(pipeline.last_result, first)

        # Force the next computation to fail before the atomic commit.
        pipeline._clock_port = _FixedClock(bad_time)
        with self.assertRaises(WorkbenchPipelineError):
            pipeline.step()

        self.assertEqual(pipeline.target_time, good_time)
        self.assertEqual(pipeline.last_result, first)


class ClosedPrefixDeduplicationTests(_BasePipelineTests):
    def test_closed_prefix_is_globally_merged_without_duplicates(self) -> None:
        # Even though preheat validation prevents target-day overlap, the merge
        # must still be timestamp-keyed rather than a blind concatenation.
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        pipeline = self._make_pipeline(
            market_input=input_with_official,
            target_time="2026-07-24 09:35:00",
        )
        result = pipeline.step()

        timestamps = [b["timestamp"] for b in result.closed_5m_prefix]
        self.assertEqual(len(timestamps), len(set(timestamps)))
        self.assertEqual(
            timestamps,
            ["2026-07-23 14:55:00", "2026-07-23 15:00:00", "2026-07-24 09:35:00"],
        )


class DailyBarsTests(_BasePipelineTests):
    def test_daily_bars_merge_history_and_dynamic_bar(self) -> None:
        history = [
            {
                "timestamp": "2026-07-22",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 10000,
                "amount": 101000,
                "closed": True,
            },
            {
                "timestamp": "2026-07-23",
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.15,
                "volume": 12000,
                "amount": 121800,
                "closed": True,
            },
        ]
        market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            bars_1m=self.bars_1m,
            daily_bars_history=history,
        )
        pipeline = self._make_pipeline(market_input=market_input)
        result = pipeline.step()

        dates = [b["timestamp"] for b in result.daily_bars]
        self.assertEqual(dates, ["2026-07-22", "2026-07-23", "2026-07-24"])
        self.assertFalse(result.daily_bars[-1]["closed"])
        self.assertEqual(result.daily_bars[-1]["timestamp"], "2026-07-24")

    def test_dynamic_daily_bar_overwrites_history_for_trade_date(self) -> None:
        history = [
            {
                "timestamp": "2026-07-24",
                "open": 9.0,
                "high": 9.0,
                "low": 9.0,
                "close": 9.0,
                "volume": 1,
                "amount": 9,
                "closed": True,
            },
        ]
        market_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            bars_1m=self.bars_1m,
            daily_bars_history=history,
        )
        pipeline = self._make_pipeline(market_input=market_input)
        result = pipeline.step()

        self.assertEqual(len(result.daily_bars), 1)
        self.assertFalse(result.daily_bars[0]["closed"])
        self.assertEqual(result.daily_bars[0]["close"], 10.4)

    def test_non_closed_daily_history_bar_is_rejected(self) -> None:
        bad_input = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            daily_bars_history=[
                {
                    "timestamp": "2026-07-23",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 1,
                    "amount": 10,
                    "closed": False,
                },
            ],
        )
        pipeline = self._make_pipeline(market_input=bad_input)

        with self.assertRaisesRegex(
            WorkbenchPipelineError,
            "expected a closed daily bar",
        ):
            pipeline.step()


class _RaisingMarketInputPort:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self, target_time: datetime) -> PipelineMarketInput:
        raise self._exc


class _RaisingAnalyzer:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __call__(
        self,
        bars: Sequence[Mapping[str, Any]],
        symbol: str,
    ) -> dict[str, Any]:
        raise self._exc


class ErrorBoundaryTests(_BasePipelineTests):
    def test_market_input_runtime_error_is_wrapped(self) -> None:
        pipeline = WorkbenchPipeline(
            session=self.session,
            market_input_port=_RaisingMarketInputPort(RuntimeError("provider down")),
            clock_port=_FixedClock(self.target_time),
        )

        with self.assertRaisesRegex(WorkbenchPipelineError, "provider down"):
            pipeline.step()

    def test_analyzer_runtime_error_is_wrapped(self) -> None:
        pipeline = self._make_pipeline(
            analyzer=_RaisingAnalyzer(ValueError("czsc failed")),
        )

        with self.assertRaisesRegex(WorkbenchPipelineError, "czsc failed"):
            pipeline.step()

    def test_workbench_pipeline_error_is_not_double_wrapped(self) -> None:
        pipeline = WorkbenchPipeline(
            session=self.session,
            market_input_port=_RaisingMarketInputPort(
                WorkbenchPipelineError("already pipeline")
            ),
            clock_port=_FixedClock(self.target_time),
        )

        with self.assertRaises(WorkbenchPipelineError) as ctx:
            pipeline.step()
        self.assertNotIn("pipeline computation failed", str(ctx.exception))


class SymbolIdentityTests(_BasePipelineTests):
    def test_chan_analysis_uses_same_symbol_as_pipeline(self) -> None:
        input_with_official = PipelineMarketInput(
            symbol=_SYMBOL,
            trade_date=_TRADE_DATE,
            previous_close=10.15,
            preheat_5m_bars=self.preheat,
            bars_1m=self.bars_1m,
            official_5m_bars=self.official_5m,
        )
        pipeline = self._make_pipeline(
            market_input=input_with_official,
            target_time="2026-07-24 09:35:00",
        )
        result = pipeline.step()

        self.assertEqual(result.chan_analysis["symbol"], result.symbol)
        self.assertEqual(result.symbol, _SYMBOL)


if __name__ == "__main__":
    unittest.main()
