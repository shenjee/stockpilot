"""Live 1-minute refresh must publish and apply the dynamic 5-minute bar."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from threading import Event, Thread
from typing import Any, Mapping, Sequence

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.coordinator import SessionSpec, SessionType
from packages.t0assistant.runtime.computation_executor import BoundedComputationExecutor
from packages.t0assistant.runtime.live_projection_store import (
    LiveIncrementalUpdate,
    LiveProjectionStore,
)
from packages.t0assistant.runtime.live_refresh import (
    LiveRefreshKind,
    LiveRefreshScheduler,
)
from packages.t0assistant.runtime.live_runtime import BranchingLiveInput
from packages.t0assistant.runtime.live_session import (
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
)
from packages.t0assistant.runtime.pipeline import PipelineMarketInput, WorkbenchPipeline


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
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


PREHEAT = _bar("2026-07-23 15:00:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)
BAR_0931 = _bar("2026-07-24 09:31:00", 10.0, 10.2, 9.9, 10.1, 100, 1000)
BAR_0932 = _bar("2026-07-24 09:32:00", 10.1, 10.4, 10.0, 10.3, 250, 2550)
BAR_0936 = _bar("2026-07-24 09:36:00", 11.0, 11.0, 11.0, 11.0, 2, 22)
OFFICIAL_0935 = _bar("2026-07-24 09:35:00", 10.0, 10.5, 9.8, 10.4, 900, 9200)


def _chan(symbol: str, closed_timestamps: Sequence[str]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "timeframe": "5m",
        "source": "fixture",
        "engine": "czsc",
        "engine_version": "0.10.12",
        "parameters": {},
        "fractals": [],
        "strokes": [],
        "segments": [],
        "pivot_zones": [],
        "divergences": [],
        "structure_alerts": [],
        "signal_series": [],
        "signal_events": [],
        "signal_snapshots": [],
        "candidate_point_events": [],
        "candidate_buy_points": [],
        "candidate_sell_points": [],
        "plot_primitives": [],
        "summary": [],
        "warnings": [],
        "meta": {"closed_bar_timestamps": list(closed_timestamps)},
    }


class _RecordingAnalyzer:
    def __init__(self) -> None:
        self.prefixes: list[tuple[str, ...]] = []

    def __call__(self, bars: Sequence[Mapping[str, Any]], symbol: str) -> dict[str, Any]:
        timestamps = tuple(str(bar["timestamp"]) for bar in bars)
        self.prefixes.append(timestamps)
        self.assert_closed(bars)
        return _chan(symbol, timestamps)

    @staticmethod
    def assert_closed(bars: Sequence[Mapping[str, Any]]) -> None:
        if any(bar.get("closed") is not True for bar in bars):
            raise AssertionError("dynamic 5m bar entered CZSC input")


class _QueuedLiveSource:
    def __init__(self) -> None:
        self.context = MarketContextService(["2026-07-23", "2026-07-24"])
        self.session = self.context.require_session("2026-07-24", "sh")
        self.bars_1m = [BAR_0931]
        self.official_5m: list[dict[str, Any]] = []

    def _market_input(self, spec: SessionSpec) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[PREHEAT],
            bars_1m=list(self.bars_1m),
            official_5m_bars=list(self.official_5m),
            daily_bars_history=[],
            quote_snapshots=[],
        )

    def prepare(self, spec, *, minimum_preheat_5m, target_trade_date=None):
        market_input = self._market_input(spec)

        class _Port:
            def read(self, target_time):
                return market_input

        return PreparedLiveWarmup(
            market_session=self.session,
            target_time=datetime(2026, 7, 24, 9, 31),
            observed_now=datetime(2026, 7, 24, 9, 31),
            market_candidate_trade_date=date(2026, 7, 24),
            market_input_port=_Port(),
            calendar_status="available",
            market_phase="morning",
        )

    def load_refresh_bars(self, spec, *, timeframe, trade_date) -> Sequence[Mapping]:
        if timeframe == "1m":
            return tuple(self.bars_1m)
        return tuple(self.official_5m)

    def load_refresh_quotes(self, spec, *, trade_date) -> Sequence[Mapping]:
        return ()


class _FakeCoordinator:
    def __init__(self) -> None:
        self._accepted: tuple[str, int] | None = None

    def set_accepted(self, session_id: str, generation: int) -> None:
        self._accepted = (session_id, generation)

    def commit_if_accepted(self, *, session_type, session_id, generation, commit) -> bool:
        if self._accepted != (session_id, generation):
            return False
        commit()
        return True


def _spec() -> SessionSpec:
    return SessionSpec(
        session_id="live-1",
        session_type=SessionType.LIVE,
        symbol="sh.600000",
        generation=1,
        trade_date=None,
    )


def _dynamic_bars(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        bar
        for bar in snapshot["market"]["bars_5m"]
        if bar.get("closed") is False
    ]


def _five_minute_indicator_timestamps(snapshot: Mapping[str, Any]) -> set[str]:
    values = snapshot["indicators"]["five_minute"]["volume"]["values"]
    return {str(point["timestamp"]) for point in values}


class LiveDynamicFiveMinuteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = _spec()
        self.source = _QueuedLiveSource()
        self.analyzer = _RecordingAnalyzer()
        self.port = BranchingLiveInput(self.source, analyzer=self.analyzer)
        self.coordinator = _FakeCoordinator()
        self.coordinator.set_accepted("live-1", 1)
        self.store = LiveProjectionStore(self.coordinator, service_generation=7)
        prepared = self.port.prepare(self.spec, minimum_preheat_5m=1)
        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
            analyzer=self.analyzer,
        )
        candidate = LiveSnapshotCandidate(
            session_id=self.spec.session_id,
            generation=self.spec.generation,
            symbol=self.spec.symbol,
            pipeline_result=pipeline.preview(prepared.target_time),
            calendar_status=prepared.calendar_status,
            market_phase=prepared.market_phase,
            market_candidate_trade_date=prepared.market_candidate_trade_date,
        )
        self.store.accept_candidate(candidate)

    def _apply(self, updates: Sequence[LiveIncrementalUpdate]) -> dict[str, Any]:
        for update in updates:
            event = self.store.accept_incremental(update)
            self.assertIsNotNone(event)
        return self.store.get_live_snapshot(
            session_id=self.spec.session_id,
            generation=self.spec.generation,
        )

    def _assert_no_dynamic_in_indicators_or_czsc(self, snapshot: Mapping[str, Any]) -> None:
        indicator_ts = _five_minute_indicator_timestamps(snapshot)
        for bar in _dynamic_bars(snapshot):
            self.assertNotIn(bar["timestamp"], indicator_ts)
        closed_from_chan = snapshot["chan_analysis"]["meta"]["closed_bar_timestamps"]
        for bar in _dynamic_bars(snapshot):
            self.assertNotIn(bar["timestamp"], closed_from_chan)

    def test_one_minute_refresh_sequence_updates_store_dynamic_5m(self) -> None:
        initial = self.store.get_live_snapshot(
            session_id=self.spec.session_id,
            generation=self.spec.generation,
        )
        initial_dynamic = _dynamic_bars(initial)
        self.assertEqual(len(initial_dynamic), 1)
        self.assertEqual(initial_dynamic[0]["timestamp"], "2026-07-24 09:35:00")
        self.assertEqual(initial_dynamic[0]["close"], 10.1)
        self._assert_no_dynamic_in_indicators_or_czsc(initial)

        self.source.bars_1m = [BAR_0931, BAR_0932]
        result_0932 = self.port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self.spec,
            observed_at=datetime(2026, 7, 24, 9, 32),
            latest_data_time=datetime(2026, 7, 24, 9, 31),
        )
        published_0932 = next(
            update.payload["bars"]
            for update in result_0932.updates
            if update.payload.get("target") == "bars_5m"
        )
        after_0932 = self._apply(result_0932.updates)
        dynamic = _dynamic_bars(after_0932)
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0], published_0932[0])
        self.assertEqual(
            dynamic[0],
            _bar(
                "2026-07-24 09:35:00",
                10.0,
                10.4,
                9.9,
                10.3,
                350,
                3550,
                closed=False,
            ),
        )
        self._assert_no_dynamic_in_indicators_or_czsc(after_0932)

        self.source.bars_1m = [BAR_0931, BAR_0932, BAR_0936]
        after_0936 = self._apply(
            self.port.refresh(
                LiveRefreshKind.ONE_MINUTE,
                self.spec,
                observed_at=datetime(2026, 7, 24, 9, 36),
                latest_data_time=datetime(2026, 7, 24, 9, 32),
            ).updates
        )
        by_timestamp = {
            bar["timestamp"]: bar for bar in after_0936["market"]["bars_5m"]
        }
        self.assertNotIn("2026-07-24 09:35:00", by_timestamp)
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])
        self.assertEqual(len(_dynamic_bars(after_0936)), 1)
        self.assertTrue(by_timestamp["2026-07-23 15:00:00"]["closed"])
        self._assert_no_dynamic_in_indicators_or_czsc(after_0936)

        self.source.official_5m = [OFFICIAL_0935]
        after_official = self._apply(
            self.port.refresh(
                LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
                self.spec,
                observed_at=datetime(2026, 7, 24, 9, 36),
                latest_data_time=datetime(2026, 7, 23, 15, 0),
            ).updates
        )
        by_timestamp = {
            bar["timestamp"]: bar for bar in after_official["market"]["bars_5m"]
        }
        self.assertTrue(by_timestamp["2026-07-24 09:35:00"]["closed"])
        self.assertEqual(by_timestamp["2026-07-24 09:35:00"]["close"], 10.4)
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])
        self.assertEqual(len(_dynamic_bars(after_official)), 1)
        self.assertIn(
            "2026-07-24 09:35:00",
            after_official["chan_analysis"]["meta"]["closed_bar_timestamps"],
        )
        self.assertNotIn(
            "2026-07-24 09:40:00",
            after_official["chan_analysis"]["meta"]["closed_bar_timestamps"],
        )
        self._assert_no_dynamic_in_indicators_or_czsc(after_official)

    def test_one_minute_branch_publishes_unclosed_bars_5m(self) -> None:
        self.source.bars_1m = [BAR_0931, BAR_0932]
        result = self.port.refresh(
            LiveRefreshKind.ONE_MINUTE,
            self.spec,
            observed_at=datetime(2026, 7, 24, 9, 32),
            latest_data_time=datetime(2026, 7, 24, 9, 31),
        )
        targets = [
            update.payload.get("target")
            for update in result.updates
            if update.event_type == "market_update"
        ]
        self.assertEqual(targets, ["bars_1m", "daily_bars", "bars_5m"])
        bars_5m = next(
            update.payload["bars"]
            for update in result.updates
            if update.payload.get("target") == "bars_5m"
        )
        self.assertEqual(len(bars_5m), 1)
        self.assertEqual(bars_5m[0]["timestamp"], "2026-07-24 09:35:00")
        self.assertFalse(bars_5m[0]["closed"])
        self.assertEqual(bars_5m[0]["volume"], 350)
        self.assertEqual(bars_5m[0]["amount"], 3550)


class _BarrierLiveInput(BranchingLiveInput):
    """Force official snapshot generation to finish before the 1m branch."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.official_generated = Event()
        self.official_may_return = Event()
        self.one_minute_returned = Event()
        self.generate_order: list[str] = []

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ):
        if kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE:
            result = super().refresh(
                kind,
                spec,
                observed_at=observed_at,
                latest_data_time=latest_data_time,
            )
            self.generate_order.append("official_five_minute")
            self.official_generated.set()
            if not self.official_may_return.wait(timeout=5):
                raise TimeoutError("official branch was not released")
            return result
        if kind is LiveRefreshKind.ONE_MINUTE:
            if not self.official_generated.wait(timeout=5):
                raise TimeoutError("official branch did not generate first")
            result = super().refresh(
                kind,
                spec,
                observed_at=observed_at,
                latest_data_time=latest_data_time,
            )
            self.generate_order.append("one_minute")
            self.one_minute_returned.set()
            return result
        return super().refresh(
            kind,
            spec,
            observed_at=observed_at,
            latest_data_time=latest_data_time,
        )


class LiveDynamicFiveMinuteRaceTests(unittest.TestCase):
    def test_official_first_generation_does_not_delete_later_dynamic_k(self) -> None:
        spec = _spec()
        source = _QueuedLiveSource()
        analyzer = _RecordingAnalyzer()
        port = _BarrierLiveInput(source, analyzer=analyzer)
        coordinator = _FakeCoordinator()
        coordinator.set_accepted("live-1", 1)
        store = LiveProjectionStore(coordinator, service_generation=7)
        prepared = port.prepare(spec, minimum_preheat_5m=1)
        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
            analyzer=analyzer,
        )
        store.accept_candidate(
            LiveSnapshotCandidate(
                session_id=spec.session_id,
                generation=spec.generation,
                symbol=spec.symbol,
                pipeline_result=pipeline.preview(prepared.target_time),
                calendar_status=prepared.calendar_status,
                market_phase=prepared.market_phase,
                market_candidate_trade_date=prepared.market_candidate_trade_date,
            )
        )
        source.bars_1m = [BAR_0931, BAR_0932, BAR_0936]
        source.official_5m = [OFFICIAL_0935]

        published_5m: list[list[dict[str, Any]]] = []
        published_seq: list[int | None] = []
        accepted: list[object] = []
        failures: list[tuple[LiveRefreshKind, BaseException]] = []

        def on_update(update: LiveIncrementalUpdate) -> object:
            if (
                update.event_type == "market_update"
                and update.payload.get("target") == "bars_5m"
            ):
                published_5m.append(list(update.payload["bars"]))
                published_seq.append(update.projection_seq)
            event = store.accept_incremental(update)
            accepted.append(event)
            return event

        executor = BoundedComputationExecutor(capacity=12, worker_count=3)
        scheduler = LiveRefreshScheduler(
            spec,
            port,
            executor,
            on_update=on_update,
            on_failure=lambda kind, exc, _epoch=None: failures.append((kind, exc)),
        )
        self.addCleanup(lambda: (
            scheduler.retire(),
            executor.shutdown(cancel_pending=True, wait=True),
        ))

        worker = Thread(
            target=lambda: scheduler.run_due(datetime(2026, 7, 24, 9, 36))
        )
        worker.start()
        self.assertTrue(port.one_minute_returned.wait(timeout=5))
        port.official_may_return.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(accepted)
        self.assertTrue(all(event is not None for event in accepted))

        self.assertEqual(
            port.generate_order,
            ["official_five_minute", "one_minute"],
        )
        self.assertGreaterEqual(len(published_5m), 2)
        first_by_ts = {bar["timestamp"]: bar for bar in published_5m[0]}
        self.assertTrue(first_by_ts["2026-07-24 09:35:00"]["closed"])
        self.assertNotIn("2026-07-24 09:40:00", first_by_ts)
        second_unclosed = [
            bar for bar in published_5m[1] if bar.get("closed") is False
        ]
        self.assertEqual(len(second_unclosed), 1)
        self.assertEqual(second_unclosed[0]["timestamp"], "2026-07-24 09:40:00")

        snapshot = store.get_live_snapshot(
            session_id=spec.session_id,
            generation=spec.generation,
        )
        by_timestamp = {
            bar["timestamp"]: bar for bar in snapshot["market"]["bars_5m"]
        }
        self.assertTrue(by_timestamp["2026-07-24 09:35:00"]["closed"])
        self.assertEqual(by_timestamp["2026-07-24 09:35:00"]["close"], 10.4)
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])
        self.assertEqual(len(_dynamic_bars(snapshot)), 1)
        self.assertEqual(published_seq, [1, 2])


if __name__ == "__main__":
    unittest.main()
