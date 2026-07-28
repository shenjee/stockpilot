"""LiveProjectionStore revision-authority tests (T0-026).

Deterministic, network-free tests that drive the single revision authority
through full snapshot candidates and typed incremental updates. They assert the
monotonic revision (assigned solely by the store), rejection of stale-Session
events via the atomic acceptance boundary, schema-valid payload enforcement,
the ``get_live_snapshot`` rebaseline contract, and concurrency safety.
"""

from __future__ import annotations

from datetime import date, datetime
from threading import Thread
import unittest
from typing import Any

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import (
    PipelineMarketInput,
    SessionType,
)
from packages.t0assistant.runtime.live_projection_store import (
    LiveAcceptedEvent,
    LiveIncrementalUpdate,
    LiveProjectionSnapshotUnavailable,
    LiveProjectionStore,
    LiveProjectionValidationError,
)
from packages.t0assistant.runtime.live_session import (
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
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
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def _quote(timestamp: str, price: float) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "latest_price": price,
        "change_percent": 1.23,
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "previous_close": 9.9,
        "volume": 10000,
        "amount": 100000.0,
        "volume_ratio": 1.5,
        "order_imbalance": None,
        "turnover_rate": 0.3,
    }


def _chan(symbol: str) -> dict[str, Any]:
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
        "meta": {},
    }


def _empty_indicators() -> dict[str, Any]:
    """A minimal schema-valid indicators payload."""

    point = {"timestamp": "2026-07-24 09:31:00", "value": 1.0}
    return {
        "five_minute": {
            "ma": {"ma5": [point], "ma10": [point], "ma20": [point], "ma30": [point], "ma60": [point]},
            "boll": {"period": 20, "stddev": 2.0, "upper": [point], "middle": [point], "lower": [point]},
            "volume": {"values": [point], "ma5": [point], "ma10": [point]},
            "macd": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "dif": [point], "dea": [point], "histogram": [point]},
        },
        "one_minute": {
            "vwap": [point],
            "volume": {"values": [point]},
            "macd": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "dif": [point], "dea": [point], "histogram": [point]},
        },
    }


class _FakeCoordinator:
    """Minimal acceptance boundary backed by commit_if_accepted semantics."""

    def __init__(self) -> None:
        self._accepted: tuple[str, int] | None = None

    def set_accepted(self, session_id: str, generation: int) -> None:
        self._accepted = (session_id, generation)

    def clear(self) -> None:
        self._accepted = None

    def commit_if_accepted(
        self,
        *,
        session_type: SessionType | str,
        session_id: str,
        generation: int,
        commit: Any,
    ) -> bool:
        if self._accepted is None:
            return False
        resolved = (
            session_type if isinstance(session_type, SessionType) else SessionType(session_type)
        )
        if resolved is not SessionType.LIVE:
            return False
        if self._accepted != (session_id, generation):
            return False
        commit()
        return True


class _StoreFixture:
    """Builds schema-valid LiveSnapshotCandidate instances without a network."""

    def __init__(self) -> None:
        calendar = MarketContextService(["2026-07-24", "2026-07-23"])
        self.market_session = calendar.require_session("2026-07-24", "sh")
        self.target_time = datetime(2026, 7, 24, 9, 31, 0)

    def _market_input(self, symbol: str) -> PipelineMarketInput:
        return PipelineMarketInput(
            symbol=symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0, 10.1, 9.9, 10.02, 1000, 10020),
                _bar("2026-07-23 15:00:00", 10.02, 10.08, 10.0, 10.05, 1200, 12060),
            ],
            bars_1m=[
                _bar("2026-07-24 09:31:00", 10.05, 10.08, 10.0, 10.06, 800, 8048),
            ],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )

    def candidate(
        self,
        *,
        session_id: str,
        generation: int,
        symbol: str = "sh.600000",
    ) -> LiveSnapshotCandidate:
        from packages.t0assistant.runtime.pipeline import WorkbenchPipeline

        class _SinglePort:
            def __init__(self, target_time: datetime, market_input: PipelineMarketInput) -> None:
                self._target_time = target_time
                self._market_input = market_input

            def read(self, target_time: datetime) -> PipelineMarketInput:
                if target_time != self._target_time:
                    raise AssertionError(f"unexpected target_time: {target_time!r}")
                return self._market_input

        prepared = PreparedLiveWarmup(
            market_session=self.market_session,
            target_time=self.target_time,
            market_input_port=_SinglePort(self.target_time, self._market_input(symbol)),
        )
        pipeline = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
            analyzer=lambda bars, sym: _chan(sym),
        )
        result = pipeline.preview(prepared.target_time)
        return LiveSnapshotCandidate(
            session_id=session_id,
            generation=generation,
            symbol=symbol,
            pipeline_result=result,
        )


class LiveProjectionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = _FakeCoordinator()
        self.store = LiveProjectionStore(self.coordinator, service_generation=7)
        self.fixture = _StoreFixture()

    # --- full snapshot revision assignment -------------------------------

    def test_first_accepted_candidate_gets_first_revision(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        candidate = self.fixture.candidate(session_id="live-1", generation=1)

        event = self.store.accept_candidate(candidate)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.revision, 0)
        self.assertEqual(event.event_type, "workbench_snapshot")
        self.assertEqual(event.session_id, "live-1")
        self.assertEqual(event.service_generation, 7)
        self.assertEqual(event.payload["session"]["revision"], 0)
        self.assertEqual(self.store.current_revision, 0)
        self.assertEqual(self.store.current_session, ("live-1", 1))

    def test_rejected_candidate_for_unaccepted_session_returns_none_and_keeps_state_empty(self) -> None:
        candidate = self.fixture.candidate(session_id="live-1", generation=1)

        event = self.store.accept_candidate(candidate)

        self.assertIsNone(event)
        self.assertIsNone(self.store.current_revision)
        self.assertIsNone(self.store.current_session)
        self.assertFalse(self.store.has_snapshot)

    def test_consecutive_accepted_events_advance_revision_strictly(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        first = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        second = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        third = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.20)},
            )
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        assert first is not None and second is not None and third is not None
        self.assertEqual([first.revision, second.revision, third.revision], [0, 1, 2])
        self.assertEqual(self.store.current_revision, 2)

    # --- stale session / generation rejection ----------------------------

    def test_old_session_id_incremental_is_rejected(self) -> None:
        self.coordinator.set_accepted("live-2", 2)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-2", generation=2))

        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=2,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.20)},
            )
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_session, ("live-2", 2))

    def test_old_generation_incremental_is_rejected(self) -> None:
        self.coordinator.set_accepted("live-2", 2)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-2", generation=2))

        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-2",
                generation=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.20)},
            )
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_session, ("live-2", 2))

    def test_late_candidate_from_retired_session_is_dropped(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        self.coordinator.clear()

        event = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )

        self.assertIsNone(event)
        self.assertEqual(self.store.current_revision, 0)

    def test_new_session_candidate_resets_revision_to_zero(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.20)},
            )
        )
        self.assertEqual(self.store.current_revision, 1)

        self.coordinator.set_accepted("live-2", 2)
        event = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-2", generation=2)
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.revision, 0)
        self.assertEqual(self.store.current_session, ("live-2", 2))
        self.assertEqual(self.store.current_revision, 0)

    def test_rejected_incremental_does_not_change_snapshot_or_revision(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        snapshot_before = self.store.get_live_snapshot(
            session_id="live-1", generation=1
        )
        revision_before = self.store.current_revision

        # Old-session incremental is rejected.
        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-2",
                generation=2,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.20)},
            )
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_revision, revision_before)
        self.assertEqual(
            self.store.get_live_snapshot(session_id="live-1", generation=1),
            snapshot_before,
        )

    # --- get_live_snapshot rebaseline contract ---------------------------

    def test_get_live_snapshot_after_increment_returns_latest_complete_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        accepted = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": _quote("2026-07-24 09:32:00", 10.55)},
            )
        )
        self.assertIsNotNone(accepted)

        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)

        self.assertEqual(snapshot["session"]["revision"], 1)
        self.assertEqual(snapshot["session"]["session_id"], "live-1")
        self.assertEqual(snapshot["market"]["quote"]["latest_price"], 10.55)

    def test_get_live_snapshot_for_wrong_session_raises(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))

        with self.assertRaises(LiveProjectionSnapshotUnavailable):
            self.store.get_live_snapshot(session_id="live-2", generation=2)

    def test_get_live_snapshot_when_no_snapshot_raises(self) -> None:
        with self.assertRaises(LiveProjectionSnapshotUnavailable):
            self.store.get_live_snapshot(session_id="live-1", generation=1)

    def test_get_live_snapshot_returns_independent_deep_copy(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))

        first = self.store.get_live_snapshot(session_id="live-1", generation=1)
        first["market"]["bars_1m"].append({"tampered": True})
        first["session"]["revision"] = 999

        second = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(second["session"]["revision"], 0)
        self.assertNotIn("tampered", second["market"]["bars_1m"][-1])

    # --- envelope / payload consistency ----------------------------------

    def test_envelope_revision_matches_snapshot_internal_revision(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        event = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )

        assert event is not None
        envelope = event.to_envelope()
        self.assertEqual(envelope["schema_version"], "t0_app_v1")
        self.assertEqual(envelope["service_generation"], 7)
        self.assertEqual(envelope["session_id"], "live-1")
        self.assertEqual(envelope["revision"], envelope["payload"]["session"]["revision"])
        self.assertEqual(envelope["event_type"], "workbench_snapshot")

    def test_incremental_envelope_carries_typed_payload_not_full_snapshot(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        new_bar = _bar("2026-07-24 09:32:00", 10.06, 10.10, 10.04, 10.09, 700, 7063)
        payload = {"target": "bars_1m", "bars": [new_bar], "quote": None}

        event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload=payload,
            )
        )

        assert event is not None
        self.assertEqual(event.event_type, "market_update")
        self.assertEqual(event.payload, payload)
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(len(snapshot["market"]["bars_1m"]), 2)
        self.assertEqual(snapshot["market"]["bars_1m"][-1]["close"], 10.09)
        self.assertEqual(snapshot["session"]["revision"], 1)

    def test_indicators_and_chan_incremental_apply_to_authoritative_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        indicators_payload = _empty_indicators()
        chan_payload = _chan("sh.600000")

        ind_event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="indicators_updated",
                payload=indicators_payload,
            )
        )
        chan_event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="chan_analysis_replaced",
                payload=chan_payload,
            )
        )

        self.assertIsNotNone(ind_event)
        self.assertIsNotNone(chan_event)
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(snapshot["indicators"], indicators_payload)
        self.assertEqual(snapshot["chan_analysis"], chan_payload)
        self.assertEqual(snapshot["session"]["revision"], 2)

    # --- schema validation (P2) ------------------------------------------

    def test_invalid_incremental_payload_raises_before_state_change(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        revision_before = self.store.current_revision
        snapshot_before = self.store.get_live_snapshot(session_id="live-1", generation=1)

        with self.assertRaises(LiveProjectionValidationError):
            self.store.accept_incremental(
                LiveIncrementalUpdate(
                    session_id="live-1",
                    generation=1,
                    event_type="market_update",
                    payload={"target": "quote", "bars": [], "quote": {"bad": "quote"}},
                )
            )

        # State untouched.
        self.assertEqual(self.store.current_revision, revision_before)
        self.assertEqual(
            self.store.get_live_snapshot(session_id="live-1", generation=1),
            snapshot_before,
        )

    def test_bar_upsert_replaces_existing_timestamp(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        revised_bar = _bar("2026-07-24 09:31:00", 10.05, 10.12, 10.01, 10.11, 900, 9099)

        event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_1m", "bars": [revised_bar], "quote": None},
            )
        )

        assert event is not None
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        bars_1m = snapshot["market"]["bars_1m"]
        self.assertEqual(len(bars_1m), 1)
        self.assertEqual(bars_1m[0]["close"], 10.11)

    def test_accepted_envelope_and_snapshot_pass_frozen_schema(self) -> None:
        from json import loads as json_loads
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from importlib import resources
        from pathlib import Path

        logical = json_loads(
            (resources.files("packages.t0assistant") / "contracts" / "logical-schema.json")
            .read_text(encoding="utf-8")
        )
        app_path = (
            Path(__file__).resolve().parents[3]
            / "apps" / "t0-assistant" / "contracts" / "app-v1.schema.json"
        )
        app = json_loads(app_path.read_text(encoding="utf-8"))
        registry = Registry().with_resources(
            [
                (logical["$id"], Resource.from_contents(logical)),
                (app["$id"], Resource.from_contents(app)),
            ]
        )
        envelope_validator = Draft202012Validator(
            {"$ref": f"{app['$id']}#/$defs/event_envelope"}, registry=registry
        )
        snapshot_validator = Draft202012Validator(
            {"$ref": f"{logical['$id']}#/$defs/workbench_snapshot"}, registry=registry
        )

        self.coordinator.set_accepted("live-1", 1)
        event = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        assert event is not None

        envelope = event.to_envelope()
        self.assertEqual(list(envelope_validator.iter_errors(envelope)), [])
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(list(snapshot_validator.iter_errors(snapshot)), [])

    # --- concurrency -----------------------------------------------------

    def test_concurrent_publish_produces_unique_ordered_revisions(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        candidate = self.fixture.candidate(session_id="live-1", generation=1)
        accepted: list[LiveAcceptedEvent] = []
        errors: list[BaseException] = []

        def publish(n: int) -> None:
            try:
                for _ in range(n):
                    event = self.store.accept_candidate(candidate)
                    if event is not None:
                        accepted.append(event)
            except BaseException as exc:  # pragma: no cover - surfaced in errors
                errors.append(exc)

        threads = [Thread(target=publish, args=(20,)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        revisions = [event.revision for event in accepted]
        self.assertEqual(sorted(revisions), list(range(len(revisions))))
        self.assertEqual(len(set(revisions)), len(revisions))
        self.assertEqual(self.store.current_revision, len(revisions) - 1)


if __name__ == "__main__":
    unittest.main()
