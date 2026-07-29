"""Tests for the simulated monotonic clock and playback scheduler (T0-046)."""

from __future__ import annotations

import threading
import time
import unittest

from packages.t0assistant.runtime.replay_clock import (
    NullPlaybackScheduler,
    SimulatedMonotonicClock,
    SystemMonotonicClock,
    TimerPlaybackScheduler,
)


class SimulatedMonotonicClockTests(unittest.TestCase):
    def test_starts_at_given_value_and_does_not_self_advance(self) -> None:
        clock = SimulatedMonotonicClock(start=12.5)
        self.assertEqual(clock.now(), 12.5)
        # Repeated reads are stable: the clock never moves on its own.
        self.assertEqual(clock.now(), 12.5)

    def test_advance_accumulates(self) -> None:
        clock = SimulatedMonotonicClock()
        self.assertEqual(clock.now(), 0.0)
        clock.advance(0.5)
        clock.advance(1.5)
        self.assertEqual(clock.now(), 2.0)

    def test_set_now_overrides(self) -> None:
        clock = SimulatedMonotonicClock(start=10.0)
        clock.set_now(3.0)
        self.assertEqual(clock.now(), 3.0)


class SystemMonotonicClockTests(unittest.TestCase):
    def test_now_is_monotonic_non_decreasing(self) -> None:
        clock = SystemMonotonicClock()
        first = clock.now()
        second = clock.now()
        self.assertGreaterEqual(second, first)


class NullPlaybackSchedulerTests(unittest.TestCase):
    def test_all_operations_are_no_ops(self) -> None:
        scheduler = NullPlaybackScheduler()
        scheduler.start()
        scheduler.schedule(1.0)
        scheduler.cancel()
        scheduler.stop()  # must not raise


class TimerPlaybackSchedulerTests(unittest.TestCase):
    def test_pump_fires_at_scheduled_monotonic_time(self) -> None:
        fired = threading.Event()
        clock = SystemMonotonicClock()
        scheduler = TimerPlaybackScheduler(fired.set, clock=clock)
        scheduler.start()
        try:
            scheduler.schedule(clock.now() + 0.02)
            self.assertTrue(fired.wait(timeout=2.0), "pump did not fire in time")
        finally:
            scheduler.stop()

    def test_cancel_drops_pending_wake(self) -> None:
        fired = threading.Event()
        clock = SystemMonotonicClock()
        scheduler = TimerPlaybackScheduler(fired.set, clock=clock)
        scheduler.start()
        try:
            scheduler.schedule(clock.now() + 1.0)
            # Cancel promptly; the pump must not fire for the far-future wake.
            scheduler.cancel()
            self.assertFalse(fired.wait(timeout=0.2))
        finally:
            scheduler.stop()

    def test_stop_is_idempotent_and_joins(self) -> None:
        scheduler = TimerPlaybackScheduler(lambda: None)
        scheduler.start()
        scheduler.stop()
        scheduler.stop()  # must not raise


if __name__ == "__main__":
    unittest.main()
