from __future__ import annotations

import copy
from datetime import timedelta
import threading
import unittest

from packages.t0assistant.runtime.computation_executor import (
    BoundedComputationExecutor,
)
from packages.t0assistant.runtime.replay_clock import (
    NullPlaybackScheduler,
    SimulatedMonotonicClock,
)
from packages.t0assistant.runtime.replay_session import ReplaySession
from packages.t0assistant.tests.test_replay_session import (
    _CachingAnalyzer,
    _default_analyze_5m,
    _prepare,
)


def _business_snapshot(snapshot: dict) -> dict:
    result = copy.deepcopy(snapshot)
    result["session"]["revision"] = 0
    return result


class ReplaySeekTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executors: list[BoundedComputationExecutor] = []

    def tearDown(self) -> None:
        for executor in self.executors:
            executor.shutdown(cancel_pending=True, wait=True)

    def _session(self) -> ReplaySession:
        prepared = _prepare("1m")
        executor = BoundedComputationExecutor(capacity=8, worker_count=1)
        self.executors.append(executor)
        return ReplaySession(
            "seek-session",
            1,
            prepared,
            executor,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            analyzer=_CachingAnalyzer(_default_analyze_5m),
        )

    def test_forward_seek_consumes_only_target_prefix(self) -> None:
        session = self._session()
        target = session.start_time + timedelta(minutes=11)

        result = session.seek(target, "seek-forward")

        self.assertEqual(result.outcome_status, "completed")
        self.assertFalse(result.rebuilt)
        snapshot = session.snapshot()
        self.assertEqual(snapshot["replay"]["current_time"], target.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertTrue(
            all(bar["timestamp"] <= snapshot["replay"]["current_time"] for bar in snapshot["market"]["bars_1m"])
        )
        self.assertTrue(
            all(point["timestamp"] <= snapshot["replay"]["current_time"] for point in snapshot["indicators"]["one_minute"]["vwap"])
        )

    def test_backward_seek_replaces_pipeline_and_removes_future_data(self) -> None:
        session = self._session()
        late = session.start_time + timedelta(minutes=40)
        early = session.start_time + timedelta(minutes=7)
        session.seek(late, "seek-late")
        old_pipeline = session._pipeline

        result = session.seek(early, "seek-early")

        self.assertTrue(result.rebuilt)
        self.assertIsNot(session._pipeline, old_pipeline)
        snapshot = session.snapshot()
        current = snapshot["replay"]["current_time"]
        self.assertTrue(
            all(bar["timestamp"] <= current for bar in snapshot["market"]["bars_1m"])
        )
        # A forming 5m candle may carry its future close label, but it is
        # explicitly unclosed and contains only the consumed 1m prefix.
        self.assertTrue(
            all(
                bar["timestamp"] <= current or bar["closed"] is False
                for bar in snapshot["market"]["bars_5m"]
            )
        )
        for series in snapshot["indicators"]["one_minute"]["macd"].values():
            if isinstance(series, list):
                self.assertTrue(all(point["timestamp"] <= current for point in series))

    def test_same_target_rebuild_is_deterministic(self) -> None:
        session = self._session()
        target = session.start_time + timedelta(minutes=26)
        session.seek(target, "seek-first")
        first = _business_snapshot(session.snapshot())
        session.seek(session.start_time, "seek-reset")
        session.seek(target, "seek-second")
        second = _business_snapshot(session.snapshot())
        self.assertEqual(first, second)

    def test_seek_during_playing_cancels_scheduler_and_resumes(self) -> None:
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        executor = BoundedComputationExecutor(capacity=8, worker_count=1)
        self.executors.append(executor)
        session = ReplaySession(
            "seek-playing",
            1,
            prepared,
            executor,
            clock=clock,
            scheduler=NullPlaybackScheduler(),
            analyzer=_CachingAnalyzer(_default_analyze_5m),
        )
        session.play()
        target = session.start_time + timedelta(minutes=12)
        result = session.seek(target, "seek-while-playing")
        self.assertEqual(result.outcome_status, "completed")
        self.assertEqual(session.current_time, target)
        self.assertEqual(session.state, "playing")
        # Playback after resume continues from the seeked cursor.
        due = session.pump_playback()
        self.assertIn(due.action, {"not_due", "advanced"})

    def test_new_seek_supersedes_late_old_seek(self) -> None:
        prepared = _prepare("1m")
        executor = BoundedComputationExecutor(capacity=8, worker_count=2)
        self.executors.append(executor)
        started = threading.Event()
        release = threading.Event()
        delegate = _CachingAnalyzer(_default_analyze_5m)
        calls = 0

        def analyzer(bars, symbol):
            nonlocal calls
            calls += 1
            if calls == 3:
                started.set()
                release.wait(timeout=5)
            return delegate(bars, symbol)

        session = ReplaySession(
            "seek-latest-wins",
            1,
            prepared,
            executor,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            analyzer=analyzer,
        )
        session.seek(
            session.start_time + timedelta(minutes=15),
            "seek-baseline",
        )
        old_results = []
        old_thread = threading.Thread(
            target=lambda: old_results.append(
                session.seek(
                    session.start_time + timedelta(minutes=30),
                    "seek-old",
                )
            ),
            daemon=True,
        )
        old_thread.start()
        self.assertTrue(started.wait(timeout=5))

        newest = session.seek(session.start_time, "seek-new")
        release.set()
        old_thread.join(timeout=5)

        self.assertEqual(newest.outcome_status, "completed")
        self.assertEqual(session.current_time, session.start_time)
        self.assertEqual(len(old_results), 1)
        self.assertEqual(old_results[0].outcome_status, "dropped")


if __name__ == "__main__":
    unittest.main()
