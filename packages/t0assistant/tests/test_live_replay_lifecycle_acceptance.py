"""T0-048 acceptance tests for the one-shot Replay lifecycle.

These tests exercise the real application coordinator and Replay engine
together.  The small Live port is deliberately transport- and provider-free:
it models an already-running Live Session whose authoritative projection keeps
advancing while Replay owns the visible workbench.
"""

from __future__ import annotations

from dataclasses import dataclass
import unittest

from packages.t0assistant.runtime import (
    AppCoordinator,
    AppMode,
    BoundedComputationExecutor,
    NullPlaybackScheduler,
    ReplaySession,
    SessionSpec,
    SessionType,
    SimulatedMonotonicClock,
)
from packages.t0assistant.tests.test_replay_session import (
    _CachingAnalyzer,
    _default_analyze_5m,
    _prepare,
)


@dataclass(slots=True)
class _BackgroundLiveSession:
    spec: SessionSpec
    revision: int = 0
    latest_value: int = 0
    retired: bool = False

    def publish_update(self, value: int) -> None:
        if self.retired:
            raise RuntimeError("retired Live Session cannot publish")
        self.revision += 1
        self.latest_value = value

    def retire(self) -> None:
        self.retired = True


class _ConcreteLifecycleFactory:
    def __init__(self) -> None:
        self.executor = BoundedComputationExecutor(capacity=16, worker_count=1)
        self.live_sessions: list[_BackgroundLiveSession] = []
        self.replay_sessions: list[ReplaySession] = []
        self.replay_events: list[dict] = []
        self.trade_events: list[dict] = []

    def create_live(self, spec: SessionSpec) -> _BackgroundLiveSession:
        session = _BackgroundLiveSession(spec)
        self.live_sessions.append(session)
        return session

    def create_replay(self, spec: SessionSpec) -> ReplaySession:
        if spec.session_type is not SessionType.REPLAY or spec.trade_date is None:
            raise AssertionError("expected a dated Replay Session spec")
        session = ReplaySession(
            spec.session_id,
            1,
            _prepare("1m"),
            self.executor,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            on_event=self.replay_events.append,
            on_trade_event=self.trade_events.append,
        )
        self.replay_sessions.append(session)
        return session

    def close(self) -> None:
        self.executor.shutdown(cancel_pending=True, wait=True)


def _session_id(session_type: SessionType, generation: int) -> str:
    return f"{session_type.value}-{generation}"


class LiveReplayLifecycleAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = _ConcreteLifecycleFactory()
        self.coordinator = AppCoordinator(
            self.factory,
            session_id_factory=_session_id,
        )

    def tearDown(self) -> None:
        try:
            self.coordinator.retire_all()
        finally:
            self.factory.close()

    def test_live_keeps_advancing_and_is_immediately_current_after_replay(self) -> None:
        selected = self.coordinator.select_symbol("sh.600000")
        live_identity = selected.live_session
        assert live_identity is not None
        live = self.factory.live_sessions[0]
        live.publish_update(101)

        blank_replay = self.coordinator.set_mode(AppMode.REPLAY)
        self.assertIsNone(blank_replay.visible_session)
        started = self.coordinator.begin_replay("2026-07-24")
        replay_identity = started.replay_session
        assert replay_identity is not None
        replay = self.factory.replay_sessions[0]
        replay.step("step-1")

        # Live remains accepted and continues to receive its own updates while
        # the Replay projection is visible.
        live.publish_update(202)
        self.assertEqual(live.revision, 2)
        self.assertEqual(live.latest_value, 202)
        self.assertFalse(live.retired)
        self.assertTrue(
            self.coordinator.accepts_result(
                session_type=SessionType.LIVE,
                session_id=live_identity.session_id,
                generation=live_identity.generation,
            )
        )

        returned = self.coordinator.set_mode(AppMode.LIVE)

        self.assertEqual(returned.visible_session, live_identity)
        self.assertIsNone(returned.replay_session)
        self.assertEqual(live.latest_value, 202)
        self.assertTrue(replay.retired)
        self.assertFalse(
            self.coordinator.accepts_result(
                session_type=SessionType.REPLAY,
                session_id=replay_identity.session_id,
                generation=replay_identity.generation,
            )
        )

    def test_leaving_replay_destroys_date_progress_picture_and_simulated_trades(
        self,
    ) -> None:
        self.coordinator.select_symbol("sh.600000")
        self.coordinator.set_mode(AppMode.REPLAY)
        started = self.coordinator.begin_replay("2026-07-24")
        replay_identity = started.replay_session
        assert replay_identity is not None
        replay = self.factory.replay_sessions[0]
        replay.step("step-trade-time")
        replay.create_simulated_trade(
            {
                "trade_scope": "simulated",
                "symbol": replay.symbol,
                "side": "buy",
                "executed_at": replay.current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "price": 10.01,
                "quantity": 100,
                "fee": None,
                "note": "one-shot",
                "fee_plan_id": None,
            },
            trade_id="sim-lifecycle",
        )
        self.assertTrue(replay.snapshot()["market"]["bars_1m"])
        self.assertEqual(len(replay.simulated_trades), 1)
        self.assertEqual(
            len(self.factory.trade_events[-1]["payload"]["trades"]),
            1,
        )

        returned = self.coordinator.set_mode(AppMode.LIVE)

        self.assertIsNone(returned.replay_session)
        self.assertTrue(replay.retired)
        self.assertEqual(replay.state, "retired")
        self.assertEqual(replay.simulated_trades, ())
        self.assertEqual(
            self.factory.trade_events[-1]["payload"]["trades"],
            [],
        )
        self.assertEqual(
            self.factory.replay_events[-1]["event_type"],
            "session_status",
        )
        self.assertEqual(
            self.factory.replay_events[-1]["payload"]["state"],
            "retired",
        )
        with self.assertRaisesRegex(RuntimeError, "retired"):
            replay.snapshot()

        # Re-entering Replay is blank and creates a different one-shot Session;
        # no date or progress identity is restored by the coordinator.
        blank = self.coordinator.set_mode(AppMode.REPLAY)
        self.assertIsNone(blank.replay_session)
        self.assertIsNone(blank.visible_session)
        replacement = self.coordinator.begin_replay("2026-07-24")
        assert replacement.replay_session is not None
        self.assertNotEqual(
            replacement.replay_session.session_id,
            replay_identity.session_id,
        )
        new_replay = self.factory.replay_sessions[1]
        self.assertEqual(new_replay.current_time, new_replay.start_time)
        self.assertEqual(new_replay.simulated_trades, ())


if __name__ == "__main__":
    unittest.main()
