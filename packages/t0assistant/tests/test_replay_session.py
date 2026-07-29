"""Tests for the Replay playback engine (T0-046).

Every test uses deterministic in-memory fixtures and fakes.  No test touches the
network or SQLite.  Playback cadence is proven with an injected
:class:`SimulatedMonotonicClock` and a :class:`NullPlaybackScheduler`, never with
real ``sleep``.
"""

from __future__ import annotations

import copy
import threading
import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from packages.t0assistant.replay.api import map_computation_outcome_to_replay_error
from packages.t0assistant.replay.validation import validate_replay_snapshot
from packages.t0assistant.runtime import (
    BoundedComputationExecutor,
    NullPlaybackScheduler,
    ReplaySession,
    ReplaySessionError,
    SimulatedMonotonicClock,
    SystemMonotonicClock,
    TimerPlaybackScheduler,
)
from packages.t0assistant.runtime.computation_executor import (
    ComputationExecutorClosedError,
    ComputationQueueFullError,
)
from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
)
from packages.t0assistant.runtime.pipeline import WorkbenchPipeline, _default_analyze_5m
from packages.t0assistant.runtime.replay_data import (
    ReplayDataPreparator,
    ReplayPreparationConfig,
)
from packages.t0assistant.tests.fixtures.replay_fixtures import (
    SYMBOL,
    TRADE_DATE,
    five_minute_fallback,
    market_context_service,
    one_minute_replay,
)
from packages.t0assistant.tests.test_replay_data import (
    FakeMarketDataPort,
    _populate_from_fixture,
)

_MARKET_TS = "%Y-%m-%d %H:%M:%S"
_ANALYZER_DEFAULT = object()


# ----------------------------------------------------------------------
# Test fakes and helpers
# ----------------------------------------------------------------------


class _CachingAnalyzer:
    """Real analyzer on the first call, deep-copied thereafter (fast & valid)."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._cached: dict[str, Any] | None = None

    def __call__(self, bars: Any, symbol: str) -> dict[str, Any]:
        if self._cached is None:
            self._cached = self._delegate(bars, symbol)
        return copy.deepcopy(self._cached)


class _GatedAnalyzer:
    """Analyzer that blocks on a gate so a computation stays in flight."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._gate = threading.Event()
        self._gate.set()
        self._entered = threading.Event()

    def __call__(self, bars: Any, symbol: str) -> dict[str, Any]:
        self._entered.set()
        self._gate.wait()
        return self._delegate(bars, symbol)

    def block(self) -> None:
        self._entered.clear()
        self._gate.clear()

    def release(self) -> None:
        self._gate.set()

    def wait_until_entered(self, timeout: float = 5.0) -> bool:
        return self._entered.wait(timeout=timeout)


class _RaisingAnalyzer:
    """Analyzer that raises on every call after the first."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._count = 0

    def __call__(self, bars: Any, symbol: str) -> dict[str, Any]:
        self._count += 1
        if self._count > 1:
            raise RuntimeError("boom")
        return self._delegate(bars, symbol)


class _CountingExecutor:
    """Wraps a real executor and counts submissions."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.submit_count = 0

    def submit(self, task: ComputationTask) -> Any:
        self.submit_count += 1
        return self._inner.submit(task)

    def shutdown(self, **kwargs: Any) -> None:
        return self._inner.shutdown(**kwargs)


@dataclass
class _ResolvedFuture:
    _outcome: ComputationOutcome

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        return self._outcome

    def done(self) -> bool:
        return True


class _ScriptedExecutor:
    """Returns pre-configured outcomes in order without running the callable."""

    def __init__(self, outcomes: list[ComputationOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> _ResolvedFuture:
        self.submitted.append(task)
        outcome = self._outcomes.pop(0)
        return _ResolvedFuture(outcome)

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _BrokenExecutor:
    """Always raises on submit (e.g. queue full or executor closed)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        raise self._exc

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _LateFuture:
    """Future that times out on the first call and returns a value on the second."""

    def __init__(self, value: ComputationOutcome) -> None:
        self._value = value
        self._count = 0

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        self._count += 1
        if self._count == 1:
            raise TimeoutError("simulated timeout")
        return self._value

    def done(self) -> bool:
        return self._count > 0


class _LateExecutor:
    """First submit returns a normal future; subsequent submits time out once then yield."""

    def __init__(self, ready_value: ComputationOutcome, late_value: ComputationOutcome) -> None:
        self._ready_value = ready_value
        self._late_value = late_value
        self._index = 0
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        if self._index == 0:
            self._index += 1
            ready_outcome = ComputationOutcome(
                task_id="ready",
                status=ComputationStatus.COMPLETED,
                value=self._ready_value,
            )
            return _ResolvedFuture(ready_outcome)
        return _LateFuture(self._late_value)

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _HoldingFuture:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._outcome: ComputationOutcome | None = None

    def set_outcome(self, outcome: ComputationOutcome) -> None:
        self._outcome = outcome
        self._event.set()

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        self._event.wait(timeout=timeout)
        if self._outcome is None:
            raise TimeoutError("holding future not set")
        return self._outcome

    def done(self) -> bool:
        return self._event.is_set()


class _HoldingExecutor:
    """First submit resolves immediately; later submits block until released."""

    def __init__(self, first_outcome: ComputationOutcome) -> None:
        self._first = True
        self._outcome = first_outcome
        self.submitted: list[ComputationTask] = []
        self.futures: list[_HoldingFuture] = []

    def submit(self, task: ComputationTask) -> _HoldingFuture:
        self.submitted.append(task)
        fut = _HoldingFuture()
        if self._first:
            fut.set_outcome(self._outcome)
            self._first = False
        self.futures.append(fut)
        return fut

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _TimeoutExecutor:
    """Always raises TimeoutError on future.result(), simulating a wait timeout."""

    def __init__(self) -> None:
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        return self

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        raise TimeoutError("simulated timeout")

    def done(self) -> bool:
        return False

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _ReadyThenTimeoutExecutor:
    """Delegates the first N-1 submissions to a real executor, then times out."""

    def __init__(self, inner: Any, timeout_after: int = 2) -> None:
        self._inner = inner
        self._count = 0
        self._timeout_after = timeout_after
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        self._count += 1
        if self._count < self._timeout_after:
            return self._inner.submit(task)
        return _TimeoutExecutor().submit(task)

    def shutdown(self, **kwargs: Any) -> None:
        return self._inner.shutdown(**kwargs)


class _CommitThenTimeoutFuture:
    """Future that simulates a timeout after commit_result but before set_result."""

    def __init__(self) -> None:
        self._committed = threading.Event()

    def mark_committed(self) -> None:
        self._committed.set()

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        # Wait for the worker to commit, then raise TimeoutError exactly like
        # the real T0-020 ordering where commit_result precedes set_result.
        if not self._committed.wait(timeout=timeout if timeout is not None else 5.0):
            raise TimeoutError("simulated timeout before commit")
        raise TimeoutError("simulated timeout after commit")

    def done(self) -> bool:
        return self._committed.is_set()


class _CommitThenTimeoutExecutor:
    """First submission resolves normally; later submissions commit then time out."""

    def __init__(self, ready_value: Any, _step_value: Any) -> None:
        self._ready_value = ready_value
        self._first = True
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        if self._first:
            self._first = False
            return _ResolvedFuture(
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=self._ready_value,
                )
            )
        fut = _CommitThenTimeoutFuture()

        def _run() -> None:
            try:
                value = task.callable(task)
                if task.accept_result is not None and not task.accept_result(value):
                    return
                if task.commit_result is not None:
                    task.commit_result(value)
                fut.mark_committed()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()
        return fut

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _CommitThenReleaseFuture:
    """Hold future delivery after the task's commit callback has completed."""

    def __init__(self) -> None:
        self.committed = threading.Event()
        self.release = threading.Event()
        self.value: Any = None
        self.task_id = ""

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        if not self.release.wait(timeout=timeout):
            raise TimeoutError("future delivery was not released")
        return ComputationOutcome(
            task_id=self.task_id,
            status=ComputationStatus.COMPLETED,
            value=self.value,
        )

    def done(self) -> bool:
        return self.release.is_set()


class _CommitThenReleaseExecutor:
    """Resolve ready immediately, then hold a completed advance after commit."""

    def __init__(self, ready_value: Any) -> None:
        self._ready_value = ready_value
        self._first = True
        self.pending: _CommitThenReleaseFuture | None = None

    def submit(self, task: ComputationTask) -> Any:
        if self._first:
            self._first = False
            return _ResolvedFuture(
                ComputationOutcome(
                    task_id=task.task_id,
                    status=ComputationStatus.COMPLETED,
                    value=self._ready_value,
                )
            )
        future = _CommitThenReleaseFuture()
        future.task_id = task.task_id
        self.pending = future

        def _run() -> None:
            value = task.callable(task)
            if task.accept_result is not None and not task.accept_result(value):
                return
            if task.commit_result is not None:
                task.commit_result(value)
            future.value = value
            future.committed.set()

        threading.Thread(target=_run, daemon=True).start()
        return future

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _FirstCompleteThenTimeoutExecutor:
    """First submission resolves normally; later submissions time out before committing."""

    def __init__(self, ready_value: Any) -> None:
        self._ready_value = ready_value
        self._first = True
        self.submitted: list[ComputationTask] = []

    def submit(self, task: ComputationTask) -> Any:
        self.submitted.append(task)
        if self._first:
            self._first = False
            return _ResolvedFuture(
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=self._ready_value,
                )
            )
        return _TimeoutExecutor().submit(task)

    def shutdown(self, **kwargs: Any) -> None:
        return None


class _CommitSpyExecutor:
    """Executor that lets us interpose between accept_result and commit_result."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_task: ComputationTask | None = None

    def submit(self, task: ComputationTask) -> Any:
        self.last_task = task
        return self._inner.submit(task)

    def shutdown(self, **kwargs: Any) -> None:
        return self._inner.shutdown(**kwargs)


def _prepare(fixture_name: str):
    if fixture_name == "1m":
        fixture = one_minute_replay()
    else:
        fixture = five_minute_fallback()
    port = FakeMarketDataPort()
    _populate_from_fixture(port, fixture)
    if fixture_name == "5m":
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
    return ReplayDataPreparator(port, market_context_service()).prepare(
        SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
    )


def _ready_value(prepared) -> Any:
    pipe = WorkbenchPipeline(
        session=prepared.market_session,
        market_input_port=prepared.market_input_port,
        analyzer=_CachingAnalyzer(_default_analyze_5m),
    )
    return pipe.preview(prepared.start_time)


def _consume_for_seconds(
    session: ReplaySession, clock: SimulatedMonotonicClock, seconds: float
) -> int:
    """Pump playback until the next bar is due after ``seconds`` (absolute).

    A tiny epsilon tolerates the sub-microsecond float drift that accumulates
    when adding ``1/speed`` repeatedly; it does not affect the bar count.
    """
    advanced = 0
    while True:
        result = session.pump_playback()
        if result.action == "advanced":
            advanced += 1
            continue
        if result.action == "not_due":
            if result.next_due_mono is None or result.next_due_mono > seconds + 1e-9:
                break
            clock.set_now(result.next_due_mono)
            continue
        break
    return advanced


def _consume_until_converged(
    session: ReplaySession, clock: SimulatedMonotonicClock, max_pumps: int = 100000
) -> int:
    advanced = 0
    pumps = 0
    while pumps < max_pumps:
        result = session.pump_playback()
        pumps += 1
        if result.action == "advanced":
            advanced += 1
            continue
        if result.action == "not_due":
            if result.next_due_mono is None:
                break
            clock.set_now(result.next_due_mono)
            continue
        break
    return advanced


def _ts(value: str) -> datetime:
    return datetime.strptime(value, _MARKET_TS)


class _SessionTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.executors: list[BoundedComputationExecutor] = []

    def tearDown(self) -> None:
        for ex in self.executors:
            ex.shutdown(cancel_pending=True, wait=True)

    def _real_executor(self, **kw: Any) -> BoundedComputationExecutor:
        ex = BoundedComputationExecutor(**kw)
        self.executors.append(ex)
        return ex

    def _make_session(
        self,
        prepared,
        *,
        events: list | None = None,
        clock: SimulatedMonotonicClock | None = None,
        executor: Any | None = None,
        analyzer: Any = _ANALYZER_DEFAULT,
        deadline_seconds: float | None = None,
        initial_operation_id: str | None = "op-begin",
        auto_ready: bool = True,
    ) -> ReplaySession:
        if events is None:
            events = []
        if clock is None:
            clock = SimulatedMonotonicClock()
        if executor is None:
            executor = self._real_executor(capacity=8, worker_count=1)
        if analyzer is _ANALYZER_DEFAULT:
            analyzer = _CachingAnalyzer(_default_analyze_5m)
        return ReplaySession(
            "replay-test",
            1,
            prepared,
            executor,
            clock=clock,
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=analyzer,
            initial_operation_id=initial_operation_id,
            auto_ready=auto_ready,
            deadline_seconds=deadline_seconds,
        )


# ----------------------------------------------------------------------
# 1. Initial state
# ----------------------------------------------------------------------


class InitialStateTests(_SessionTestBase):
    def test_initial_state_1m(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        self.assertEqual(s.state, "ready")
        self.assertEqual(s.revision, 0)
        self.assertEqual(s.current_time, prepared.start_time)
        self.assertEqual(s.next_bar_time, prepared.actual_bar_times[0])
        self.assertEqual(s.end_time, prepared.market_session.end)
        self.assertEqual(s.playback_speed, 1)
        self.assertEqual(s.step_seconds, 60)
        self.assertEqual(s.granularity, "one_minute")
        self.assertFalse(s.playing)
        # The ready snapshot is the only event and carries begin's operation_id.
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "workbench_snapshot")
        self.assertEqual(events[0]["revision"], 0)
        self.assertEqual(events[0]["operation_id"], "op-begin")

    def test_end_time_comes_from_calendar_not_data_tail(self) -> None:
        prepared = _prepare("1m")
        s = self._make_session(prepared)
        self.assertEqual(s.end_time, prepared.market_session.end)
        self.assertEqual(s.end_time, datetime(2026, 7, 24, 15, 0))
        self.assertEqual(prepared.end_time, prepared.market_session.end)

    def test_initial_state_5m_degradation(self) -> None:
        prepared = _prepare("5m")
        events: list = []
        s = self._make_session(prepared, events=events)
        self.assertEqual(s.granularity, "five_minute")
        self.assertEqual(s.step_seconds, 300)
        self.assertEqual(s.current_time, prepared.start_time)
        self.assertEqual(s.next_bar_time, prepared.actual_bar_times[0])
        self.assertEqual(s.end_time, prepared.market_session.end)


# ----------------------------------------------------------------------
# 2. Step
# ----------------------------------------------------------------------


class StepTests(_SessionTestBase):
    def test_step_advances_one_actual_1m_bar(self) -> None:
        prepared = _prepare("1m")
        s = self._make_session(prepared)
        first = prepared.actual_bar_times[0]
        second = prepared.actual_bar_times[1]
        self.assertEqual(s.next_bar_time, first)

        result = s.step("op-step-1")
        self.assertTrue(result.advanced)
        self.assertEqual(result.outcome_status, "completed")
        self.assertEqual(s.current_time, first)
        self.assertEqual(s.next_bar_time, second)
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.revision, 2)

    def test_step_advances_one_actual_5m_bar(self) -> None:
        prepared = _prepare("5m")
        s = self._make_session(prepared)
        first = prepared.actual_bar_times[0]
        second = prepared.actual_bar_times[1]
        result = s.step("op-step-1")
        self.assertTrue(result.advanced)
        self.assertEqual(s.current_time, first)
        self.assertEqual(s.next_bar_time, second)

    def test_advance_task_uses_bounded_computation_contract(self) -> None:
        prepared = _prepare("1m")
        scripted = _ScriptedExecutor(
            [
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=_ready_value(prepared),
                )
            ]
        )
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            scripted,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        task = scripted.submitted[0]
        self.assertEqual(task.priority, ComputationPriority.REPLAY_INTERACTIVE)
        self.assertEqual(task.session_id, "replay-test")
        self.assertEqual(task.pipeline_identity.session_id, "replay-test")
        self.assertEqual(task.pipeline_identity.generation, 1)
        self.assertIsNotNone(task.accept_result)
        self.assertIsNotNone(task.is_session_valid)
        self.assertIsNotNone(task.is_cancelled)
        self.assertIsNotNone(task.commit_result)

    def test_step_skips_lunch_without_fabricating_a_bar(self) -> None:
        prepared = _prepare("5m")
        s = self._make_session(prepared)
        target_index = None
        for index, t in enumerate(prepared.actual_bar_times):
            if t == datetime(2026, 7, 24, 11, 30):
                target_index = index
                break
        self.assertIsNotNone(target_index)
        for _ in range(target_index + 1):
            s.step("op-step")
        self.assertEqual(s.current_time, datetime(2026, 7, 24, 11, 30))
        # The next actual bar after 11:30 is the first afternoon bar (13:05),
        # not a lunch-time slot.
        s.step("op-step-lunch")
        self.assertEqual(s.current_time, datetime(2026, 7, 24, 13, 5))

    def test_step_atomic_cursor_pipeline_snapshot(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        rev_before = s.revision
        first = prepared.actual_bar_times[0]
        s.step("op-step-1")
        # cursor, pipeline target, revision and snapshot are consistent.
        # The first step from ready emits an extra paused session_status, so
        # revision grows by two (status + snapshot) rather than one.
        self.assertEqual(s.current_time, first)
        self.assertEqual(s._pipeline.target_time, first)
        self.assertEqual(s.revision, rev_before + 2)
        snap = s.snapshot()
        self.assertEqual(snap["session"]["revision"], s.revision)
        self.assertEqual(snap["replay"]["current_time"], first.strftime(_MARKET_TS))
        # The snapshot event carries the step's operation_id.
        self.assertEqual(events[-1]["operation_id"], "op-step-1")


# ----------------------------------------------------------------------
# 3. Endpoint idempotence (step)
# ----------------------------------------------------------------------


class EndpointIdempotentTests(_SessionTestBase):
    def _step_to_end(self, s: ReplaySession, prepared) -> None:
        for _ in range(len(prepared.actual_bar_times)):
            s.step("op-step")

    def test_step_at_end_is_idempotent_noop(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        self._step_to_end(s, prepared)
        self.assertIsNone(s.next_bar_time)
        self.assertEqual(s.current_time, prepared.actual_bar_times[-1])

        revision_before = s.revision
        event_count_before = len(events)
        result = s.step("op-step-end")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "no_op")
        self.assertEqual(s.revision, revision_before)
        self.assertEqual(len(events), event_count_before)
        self.assertEqual(s.end_time, prepared.market_session.end)

    def test_repeated_step_at_end_does_not_create_operation(self) -> None:
        prepared = _prepare("1m")
        s = self._make_session(prepared)
        self._step_to_end(s, prepared)
        r1 = s.step("op-a")
        r2 = s.step("op-b")
        self.assertFalse(r1.advanced)
        self.assertFalse(r2.advanced)
        self.assertIsNone(r1.operation_id)
        self.assertIsNone(r2.operation_id)


# ----------------------------------------------------------------------
# 4. Play and pause
# ----------------------------------------------------------------------


class PlayPauseTests(_SessionTestBase):
    def test_play_advances_in_order(self) -> None:
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, clock=clock)
        s.play()
        self.assertTrue(s.playing)
        self.assertEqual(s.state, "playing")
        advanced = _consume_for_seconds(s, clock, 3.0)
        self.assertEqual(advanced, 3)
        self.assertEqual(s.current_time, prepared.actual_bar_times[2])
        self.assertEqual(s.next_bar_time, prepared.actual_bar_times[3])

    def test_pause_stops_new_advances(self) -> None:
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, clock=clock)
        s.play()
        _consume_for_seconds(s, clock, 2.0)
        self.assertEqual(s.current_time, prepared.actual_bar_times[1])
        s.pause()
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.pump_playback().action, "no_op")
        clock.advance(100.0)
        self.assertEqual(s.pump_playback().action, "no_op")
        self.assertEqual(s.current_time, prepared.actual_bar_times[1])

    def test_pause_is_idempotent_from_paused(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, events=events, clock=clock)
        s.play()
        _consume_for_seconds(s, clock, 1.0)
        s.pause()
        revision_after_pause = s.revision
        events_after_pause = len(events)
        s.pause()  # idempotent
        self.assertEqual(s.revision, revision_after_pause)
        self.assertEqual(len(events), events_after_pause)

    def test_pause_is_idempotent_from_ready(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        revision_before = s.revision
        events_before = len(events)
        s.pause()  # ready -> idempotent no-op
        self.assertEqual(s.state, "ready")
        self.assertEqual(s.revision, revision_before)
        self.assertEqual(len(events), events_before)

    def test_late_result_during_pause_does_not_advance(self) -> None:
        prepared = _prepare("1m")
        gated = _GatedAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, events=[], clock=clock, analyzer=gated)
        gated.block()
        s.play()
        # Push the clock past the first due time so the pump submits at once;
        # these tests exercise concurrency, not cadence.
        clock.set_now(1000.0)
        cursor_before = s.current_time

        done = threading.Event()
        error_box: list = []

        def _pump() -> None:
            try:
                s.pump_playback()
            except Exception as exc:  # pragma: no cover - test guard
                error_box.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        self.assertTrue(gated.wait_until_entered())
        s.pause()
        gated.release()
        self.assertTrue(done.wait(timeout=5.0))
        thread.join(timeout=5.0)
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.current_time, cursor_before)
        self.assertFalse(error_box)


# ----------------------------------------------------------------------
# 5. Speed
# ----------------------------------------------------------------------


class SpeedTests(_SessionTestBase):
    def test_speed_consumes_corresponding_bars_per_simulated_second_1m(self) -> None:
        for speed in (1, 2, 5, 10):
            prepared = _prepare("1m")
            clock = SimulatedMonotonicClock()
            s = self._make_session(prepared, clock=clock)
            s.set_playback_speed(speed)
            s.play()
            advanced = _consume_for_seconds(s, clock, 1.0)
            self.assertEqual(advanced, speed)

    def test_speed_consumes_corresponding_bars_per_simulated_second_5m(self) -> None:
        for speed in (1, 2, 5, 10):
            prepared = _prepare("5m")
            clock = SimulatedMonotonicClock()
            s = self._make_session(prepared, clock=clock)
            s.set_playback_speed(speed)
            s.play()
            advanced = _consume_for_seconds(s, clock, 1.0)
            self.assertEqual(advanced, speed)

    def test_speed_change_mid_play_takes_effect_immediately(self) -> None:
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, clock=clock)
        s.play()  # 1x
        first_batch = _consume_for_seconds(s, clock, 1.0)
        self.assertEqual(first_batch, 1)
        s.set_playback_speed(10)
        second_batch = _consume_for_seconds(s, clock, 2.0)
        self.assertEqual(second_batch, 10)

    def test_repeat_speed_is_idempotent_noop(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        s.set_playback_speed(5)
        revision_after = s.revision
        events_after = len(events)
        s.set_playback_speed(5)  # idempotent
        self.assertEqual(s.revision, revision_after)
        self.assertEqual(len(events), events_after)
        self.assertEqual(s.playback_speed, 5)

    def test_illegal_speed_rejected_without_state_change(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        s.set_playback_speed(5)
        revision_before = s.revision
        events_before = len(events)
        for illegal in (0, 3, 4, 7, 11, 20, 2.0, "1", True):
            with self.assertRaises((ReplaySessionError, ValueError, TypeError)):
                s.set_playback_speed(illegal)  # type: ignore[arg-type]
        self.assertEqual(s.playback_speed, 5)
        self.assertEqual(s.revision, revision_before)
        self.assertEqual(len(events), events_before)

    def test_speed_change_event_has_no_operation_id(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        s.set_playback_speed(2)
        status_events = [e for e in events if e["event_type"] == "session_status"]
        self.assertTrue(status_events)
        last = status_events[-1]
        self.assertEqual(last["payload"]["reason"], "playback_speed_changed")
        self.assertEqual(last["payload"]["playback_speed"], 2)
        self.assertNotIn("operation_id", last)


# ----------------------------------------------------------------------
# 6. Bounded computation and concurrency
# ----------------------------------------------------------------------


class BoundedConcurrentTests(_SessionTestBase):
    def test_at_most_one_inflight_advance(self) -> None:
        prepared = _prepare("1m")
        gated = _GatedAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        real = self._real_executor(capacity=8, worker_count=1)
        counting = _CountingExecutor(real)
        clock = SimulatedMonotonicClock()
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            counting,
            clock=clock,
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=gated,
            initial_operation_id="op-begin",
        )
        gated.block()
        s.play()
        # Push the clock past the first due time so the pump submits at once.
        clock.set_now(1000.0)

        done = threading.Event()

        def _pump() -> None:
            s.pump_playback()
            done.set()

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        self.assertTrue(gated.wait_until_entered())

        submit_count_after_first = counting.submit_count
        for _ in range(5):
            result = s.pump_playback()
            self.assertEqual(result.action, "no_op")
        self.assertEqual(counting.submit_count, submit_count_after_first)

        gated.release()
        self.assertTrue(done.wait(timeout=5.0))
        thread.join(timeout=5.0)
        self.assertEqual(s.current_time, prepared.actual_bar_times[0])

    def test_failed_result_does_not_advance_cursor(self) -> None:
        prepared = _prepare("1m")
        raising = _RaisingAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        events: list = []
        s = self._make_session(prepared, events=events, analyzer=raising)
        cursor_before = s.current_time
        revision_before = s.revision
        result = s.step("op-fail")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.current_time, cursor_before)
        # ready -> paused -> failed: two status events, so revision grows by 2.
        self.assertEqual(s.revision, revision_before + 2)
        status = [e for e in events if e["event_type"] == "session_status"]
        self.assertTrue(status)
        self.assertEqual(status[-1]["payload"]["state"], "failed")
        self.assertEqual(status[-1]["operation_id"], "op-fail")

    def test_deadline_exceeded_does_not_advance_cursor(self) -> None:
        prepared = _prepare("1m")
        scripted = _ScriptedExecutor(
            [
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=_ready_value(prepared),
                ),
                ComputationOutcome(
                    task_id="step",
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.DEADLINE_EXCEEDED,
                ),
            ]
        )
        events: list = []
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            scripted,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        cursor_before = s.current_time
        result = s.step("op-deadline")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.current_time, cursor_before)

    def test_session_invalid_result_does_not_advance_cursor(self) -> None:
        prepared = _prepare("1m")
        scripted = _ScriptedExecutor(
            [
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=_ready_value(prepared),
                ),
                ComputationOutcome(
                    task_id="step",
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.SESSION_INVALID,
                ),
            ]
        )
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            scripted,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        cursor_before = s.current_time
        result = s.step("op-invalid")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "dropped")
        # The step transitions to paused before submitting, even if the result
        # is ultimately dropped; the cursor does not advance.
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.current_time, cursor_before)

    def test_retired_session_late_result_is_dropped(self) -> None:
        prepared = _prepare("1m")
        gated = _GatedAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        events: list = []
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, events=events, clock=clock, analyzer=gated)
        gated.block()
        s.play()
        # Push the clock past the first due time so the pump submits at once.
        clock.set_now(1000.0)
        cursor_before = s.current_time

        done = threading.Event()

        def _pump() -> None:
            s.pump_playback()
            done.set()

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        self.assertTrue(gated.wait_until_entered())
        events_before_retire = len(events)
        s.retire()
        self.assertEqual(s.state, "retired")
        gated.release()
        self.assertTrue(done.wait(timeout=5.0))
        thread.join(timeout=5.0)
        self.assertEqual(s.current_time, cursor_before)
        new_events = events[events_before_retire:]
        self.assertEqual(len(new_events), 1)
        self.assertEqual(new_events[0]["event_type"], "session_status")
        self.assertEqual(new_events[0]["payload"]["state"], "retired")


# ----------------------------------------------------------------------
# 7. Endpoint convergence (auto-playback)
# ----------------------------------------------------------------------


class EndpointConvergenceTests(_SessionTestBase):
    def test_autoplay_consumes_last_bar_then_converges_once(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, events=events, clock=clock)
        s.set_playback_speed(10)
        s.play()
        advanced = _consume_until_converged(s, clock)
        self.assertEqual(advanced, len(prepared.actual_bar_times))
        self.assertIsNone(s.next_bar_time)
        self.assertEqual(s.current_time, prepared.actual_bar_times[-1])
        self.assertEqual(s.state, "paused")

        status_events = [e for e in events if e["event_type"] == "session_status"]
        converged = [e for e in status_events if e["payload"]["state"] == "paused"]
        self.assertEqual(len(converged), 1)
        self.assertNotIn("operation_id", converged[0])
        self.assertNotIn("reason", converged[0]["payload"])

        revision_after_convergence = s.revision
        events_after = len(events)
        self.assertEqual(s.pump_playback().action, "no_op")
        self.assertEqual(s.step("op-after").outcome_status, "no_op")
        self.assertEqual(s.revision, revision_after_convergence)
        self.assertEqual(len(events), events_after)

    def test_converged_session_end_time_unchanged(self) -> None:
        prepared = _prepare("5m")
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, clock=clock)
        s.set_playback_speed(10)
        s.play()
        _consume_until_converged(s, clock)
        self.assertEqual(s.end_time, prepared.market_session.end)
        self.assertEqual(s.end_time, datetime(2026, 7, 24, 15, 0))


# ----------------------------------------------------------------------
# 8. Contract consistency
# ----------------------------------------------------------------------


class ContractTests(_SessionTestBase):
    def test_full_snapshot_validates_against_replay_schema(self) -> None:
        prepared = _prepare("1m")
        s = self._make_session(prepared, analyzer=None)  # real analyzer
        s.step("op-step-1")
        s.step("op-step-2")
        snap = s.snapshot()
        validate_replay_snapshot(snap, expected_session_id="replay-test")
        self.assertEqual(snap["session"]["session_type"], "replay")
        self.assertEqual(snap["session"]["state"], "paused")
        self.assertEqual(snap["session"]["revision"], s.revision)
        self.assertEqual(snap["replay"]["granularity"], "one_minute")
        self.assertEqual(snap["replay"]["step_seconds"], 60)
        self.assertEqual(snap["replay"]["playback_speed"], 1)
        self.assertFalse(snap["replay"]["playing"])
        self.assertEqual(snap["replay"]["current_time"], s.current_time.strftime(_MARKET_TS))
        self.assertEqual(snap["replay"]["next_bar_time"], s.next_bar_time.strftime(_MARKET_TS))

    def test_playing_snapshot_has_playing_true(self) -> None:
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, clock=clock, analyzer=None)
        s.play()
        _consume_for_seconds(s, clock, 1.0)
        self.assertTrue(s.playing)
        snap = s.snapshot()
        validate_replay_snapshot(snap, expected_session_id="replay-test")
        self.assertEqual(snap["session"]["state"], "playing")
        self.assertTrue(snap["replay"]["playing"])

    def test_no_future_data_in_snapshot(self) -> None:
        prepared = _prepare("1m")
        s = self._make_session(prepared, analyzer=None)
        for _ in range(30):
            s.step("op-step")
        current = s.current_time
        snap = s.snapshot()
        validate_replay_snapshot(snap, expected_session_id="replay-test")

        for bar in snap["market"]["bars_1m"]:
            self.assertLessEqual(_ts(bar["timestamp"]), current)
        for bar in snap["market"]["bars_5m"]:
            self.assertLessEqual(_ts(bar["timestamp"]), current)
        for bar in snap["market"]["daily_bars"]:
            self.assertLessEqual(_ts(bar["timestamp"] + " 00:00:00"), current)
        if snap["market"]["quote"] is not None:
            self.assertLessEqual(_ts(snap["market"]["quote"]["timestamp"]), current)
        for tf in ("five_minute", "one_minute"):
            self._assert_indicators_no_future(snap["indicators"][tf], current)
        for prim in snap["chan_analysis"].get("plot_primitives", []):
            ts = prim.get("timestamp") if isinstance(prim, dict) else None
            if ts:
                self.assertLessEqual(_ts(ts), current)

    def _assert_indicators_no_future(self, node: dict, current: datetime) -> None:
        for value in node.values():
            if isinstance(value, list):
                for point in value:
                    if isinstance(point, dict) and "timestamp" in point:
                        self.assertLessEqual(_ts(point["timestamp"]), current)
            elif isinstance(value, dict):
                self._assert_indicators_no_future(value, current)

    def test_revision_sequence_strictly_monotonic(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        s.step("op-1")
        s.set_playback_speed(2)
        s.step("op-2")
        revisions = [e["revision"] for e in events]
        self.assertEqual(revisions, sorted(set(revisions)))
        self.assertEqual(revisions[-1], s.revision)
        for e in events:
            if e["event_type"] == "workbench_snapshot":
                self.assertEqual(e["payload"]["session"]["revision"], e["revision"])

    def test_operation_id_carrying_rules(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        clock = SimulatedMonotonicClock()
        s = self._make_session(prepared, events=events, clock=clock)
        self.assertEqual(events[0]["operation_id"], "op-begin")
        s.step("op-step-1")
        # The first step from ready publishes a paused session_status carrying
        # the step's operation_id, followed by the workbench snapshot.
        paused_event = events[-2]
        self.assertEqual(paused_event["event_type"], "session_status")
        self.assertEqual(paused_event["payload"]["state"], "paused")
        self.assertEqual(paused_event["operation_id"], "op-step-1")
        step_event = events[-1]
        self.assertEqual(step_event["event_type"], "workbench_snapshot")
        self.assertEqual(step_event["operation_id"], "op-step-1")
        s.play()
        play_event = events[-1]
        self.assertEqual(play_event["event_type"], "session_status")
        self.assertNotIn("operation_id", play_event)
        _consume_for_seconds(s, clock, 1.0)
        playback_snaps = [
            e for e in events
            if e["event_type"] == "workbench_snapshot" and e["revision"] > step_event["revision"]
        ]
        self.assertTrue(playback_snaps)
        for e in playback_snaps:
            self.assertNotIn("operation_id", e)
        s.pause()
        pause_event = events[-1]
        self.assertNotIn("operation_id", pause_event)


# ----------------------------------------------------------------------
# 9. Regression tests for review findings
# ----------------------------------------------------------------------


class RegressionTests(_SessionTestBase):
    def test_step_from_ready_publishes_paused_session_status(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        s = self._make_session(prepared, events=events)
        self.assertEqual(s.state, "ready")
        s.step("op-step-1")
        # The step emitted a paused session_status (carrying operation_id) and
        # then the workbench snapshot.  No two events share the same revision.
        status_events = [e for e in events if e["event_type"] == "session_status"]
        paused = [e for e in status_events if e["payload"]["state"] == "paused"]
        self.assertEqual(len(paused), 1)  # only the step's paused status
        step_paused = paused[-1]
        self.assertEqual(step_paused["operation_id"], "op-step-1")
        revisions = [e["revision"] for e in events]
        self.assertEqual(len(revisions), len(set(revisions)))
        # snapshot() is consistent with the last published state.
        snap = s.snapshot()
        self.assertEqual(snap["session"]["state"], "paused")
        self.assertEqual(snap["session"]["revision"], s.revision)

    def test_queue_full_and_executor_closed_produce_cancelled_reasons(self) -> None:
        for exc_class in (ComputationQueueFullError, ComputationExecutorClosedError):
            with self.subTest(exc_class=exc_class):
                prepared = _prepare("1m")
                ex = _BrokenExecutor(exc_class("boom"))
                s = ReplaySession(
                    "replay-test",
                    1,
                    prepared,
                    ex,
                    clock=SimulatedMonotonicClock(),
                    scheduler=NullPlaybackScheduler(),
                    on_event=lambda _e: None,
                    analyzer=_CachingAnalyzer(_default_analyze_5m),
                    initial_operation_id="op-begin",
                )
                self.assertIsNone(s._inflight_ticket)
                self.assertEqual(s.state, "failed")

    def test_timeout_closes_late_commit_window(self) -> None:
        prepared = _prepare("1m")
        # Ready uses a real executor so the pipeline commits; the step submit
        # is then forced to timeout so we can verify the late result is rejected.
        real_ex = BoundedComputationExecutor(capacity=8, worker_count=1)
        self.executors.append(real_ex)
        ex = _ReadyThenTimeoutExecutor(real_ex, timeout_after=2)
        events: list = []
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        self.assertEqual(s._pipeline.target_time, prepared.start_time)

        result = s.step("op-timeout")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertIsNone(s._inflight_ticket)
        self.assertEqual(s.state, "failed")

        # The step task was captured; its late commit must be rejected by the
        # per-task cancellation flag.
        step_task = ex.submitted[-1]
        self.assertFalse(step_task.accept_result(_ready_value(prepared)))
        step_task.commit_result(_ready_value(prepared))
        self.assertEqual(s.current_time, prepared.start_time)
        self.assertEqual(s._pipeline.target_time, prepared.start_time)

    def test_timeout_preserves_cancel_reason(self) -> None:
        prepared = _prepare("1m")
        ex = _TimeoutExecutor()
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
            auto_ready=False,
            computation_timeout=0.0,
        )
        # Manually call _submit_advance to inspect the structured outcome.
        outcome = s._submit_advance(prepared.start_time, "op-test", None, 1)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.DEADLINE_EXCEEDED)
        self.assertIsInstance(outcome.exception, TimeoutError)

    def test_play_while_step_inflight_raises_replay_busy(self) -> None:
        prepared = _prepare("1m")
        gated = _GatedAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        s = self._make_session(prepared, analyzer=gated)
        gated.block()

        def _step() -> None:
            s.step("op-step")

        thread = threading.Thread(target=_step, daemon=True)
        thread.start()
        self.assertTrue(gated.wait_until_entered())

        with self.assertRaisesRegex(ReplaySessionError, "replay_busy"):
            s.play()

        gated.release()
        thread.join(timeout=5.0)

    def test_timer_scheduler_reschedules_on_speed_change(self) -> None:
        """Playback at 1x then switch to 10x; the next wake follows new speed."""
        prepared = _prepare("1m")
        clock = SimulatedMonotonicClock()
        scheduler = TimerPlaybackScheduler(lambda: None, clock=clock)
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            self._real_executor(capacity=8, worker_count=1),
            clock=clock,
            scheduler=scheduler,
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        # Pump manually to reach the first scheduled wake.
        s.play()
        # Advance just a bit; still before the 1x due time of 1.0.
        clock.advance(0.1)
        # Speed change to 10x should re-schedule to t = 0.1 + 0.1 = 0.2.
        s.set_playback_speed(10)
        # Pump at the new due time; it should be due now.
        clock.set_now(0.2)
        result = s.pump_playback()
        self.assertEqual(result.action, "advanced")
        self.assertEqual(s.current_time, prepared.actual_bar_times[0])

    def test_failed_session_retired_converges_state(self) -> None:
        prepared = _prepare("1m")
        raising = _RaisingAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        events: list = []
        s = self._make_session(prepared, events=events, analyzer=raising)
        s.step("op-fail")
        self.assertEqual(s.state, "failed")
        revision_before = s.revision
        events_before = len(events)
        s.retire()
        self.assertEqual(s.state, "retired")
        self.assertEqual(s.revision, revision_before + 1)
        self.assertEqual(len(events), events_before + 1)
        self.assertEqual(events[-1]["payload"]["state"], "retired")

    def test_commit_result_guarded_against_pause_during_accept(self) -> None:
        prepared = _prepare("1m")
        ready_value = _ready_value(prepared)
        step_value = ComputationOutcome(
            task_id="step",
            status=ComputationStatus.COMPLETED,
            value=ready_value,
        )
        # The executor resolves the ready task immediately and holds the step
        # task until the test releases it, letting us interpose between
        # accept_result and commit_result.
        ex = _HoldingExecutor(step_value)
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )

        def _step() -> None:
            s.step("op-step")

        thread = threading.Thread(target=_step, daemon=True)
        thread.start()
        # Wait for the step task to be submitted (it will block on the future).
        while len(ex.submitted) < 2:
            threading.Event().wait(timeout=0.01)
        step_task = ex.submitted[-1]

        # accept_result sees an in-flight ticket and valid paused state.
        self.assertTrue(step_task.accept_result(step_value.value))
        # Now retire before the executor can commit.  This is the race window.
        s.retire()
        # commit_result must no-op because the Session is retired.
        step_task.commit_result(step_value.value)
        self.assertEqual(s.state, "retired")
        self.assertEqual(s.current_time, prepared.start_time)

        # Release the step future so the step thread can finish cleanly.
        ex.futures[-1].set_outcome(step_value)
        thread.join(timeout=5.0)

    def test_timeout_after_commit_still_advances_cursor(self) -> None:
        """Regression: commit_result before future.set_result must not fail the step."""
        prepared = _prepare("1m")
        ready_value = _ready_value(prepared)
        step_pipe = WorkbenchPipeline(
            session=prepared.market_session,
            market_input_port=prepared.market_input_port,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
        )
        step_value = step_pipe.preview(prepared.actual_bar_times[0])
        ex = _CommitThenTimeoutExecutor(ready_value, step_value)
        events: list = []
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
            computation_timeout=0.1,
        )
        cursor_before = s.current_time
        target = prepared.actual_bar_times[0]

        result = s.step("op-timeout-after-commit")

        self.assertTrue(result.advanced)
        self.assertEqual(result.outcome_status, "completed")
        self.assertIsNotNone(result.outcome)
        self.assertEqual(result.outcome.status, ComputationStatus.COMPLETED)
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.current_time, target)
        self.assertEqual(s._pipeline.target_time, target)
        self.assertGreater(s.revision, 0)
        # The cursor moved exactly one bar from the pre-timeout position.
        self.assertEqual(s.current_time, prepared.actual_bar_times[0])
        self.assertEqual(s._pipeline.target_time, prepared.actual_bar_times[0])

    def test_retire_after_commit_does_not_split_or_publish_late_snapshot(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        ex = _CommitThenReleaseExecutor(_ready_value(prepared))
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
            computation_timeout=5.0,
        )
        results: list = []
        thread = threading.Thread(
            target=lambda: results.append(s.step("op-step")),
            daemon=True,
        )
        thread.start()
        while ex.pending is None:
            threading.Event().wait(timeout=0.01)
        self.assertTrue(ex.pending.committed.wait(timeout=5.0))

        target = prepared.actual_bar_times[0]
        self.assertEqual(s.current_time, target)
        self.assertEqual(s._pipeline.target_time, target)
        self.assertEqual(events[-1]["event_type"], "workbench_snapshot")
        self.assertEqual(events[-1]["operation_id"], "op-step")
        committed_revision = events[-1]["revision"]

        s.retire()
        revision_after_retire = s.revision
        event_count_after_retire = len(events)
        ex.pending.release.set()
        thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0].advanced)
        self.assertEqual(results[0].revision, committed_revision)
        self.assertLess(results[0].revision, revision_after_retire)
        self.assertEqual(s.state, "retired")
        self.assertEqual(s.current_time, s._pipeline.target_time)
        self.assertEqual(s.revision, revision_after_retire)
        self.assertEqual(len(events), event_count_after_retire)
        self.assertEqual(events[-1]["payload"]["state"], "retired")

    def test_pause_after_playback_commit_does_not_split_or_publish_late_snapshot(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        clock = SimulatedMonotonicClock()
        ex = _CommitThenReleaseExecutor(_ready_value(prepared))
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=clock,
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
            computation_timeout=5.0,
        )
        s.play()
        clock.set_now(1.0)
        results: list = []
        thread = threading.Thread(
            target=lambda: results.append(s.pump_playback()),
            daemon=True,
        )
        thread.start()
        while ex.pending is None:
            threading.Event().wait(timeout=0.01)
        self.assertTrue(ex.pending.committed.wait(timeout=5.0))

        target = prepared.actual_bar_times[0]
        self.assertEqual(s.current_time, target)
        self.assertEqual(s._pipeline.target_time, target)
        self.assertEqual(events[-1]["event_type"], "workbench_snapshot")

        s.pause()
        revision_after_pause = s.revision
        event_count_after_pause = len(events)
        ex.pending.release.set()
        thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].action, "advanced")
        self.assertIsNone(results[0].next_due_mono)
        self.assertEqual(s.state, "paused")
        self.assertEqual(s.current_time, s._pipeline.target_time)
        self.assertEqual(s.revision, revision_after_pause)
        self.assertEqual(len(events), event_count_after_pause)
        self.assertEqual(events[-1]["payload"]["state"], "paused")

    def test_initial_result_reserves_revision_for_t044_mapping(self) -> None:
        prepared = _prepare("1m")
        events: list = []
        expected = ComputationOutcome(
            task_id="ready-failed",
            status=ComputationStatus.FAILED,
            exception=RuntimeError("boom"),
        )
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            _ScriptedExecutor([expected]),
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )

        self.assertEqual(events[-1]["event_type"], "session_status")
        self.assertEqual(events[-1]["payload"]["state"], "failed")
        failed_status_revision = events[-1]["revision"]

        result = s.take_initial_result()
        self.assertIsNotNone(result)
        self.assertIs(result.outcome, expected)
        self.assertEqual(result.revision, failed_status_revision + 1)
        self.assertEqual(s.revision, result.revision)
        error, channel = map_computation_outcome_to_replay_error(result.outcome)
        self.assertEqual(error.error_code, "calculation_failed")
        self.assertEqual(channel.value, "operation_failed")
        self.assertIsNone(s.take_initial_result())

        # A later Session event must advance beyond the revision reserved for
        # operation_failed instead of reusing it.
        s.retire()
        self.assertEqual(events[-1]["event_type"], "session_status")
        self.assertEqual(events[-1]["payload"]["state"], "retired")
        self.assertEqual(events[-1]["revision"], result.revision + 1)

    def test_step_result_preserves_failed_outcome_for_t044_mapping(self) -> None:
        prepared = _prepare("1m")
        raising = _RaisingAnalyzer(_CachingAnalyzer(_default_analyze_5m))
        s = self._make_session(prepared, analyzer=raising)
        result = s.step("op-fail")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertIsNotNone(result.outcome)
        self.assertEqual(result.outcome.status, ComputationStatus.FAILED)
        error, channel = map_computation_outcome_to_replay_error(result.outcome)
        self.assertEqual(error.error_code, "calculation_failed")
        self.assertEqual(channel.value, "operation_failed")

    def test_step_result_preserves_deadline_exceeded_outcome(self) -> None:
        prepared = _prepare("1m")
        ex = _FirstCompleteThenTimeoutExecutor(_ready_value(prepared))
        events: list = []
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            ex,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=events.append,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
            computation_timeout=0.0,
        )
        result = s.step("op-deadline")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertIsNotNone(result.outcome)
        self.assertEqual(result.outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(result.outcome.cancel_reason, CancelReason.DEADLINE_EXCEEDED)
        error, channel = map_computation_outcome_to_replay_error(result.outcome)
        self.assertEqual(error.error_code, "calculation_failed")
        self.assertEqual(error.details.get("cancel_reason"), "deadline_exceeded")
        self.assertEqual(channel.value, "operation_failed")

    def test_step_result_preserves_executor_closed_outcome(self) -> None:
        prepared = _prepare("1m")
        scripted = _ScriptedExecutor(
            [
                ComputationOutcome(
                    task_id="ready",
                    status=ComputationStatus.COMPLETED,
                    value=_ready_value(prepared),
                ),
                ComputationOutcome(
                    task_id="step",
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.EXECUTOR_CLOSED,
                ),
            ]
        )
        s = ReplaySession(
            "replay-test",
            1,
            prepared,
            scripted,
            clock=SimulatedMonotonicClock(),
            scheduler=NullPlaybackScheduler(),
            on_event=lambda _e: None,
            analyzer=_CachingAnalyzer(_default_analyze_5m),
            initial_operation_id="op-begin",
        )
        result = s.step("op-executor-closed")
        self.assertFalse(result.advanced)
        self.assertEqual(result.outcome_status, "failed")
        self.assertIsNotNone(result.outcome)
        self.assertEqual(result.outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(result.outcome.cancel_reason, CancelReason.EXECUTOR_CLOSED)
        error, channel = map_computation_outcome_to_replay_error(result.outcome)
        self.assertEqual(error.error_code, "service_unavailable")
        self.assertEqual(channel.value, "operation_failed")


if __name__ == "__main__":
    unittest.main()
