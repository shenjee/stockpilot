"""Simulated monotonic clock and injectable Replay playback scheduler.

T0-046 drives Replay playback with a clock that is independent of the market
data layer.  The Replay cursor advances strictly along
``PreparedReplayData.actual_bar_times``; the monotonic clock and scheduler only
pace auto-playback.  Tests inject a :class:`SimulatedMonotonicClock` together
with a :class:`NullPlaybackScheduler` and drive :meth:`ReplaySession.pump_playback`
manually, so 1x/2x/5x/10x cadence can be proven without any real ``sleep``.

The module deliberately stays transport-free: it knows nothing about Electron,
HTTP, React, the Replay JSON Schema or the workbench pipeline.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol


class MonotonicClockPort(Protocol):
    """Injectable monotonic clock used only for playback pacing.

    ``now()`` returns monotonic seconds (never wall-clock time).  Production
    uses :class:`SystemMonotonicClock`; deterministic tests use
    :class:`SimulatedMonotonicClock`.
    """

    def now(self) -> float: ...


class SystemMonotonicClock:
    """Default monotonic clock backed by :func:`time.monotonic`."""

    def now(self) -> float:
        return time.monotonic()


class SimulatedMonotonicClock:
    """Controllable monotonic clock for deterministic playback tests.

    The clock never advances on its own; tests call :meth:`advance` or
    :meth:`set_now` to move playback time forward.  Combined with the
    :class:`NullPlaybackScheduler` this lets a test prove that, for example,
    a 2x session consumes two actual bars per simulated second.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._value = float(start)

    def now(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += float(seconds)

    def set_now(self, value: float) -> None:
        self._value = float(value)


class PlaybackSchedulerPort(Protocol):
    """Paces auto-playback by invoking the pump at monotonic due times.

    The scheduler owns no Replay state.  It only decides *when* to call
    ``ReplaySession.pump_playback``; the pump performs the actual cursor
    advance, computation submission and event publishing.  This separation is
    what makes playback fully testable without a real timer.
    """

    def start(self) -> None:
        """Begin servicing scheduled wakes (idempotent)."""

    def schedule(self, at_mono: float) -> None:
        """Request the next pump wake at the given monotonic time.

        A newer call replaces any earlier request.  ``at_mono`` may be in the
        past, in which case the pump runs as soon as possible.
        """

    def cancel(self) -> None:
        """Drop the pending wake without stopping the scheduler."""

    def stop(self) -> None:
        """Stop servicing wakes permanently."""


class NullPlaybackScheduler:
    """No-op scheduler used by deterministic tests.

    ``schedule``/``cancel``/``stop`` are no-ops; tests call
    :meth:`ReplaySession.pump_playback` directly after advancing an injected
    :class:`SimulatedMonotonicClock`.
    """

    def start(self) -> None:
        return None

    def schedule(self, at_mono: float) -> None:
        return None

    def cancel(self) -> None:
        return None

    def stop(self) -> None:
        return None


class TimerPlaybackScheduler:
    """Production scheduler backed by a single dedicated daemon thread.

    The thread waits for a requested wake time, sleeps interruptibly until that
    monotonic instant, then invokes the pump callback.  The pump callback blocks
    while a computation is in flight; that backpressure is intentional and keeps
    at most one in-flight advance per Session, so faster speeds never pile up an
    unbounded queue of computation tasks.

    ``cancel`` and ``stop`` wake the sleeping thread promptly so pause/retire
    take effect without waiting for the next due time.
    """

    def __init__(
        self,
        pump: Callable[[], None],
        *,
        clock: MonotonicClockPort | None = None,
    ) -> None:
        if not callable(pump):
            raise TypeError("pump must be callable")
        self._pump = pump
        self._clock = clock or SystemMonotonicClock()
        self._cond = threading.Condition()
        self._next_wake: float | None = None
        self._stopped = False
        self._started = False
        self._thread = threading.Thread(
            target=self._run,
            name="stockpilot-replay-playback",
            daemon=True,
        )

    def start(self) -> None:
        with self._cond:
            if self._started:
                return
            self._started = True
        self._thread.start()

    def schedule(self, at_mono: float) -> None:
        with self._cond:
            self._next_wake = float(at_mono)
            self._cond.notify_all()

    def cancel(self) -> None:
        with self._cond:
            self._next_wake = None
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._next_wake = None
            self._cond.notify_all()
        if threading.current_thread() is not self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and self._next_wake is None:
                    self._cond.wait()
                if self._stopped:
                    return
                wake = self._next_wake
                delta = wake - self._clock.now()
                if delta > 0:
                    # Sleep interruptibly so cancel/stop/reschedule take effect
                    # immediately instead of waiting for the full delta.
                    self._cond.wait(timeout=delta)
                    continue
                self._next_wake = None
            try:
                self._pump()
            except Exception:
                # The pump owns its own state transitions and event publishing.
                # Swallow so the scheduler thread never dies silently and leave
                # the pump's error handling in charge of the Session state.
                pass


__all__ = [
    "MonotonicClockPort",
    "NullPlaybackScheduler",
    "PlaybackSchedulerPort",
    "SimulatedMonotonicClock",
    "SystemMonotonicClock",
    "TimerPlaybackScheduler",
]
