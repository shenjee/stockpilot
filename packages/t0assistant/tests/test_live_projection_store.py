"""LiveProjectionStore revision-authority tests (T0-026).

Deterministic, network-free tests that drive the single revision authority
through full snapshot candidates and typed incremental updates. They assert the
monotonic revision, rejection of stale/duplicate/out-of-order events, the
``get_live_snapshot`` rebaseline contract, and concurrency safety.
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


class _FakeCoordinator:
    """Minimal acceptance boundary backed by Coordinator.accepts_result shape."""

    def __init__(self) -> None:
        self._accepted: tuple[str, int] | None = None

    def set_accepted(self, session_id: str, generation: int) -> None:
        self._accepted = (session_id, generation)

    def clear(self) -> None:
        self._accepted = None

    def accepts_result(
        self,
        *,
        session_type: SessionType | str,
        session_id: str,
        generation: int,
    ) -> bool:
        if self._accepted is None:
            return False
        resolved = (
            session_type if isinstance(session_type, SessionType) else SessionType(session_type)
        )
        if resolved is not SessionType.LIVE:
            return False
        return self._accepted == (session_id, generation)


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
        # No session accepted yet.
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
                proposed_revision=2,
                event_type="market_update",
                payload={
                    "target": "quote",
                    "bars": [],
                    "quote": {"price": 10.20, "volume": 100, "amount": 1020.0},
                },
            )
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        assert first is not None and second is not None and third is not None
        self.assertEqual([first.revision, second.revision, third.revision], [0, 1, 2])
        self.assertEqual(self.store.current_revision, 2)

    # --- incremental rejection rules -------------------------------------

    def test_duplicate_or_old_revision_incremental_is_rejected(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        # current_revision == 0; propose 0 (duplicate) and a second propose 0 again.
        dup = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=0,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
            )
        )
        again = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=0,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
            )
        )

        self.assertIsNone(dup)
        self.assertIsNone(again)
        self.assertEqual(self.store.current_revision, 0)

    def test_out_of_order_gap_incremental_is_rejected(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        # current_revision == 0; expected next is 1, propose 2 (gap).
        gap = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=2,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
            )
        )

        self.assertIsNone(gap)
        self.assertEqual(self.store.current_revision, 0)

    def test_rejected_incremental_does_not_change_snapshot_or_revision(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        snapshot_before = self.store.get_live_snapshot(
            session_id="live-1", generation=1
        )
        revision_before = self.store.current_revision

        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=5,  # gap
                event_type="indicators_updated",
                payload={"five_minute": {}, "one_minute": {}},
            )
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_revision, revision_before)
        self.assertEqual(
            self.store.get_live_snapshot(session_id="live-1", generation=1),
            snapshot_before,
        )

    # --- stale session / generation rejection ----------------------------

    def test_old_session_id_incremental_is_rejected(self) -> None:
        self.coordinator.set_accepted("live-2", 2)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-2", generation=2))

        rejected = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",  # old session
                generation=2,
                proposed_revision=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
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
                generation=1,  # old generation
                proposed_revision=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
            )
        )

        self.assertIsNone(rejected)
        self.assertEqual(self.store.current_session, ("live-2", 2))

    def test_late_candidate_from_retired_session_is_dropped(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        # Session retires / is superseded; coordinator no longer accepts it.
        self.coordinator.clear()

        event = self.store.accept_candidate(
            self.fixture.candidate(session_id="live-1", generation=1)
        )

        self.assertIsNone(event)
        # Authority keeps the last published state but will not advance it.
        self.assertEqual(self.store.current_revision, 0)

    def test_new_session_candidate_resets_revision_to_zero(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=1,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
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

    # --- get_live_snapshot rebaseline contract ---------------------------

    def test_get_live_snapshot_after_gap_returns_latest_complete_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        # Apply one accepted increment so the snapshot advances past the baseline.
        accepted = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=1,
                event_type="market_update",
                payload={
                    "target": "quote",
                    "bars": [],
                    "quote": {"price": 10.55, "volume": 200, "amount": 2110.0},
                },
            )
        )
        self.assertIsNotNone(accepted)
        # A gap event arrives and is rejected.
        self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=3,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": None},
            )
        )

        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)

        self.assertEqual(snapshot["session"]["revision"], 1)
        self.assertEqual(snapshot["session"]["session_id"], "live-1")
        self.assertEqual(snapshot["market"]["quote"]["price"], 10.55)

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
        payload = {
            "target": "bars_1m",
            "bars": [_bar("2026-07-24 09:32:00", 10.06, 10.10, 10.04, 10.09, 700, 7063)],
            "quote": None,
        }

        event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=1,
                event_type="market_update",
                payload=payload,
            )
        )

        assert event is not None
        self.assertEqual(event.event_type, "market_update")
        self.assertEqual(event.payload, payload)
        # Internal authoritative snapshot reflects the applied increment.
        snapshot = self.store.get_live_snapshot(session_id="live-1", generation=1)
        self.assertEqual(len(snapshot["market"]["bars_1m"]), 2)
        self.assertEqual(snapshot["market"]["bars_1m"][-1]["close"], 10.09)
        self.assertEqual(snapshot["session"]["revision"], 1)

    def test_indicators_and_chan_incremental_apply_to_authoritative_state(self) -> None:
        self.coordinator.set_accepted("live-1", 1)
        self.store.accept_candidate(self.fixture.candidate(session_id="live-1", generation=1))
        indicators_payload = {"five_minute": {"ma5": [1.0]}, "one_minute": {"ma5": [2.0]}}
        chan_payload = {"symbol": "sh.600000", "timeframe": "5m", "strokes": [{"a": 1}]}

        ind_event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=1,
                event_type="indicators_updated",
                payload=indicators_payload,
            )
        )
        chan_event = self.store.accept_incremental(
            LiveIncrementalUpdate(
                session_id="live-1",
                generation=1,
                proposed_revision=2,
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
        # No duplicates and strictly ascending overall sequence 0..N-1.
        self.assertEqual(sorted(revisions), list(range(len(revisions))))
        self.assertEqual(len(set(revisions)), len(revisions))
        self.assertEqual(self.store.current_revision, len(revisions) - 1)


if __name__ == "__main__":
    unittest.main()
