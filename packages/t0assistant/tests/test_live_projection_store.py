"""LiveProjectionStore revision-authority tests (T0-026).

Deterministic, network-free tests that drive the single revision authority
through full snapshot candidates and typed incremental updates. They assert the
monotonic revision (assigned solely by the store), rejection of stale-Session
events via the atomic acceptance boundary, schema-valid payload enforcement,
the ``get_live_snapshot`` rebaseline contract, and concurrency safety.
"""

from __future__ import annotations

from dataclasses import replace
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


def _indicators_with_vwap(vwap_points: list[dict[str, Any]]) -> dict[str, Any]:
    """A schema-valid indicators increment carrying only one_minute.vwap points.

    All other series are empty arrays (preserved by the merge), and the
    required scalar structure fields keep their canonical values.  This
    mirrors the canonical ``workbench-flow-v1.json`` increment shape.
    """

    return {
        "five_minute": {
            "ma": {"ma5": [], "ma10": [], "ma20": [], "ma30": [], "ma60": []},
            "boll": {"period": 20, "stddev": 2.0, "upper": [], "middle": [], "lower": []},
            "volume": {"values": [], "ma5": [], "ma10": []},
            "macd": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "dif": [], "dea": [], "histogram": []},
        },
        "one_minute": {
            "vwap": list(vwap_points),
            "volume": {"values": []},
            "macd": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "dif": [], "dea": [], "histogram": []},
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
            observed_now=self.target_time,
            market_candidate_trade_date=self.market_session.trade_date,
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

    def test_operation_failure_advances_revision_without_changing_projection(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        before = self.store.get_live_snapshot(session_id="live-1", generation=1)

        event = self.store.accept_operation_failure(
            session_id="live-1",
            generation=1,
            operation_id="refresh-1",
            payload={"error_code": "calculation_failed"},
        )
        after = self.store.get_live_snapshot(session_id="live-1", generation=1)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.revision, 1)
        self.assertEqual(event.event_type, "operation_failed")
        self.assertEqual(after["session"]["revision"], 1)
        before["session"]["revision"] = 1
        self.assertEqual(after, before)

    def test_prebaseline_failure_is_revision_zero_and_retains_prior_snapshot(self) -> None:
        self.coordinator.set_accepted("live-old", 1)
        self.store.accept_candidate(
            self.fixture.candidate(session_id="live-old", generation=1)
        )
        retained_session = self.store.current_session
        retained_revision = self.store.current_revision
        self.coordinator.set_accepted("live-new", 2)

        event = self.store.accept_operation_failure(
            session_id="live-new",
            generation=2,
            operation_id="load-live-new",
            payload={"error_code": "calculation_failed"},
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.revision, 0)
        self.assertEqual(event.session_id, "live-new")
        self.assertEqual(self.store.current_session, retained_session)
        self.assertEqual(self.store.current_revision, retained_revision)

    def test_prebaseline_failure_without_prior_snapshot_is_revision_zero(self) -> None:
        self.coordinator.set_accepted("live-new", 1)

        event = self.store.accept_operation_failure(
            session_id="live-new",
            generation=1,
            operation_id="load-live-new",
            payload={"error_code": "calculation_failed"},
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.revision, 0)
        self.assertFalse(self.store.has_snapshot)

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
        self.assertEqual(envelope["schema_version"], "t0_app_v2")
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

    def test_live_market_view_updated_applies_to_authoritative_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        payload = {
            "effective_trade_date": "2026-07-24",
            "calendar_status": "available",
            "market_phase": "morning",
            "symbol_availability": "available",
            "data_quality": "partial",
            "polling_profile": "active",
            "quote_as_of": "2026-07-24 09:32:03",
            "bars_1m_as_of": "2026-07-24 09:32:00",
            "bars_5m_as_of": None,
            "daily_as_of": None,
            "one_minute_indicators_as_of": "2026-07-24 09:32:00",
            "five_minute_indicators_as_of": None,
            "czsc_as_of": None,
            "bars_30m_as_of": None,
            "thirty_minute_indicators_as_of": None,
            "czsc_30m_as_of": None,
        }
        event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="live_market_view_updated",
                payload=payload,
            )
        )
        self.assertIsNotNone(event)
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(snapshot["live_market_view"]["quote_as_of"], "2026-07-24 09:32:03")

    def test_indicators_merge_and_chan_replacement_apply_to_authoritative_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        baseline = self.store.get_live_snapshot(session_id="live-1", generation=1)
        baseline_vwap_ts = [
            point["timestamp"]
            for point in baseline["indicators"]["one_minute"]["vwap"]
        ]
        new_point = {"timestamp": "2026-07-24 09:32:00", "value": 10.08}
        indicators_payload = _indicators_with_vwap([new_point])
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
        # Indicators are merged by timestamp: baseline history is preserved and
        # the new point is present (not a whole-block replacement).
        vwap_ts = [
            point["timestamp"]
            for point in snapshot["indicators"]["one_minute"]["vwap"]
        ]
        for ts in baseline_vwap_ts:
            self.assertIn(ts, vwap_ts)
        self.assertIn("2026-07-24 09:32:00", vwap_ts)
        # Chan analysis is an authoritative full replacement.
        self.assertEqual(snapshot["chan_analysis"], chan_payload)
        self.assertEqual(snapshot["session"]["revision"], 2)

    def test_indicator_increment_merges_by_timestamp_preserving_history(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))

        first_point = {"timestamp": "2026-07-24 09:32:00", "value": 10.08}
        second_point = {"timestamp": "2026-07-24 09:33:00", "value": 10.12}
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="indicators_updated",
                payload=_indicators_with_vwap([first_point]),
            )
        )
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="indicators_updated",
                payload=_indicators_with_vwap([second_point]),
            )
        )

        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        vwap = snapshot["indicators"]["one_minute"]["vwap"]
        timestamps = [point["timestamp"] for point in vwap]
        # The first increment's point survives the second increment (merge, not
        # replace), and the merged series stays in ascending timestamp order.
        self.assertIn("2026-07-24 09:32:00", timestamps)
        self.assertIn("2026-07-24 09:33:00", timestamps)
        self.assertEqual(timestamps, sorted(timestamps))

    def test_indicator_increment_upserts_existing_timestamp_value(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))

        original = {"timestamp": "2026-07-24 09:32:00", "value": 10.08}
        revised = {"timestamp": "2026-07-24 09:32:00", "value": 10.50}
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="indicators_updated",
                payload=_indicators_with_vwap([original]),
            )
        )
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="indicators_updated",
                payload=_indicators_with_vwap([revised]),
            )
        )

        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        vwap = snapshot["indicators"]["one_minute"]["vwap"]
        matching = [
            point for point in vwap
            if point["timestamp"] == "2026-07-24 09:32:00"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["value"], 10.50)

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

    def test_bar_increment_sorts_late_bar_into_chronological_order(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        # Baseline bars_1m carries the 09:31 bar.  Send a late, earlier bar so a
        # plain append would leave the array out of order relative to Renderer.
        late_bar = _bar("2026-07-24 09:30:00", 9.98, 10.0, 9.95, 10.0, 500, 4998)

        event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_1m", "bars": [late_bar], "quote": None},
            )
        )

        assert event is not None
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        bars_1m = snapshot["market"]["bars_1m"]
        timestamps = [bar["timestamp"] for bar in bars_1m]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(timestamps[0], "2026-07-24 09:30:00")

    def test_five_minute_increment_revises_dynamic_bar_and_drops_stale_unclosed(
        self,
    ) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        revised = _bar(
            "2026-07-24 09:35:00",
            10.0,
            10.4,
            9.9,
            10.3,
            350,
            3550,
            closed=False,
        )
        next_bucket = _bar(
            "2026-07-24 09:40:00",
            11.0,
            11.0,
            11.0,
            11.0,
            2,
            22,
            closed=False,
        )
        official = _bar(
            "2026-07-24 09:35:00",
            10.0,
            10.5,
            9.8,
            10.4,
            900,
            9200,
        )

        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_5m", "bars": [revised], "quote": None},
            )
        )
        after_revise = self.store.get_live_snapshot(session_id="live-1", generation=1)
        dynamic = [
            bar
            for bar in after_revise["market"]["bars_5m"]
            if bar["timestamp"] == "2026-07-24 09:35:00"
        ]
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0]["close"], 10.3)
        self.assertFalse(dynamic[0]["closed"])

        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_5m", "bars": [next_bucket], "quote": None},
            )
        )
        after_cross = self.store.get_live_snapshot(session_id="live-1", generation=1)
        by_timestamp = {
            bar["timestamp"]: bar for bar in after_cross["market"]["bars_5m"]
        }
        self.assertNotIn("2026-07-24 09:35:00", by_timestamp)
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])
        self.assertEqual(
            sum(1 for bar in by_timestamp.values() if bar["closed"] is False),
            1,
        )

        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={
                    "target": "bars_5m",
                    "bars": [official, next_bucket],
                    "quote": None,
                },
            )
        )
        after_official = self.store.get_live_snapshot(session_id="live-1", generation=1)
        by_timestamp = {
            bar["timestamp"]: bar for bar in after_official["market"]["bars_5m"]
        }
        self.assertTrue(by_timestamp["2026-07-24 09:35:00"]["closed"])
        self.assertEqual(by_timestamp["2026-07-24 09:35:00"]["close"], 10.4)
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])

    def test_older_projection_seq_does_not_delete_current_dynamic_bar(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        next_bucket = _bar(
            "2026-07-24 09:40:00",
            11.0,
            11.0,
            11.0,
            11.0,
            2,
            22,
            closed=False,
        )
        official = _bar(
            "2026-07-24 09:35:00",
            10.0,
            10.5,
            9.8,
            10.4,
            900,
            9200,
        )

        accepted = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_5m", "bars": [next_bucket], "quote": None},
                projection_seq=2,
            )
        )
        self.assertIsNotNone(accepted)
        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                event_type="market_update",
                payload={"target": "bars_5m", "bars": [official], "quote": None},
                projection_seq=1,
            )
        )
        self.assertIsNone(rejected)
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        by_timestamp = {
            bar["timestamp"]: bar for bar in snapshot["market"]["bars_5m"]
        }
        self.assertFalse(by_timestamp["2026-07-24 09:40:00"]["closed"])
        self.assertNotIn("2026-07-24 09:35:00", by_timestamp)

    def test_accepted_envelope_and_snapshot_pass_frozen_schema(self) -> None:
        from json import loads as json_loads
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from importlib import resources
        from pathlib import Path

        logical = json_loads(
            (resources.files("packages.t0assistant") / "contracts" / "logical-v2.schema.json")
            .read_text(encoding="utf-8")
        )
        app_path = (
            Path(__file__).resolve().parents[3]
            / "apps" / "t0-assistant" / "contracts" / "app-v2.schema.json"
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

    def test_stale_epoch_incremental_rejected_after_new_baseline(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        baseline = self.fixture.candidate(session_id="live-1", generation=1)
        self.store.accept_candidate(baseline)
        revision_after_baseline = self.store.current_revision
        self.assertEqual(self.store.published_market_epoch, 0)

        stale = LiveIncrementalUpdate(
            session_id="live-1",
            generation=1,
            event_type="market_update",
            payload={
                "target": "quote",
                "bars": [],
                "quote": _quote("2026-07-24 09:30:03", 10.03),
            },
            market_epoch=0,
        )
        switched = self.fixture.candidate(session_id="live-1", generation=1)
        switched = LiveSnapshotCandidate(
            session_id=switched.session_id,
            generation=switched.generation,
            symbol=switched.symbol,
            pipeline_result=switched.pipeline_result,
            market_epoch=1,
        )
        switch_event = self.store.accept_candidate(switched)

        self.assertIsNotNone(switch_event)
        self.assertEqual(self.store.published_market_epoch, 1)
        rejected = self.store.accept_incremental(stale)
        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_revision, revision_after_baseline + 1)

    def test_incremental_rejected_before_matching_baseline_epoch(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        revision_after_baseline = self.store.current_revision

        ahead = LiveIncrementalUpdate(
            session_id="live-1",
            generation=1,
            event_type="market_update",
            payload={
                "target": "quote",
                "bars": [],
                "quote": _quote("2026-07-24 09:30:03", 10.03),
            },
            market_epoch=1,
        )
        self.assertIsNone(self.store.accept_incremental(ahead))
        self.assertEqual(self.store.current_revision, revision_after_baseline)
        self.assertEqual(self.store.published_market_epoch, 0)

    def test_stale_full_candidate_cannot_roll_back_published_epoch(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        baseline = self.fixture.candidate(session_id="live-1", generation=1)
        self.store.accept_candidate(baseline)
        switched = replace(
            self.fixture.candidate(session_id="live-1", generation=1),
            market_epoch=1,
        )
        self.store.accept_candidate(switched)

        revision = self.store.current_revision
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)

        rejected = self.store.accept_candidate(baseline)

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_revision, revision)
        self.assertEqual(self.store.published_market_epoch, 1)
        self.assertEqual(
            self.store.get_live_snapshot(session_id="live-1", generation=1),
            snapshot,
        )

    def test_concurrent_stale_candidate_cannot_roll_back_new_epoch(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        baseline = self.fixture.candidate(session_id="live-1", generation=1)
        switched = replace(
            self.fixture.candidate(session_id="live-1", generation=1),
            market_epoch=1,
        )
        self.store.accept_candidate(baseline)
        revision_after_switch: list[int] = []
        errors: list[BaseException] = []

        def publish_new_epoch() -> None:
            try:
                event = self.store.accept_candidate(switched)
                if event is not None:
                    revision_after_switch.append(event.revision)
            except BaseException as exc:
                errors.append(exc)

        def publish_stale_epoch() -> None:
            try:
                self.store.accept_candidate(baseline)
            except BaseException as exc:
                errors.append(exc)

        new_thread = Thread(target=publish_new_epoch)
        stale_thread = Thread(target=publish_stale_epoch)
        new_thread.start()
        new_thread.join(timeout=2)
        stale_thread.start()
        stale_thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(revision_after_switch, [1])
        self.assertEqual(self.store.published_market_epoch, 1)
        self.assertEqual(self.store.current_revision, 1)

    def test_stale_epoch_operation_failure_rejected_after_day_switch(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )
        switched = replace(
            self.fixture.candidate(session_id="live-1", generation=1),
            market_epoch=1,
        )
        self.store.accept_candidate(switched)
        revision = self.store.current_revision

        rejected = self.store.accept_operation_failure(
            session_id="live-1",
            generation=1,
            operation_id="refresh-quote",
            market_epoch=0,
            payload={"error_code": "calculation_failed"},
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_revision, revision)
        self.assertEqual(self.store.published_market_epoch, 1)


if __name__ == "__main__":
    unittest.main()
