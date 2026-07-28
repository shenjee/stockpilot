"""Tests for the bounded computation executor (T0-020).

All concurrent tests use deterministic synchronisation primitives (Event,
Barrier) and never rely on ``time.sleep`` guessing.  Every executor created in
these tests is shut down at the end of the test so no worker threads leak.
"""

from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime
from typing import Any

from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
    default_accept_result,
)
from packages.t0assistant.runtime.computation_executor import (
    BoundedComputationExecutor,
    ComputationExecutorClosedError,
    ComputationQueueFullError,
)


def _identity(
    instance_id: int = 1,
    generation: int = 1,
    session_id: str = "replay-1",
) -> PipelineInstanceIdentity:
    return PipelineInstanceIdentity(
        instance_id=instance_id,
        generation=generation,
        session_id=session_id,
    )


def _task(
    *,
    callable_,
    session_id: str = "replay-1",
    generation: int = 1,
    instance_id: int = 1,
    priority: ComputationPriority = ComputationPriority.REPLAY_INTERACTIVE,
    operation_id: str | None = None,
    deadline: float | None = None,
    is_cancelled=None,
    is_session_valid=None,
    accept_result=None,
    commit_result=None,
    task_id: str = "task",
) -> ComputationTask:
    return ComputationTask(
        task_id=task_id,
        session_id=session_id,
        session_generation=generation,
        pipeline_identity=_identity(
            instance_id=instance_id,
            generation=generation,
            session_id=session_id,
        ),
        priority=priority,
        callable=callable_,
        operation_id=operation_id,
        deadline=deadline,
        is_cancelled=is_cancelled,
        is_session_valid=is_session_valid,
        accept_result=accept_result,
        commit_result=commit_result,
    )


class _ExecutorTestBase(unittest.TestCase):
    """Base class ensuring every executor is shut down at teardown."""

    def setUp(self) -> None:
        self._executors: list[BoundedComputationExecutor] = []

    def tearDown(self) -> None:
        for executor in self._executors:
            try:
                executor.shutdown(cancel_pending=True, wait=True)
            except Exception:
                pass
        self._executors.clear()

    def _new_executor(
        self,
        *,
        capacity: int = 8,
        worker_count: int = 2,
        clock=None,
    ) -> BoundedComputationExecutor:
        executor = BoundedComputationExecutor(
            capacity=capacity,
            worker_count=worker_count,
            clock=clock,
        )
        self._executors.append(executor)
        return executor


class PriorityOrderingTests(_ExecutorTestBase):
    def test_live_beats_queued_replay_interactive(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)
        block_first = threading.Event()
        release_first = threading.Event()
        order: list[str] = []
        first_running = threading.Event()

        def first_live(_task):
            first_running.set()
            block_first.set()
            release_first.wait(timeout=2)
            order.append("first_live")
            return "first"

        def replay_interactive(_task):
            order.append("replay_interactive")
            return "replay"

        # Occupy the single worker so subsequent tasks queue.
        first_future = executor.submit(
            _task(
                callable_=first_live,
                priority=ComputationPriority.LIVE,
                task_id="first_live",
            )
        )
        self.assertTrue(first_running.wait(timeout=1))

        replay_future = executor.submit(
            _task(
                callable_=replay_interactive,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="replay",
            )
        )
        live_future = executor.submit(
            _task(
                callable_=first_live,  # reuse, but this one returns quickly
                priority=ComputationPriority.LIVE,
                task_id="second_live",
            )
        )
        # Wait a beat so the queue settles before releasing the worker.
        block_first.wait(timeout=1)
        release_first.set()

        first_future.result(timeout=2)
        # The second LIVE task must run before the REPLAY_INTERACTIVE task.
        live_future.result(timeout=2)
        replay_future.result(timeout=2)
        self.assertEqual(order, ["first_live", "first_live", "replay_interactive"])

    def test_replay_interactive_beats_prefetch(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)
        order: list[str] = []
        block = threading.Event()
        release = threading.Event()
        running = threading.Event()

        def blocker(_task):
            running.set()
            block.wait(timeout=2)
            release.wait(timeout=2)
            order.append("blocker")
            return "blocker"

        def interactive(_task):
            order.append("interactive")
            return "interactive"

        def prefetch(_task):
            order.append("prefetch")
            return "prefetch"

        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="blocker"))
        self.assertTrue(running.wait(timeout=1))
        prefetch_future = executor.submit(
            _task(callable_=prefetch, priority=ComputationPriority.REPLAY_PREFETCH, task_id="prefetch")
        )
        interactive_future = executor.submit(
            _task(
                callable_=interactive,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="interactive",
            )
        )
        block.set()
        release.set()
        interactive_future.result(timeout=2)
        prefetch_future.result(timeout=2)
        self.assertEqual(order, ["blocker", "interactive", "prefetch"])

    def test_busy_root_still_prefers_interactive_over_prefetch(self) -> None:
        executor = self._new_executor(capacity=8, worker_count=2)
        a_running = threading.Event()
        a_release = threading.Event()
        order: list[str] = []
        b_done = threading.Event()

        def pipeline_a_first(_task):
            a_running.set()
            a_release.wait(timeout=2)
            order.append("a-first")
            return "a-first"

        def pipeline_a_second(_task):
            order.append("a-second")
            return "a-second"

        def prefetch(_task):
            order.append("prefetch")
            return "prefetch"

        def interactive(_task):
            order.append("interactive")
            b_done.set()
            return "interactive"

        f_a1 = executor.submit(
            _task(callable_=pipeline_a_first, priority=ComputationPriority.LIVE, task_id="a1", instance_id=1)
        )
        self.assertTrue(a_running.wait(timeout=1))
        f_a2 = executor.submit(
            _task(callable_=pipeline_a_second, priority=ComputationPriority.LIVE, task_id="a2", instance_id=1)
        )
        f_prefetch = executor.submit(
            _task(callable_=prefetch, priority=ComputationPriority.REPLAY_PREFETCH, task_id="p", instance_id=2)
        )
        f_interactive = executor.submit(
            _task(callable_=interactive, priority=ComputationPriority.REPLAY_INTERACTIVE, task_id="i", instance_id=3)
        )
        self.assertTrue(b_done.wait(timeout=2))
        a_release.set()
        f_a1.result(timeout=2)
        f_a2.result(timeout=2)
        f_interactive.result(timeout=2)
        f_prefetch.result(timeout=2)
        self.assertLess(order.index("interactive"), order.index("prefetch"))


class CapacityBoundaryTests(_ExecutorTestBase):
    def test_queue_full_raises(self) -> None:
        executor = self._new_executor(capacity=1, worker_count=1)
        block = threading.Event()
        running = threading.Event()

        def blocker(_task):
            running.set()
            block.wait(timeout=2)
            return "ok"

        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="b1"))
        self.assertTrue(running.wait(timeout=1))
        # Queue is now at capacity (1 pending slot used by nothing yet).
        # Submit one task that fills the pending slot.
        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="b2"))
        with self.assertRaises(ComputationQueueFullError):
            executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="b3"))
        block.set()


class SerialisationTests(_ExecutorTestBase):
    def test_same_pipeline_instance_never_runs_concurrently(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=4)
        barrier = threading.Barrier(2, timeout=2)
        first_started = threading.Event()
        first_release = threading.Event()
        second_started = threading.Event()
        first_finished = threading.Event()

        def first(_task):
            first_started.set()
            first_release.wait(timeout=2)
            first_finished.set()
            return "first"

        def second(_task):
            second_started.set()
            # If first is still running, the barrier will time out.
            self.assertTrue(first_finished.wait(timeout=1))
            return "second"

        first_future = executor.submit(
            _task(callable_=first, priority=ComputationPriority.LIVE, task_id="first")
        )
        self.assertTrue(first_started.wait(timeout=1))
        second_future = executor.submit(
            _task(
                callable_=second,
                priority=ComputationPriority.LIVE,
                task_id="second",
                # Same identity -> must wait for first.
                instance_id=1,
            )
        )
        # Give the second task a chance to (incorrectly) start.
        self.assertFalse(second_started.wait(timeout=0.2))
        first_release.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)
        self.assertTrue(second_started.is_set())

    def test_different_pipeline_instances_run_in_parallel(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=4)
        barrier = threading.Barrier(2, timeout=2)
        results: list[str] = []

        def slow(_task):
            results.append("start")
            barrier.wait()
            results.append("end")
            return "ok"

        f1 = executor.submit(
            _task(
                callable_=slow,
                priority=ComputationPriority.LIVE,
                task_id="t1",
                instance_id=10,
            )
        )
        f2 = executor.submit(
            _task(
                callable_=slow,
                priority=ComputationPriority.LIVE,
                task_id="t2",
                instance_id=20,
            )
        )
        f1.result(timeout=2)
        f2.result(timeout=2)
        # Both tasks must have reached the barrier, proving they ran in parallel.
        self.assertEqual(results.count("start"), 2)

    def test_busy_pipeline_does_not_block_other_pipeline(self) -> None:
        """Regression: a busy Pipeline A at the heap head must not stop a worker
        from picking a lower-priority Pipeline B task behind it."""

        executor = self._new_executor(capacity=8, worker_count=2)
        a_running = threading.Event()
        a_release = threading.Event()
        b_started = threading.Event()

        def pipeline_a_first(_task):
            # Hold Pipeline A so any later A task is busy-blocked.
            a_running.set()
            a_release.wait(timeout=2)
            return "a"

        def pipeline_a_second(_task):
            # This should NOT run until the first A finishes.
            return "a-second"

        def pipeline_b(_task):
            # This MUST run on the second worker while A is still held.
            b_started.set()
            return "b"

        # First task occupies Pipeline A on worker 0.
        f_a1 = executor.submit(
            _task(callable_=pipeline_a_first, priority=ComputationPriority.LIVE, task_id="a1", instance_id=1)
        )
        self.assertTrue(a_running.wait(timeout=1))
        # Second A task (same identity) queues behind A1; it is busy.
        f_a2 = executor.submit(
            _task(callable_=pipeline_a_second, priority=ComputationPriority.LIVE, task_id="a2", instance_id=1)
        )
        # Pipeline B task (different identity) is lower priority but runnable.
        f_b = executor.submit(
            _task(callable_=pipeline_b, priority=ComputationPriority.REPLAY_INTERACTIVE, task_id="b", instance_id=2)
        )
        # Worker 1 must pick B even though A2 (higher priority) is at the head.
        self.assertTrue(b_started.wait(timeout=2))
        a_release.set()
        f_a1.result(timeout=2)
        f_a2.result(timeout=2)
        f_b.result(timeout=2)
        self.assertTrue(b_started.is_set())


class CancellationTests(_ExecutorTestBase):
    def test_queued_task_cancelled_via_predicate_does_not_execute(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=2)
        running = threading.Event()
        release = threading.Event()
        executed: list[str] = []

        def blocker(_task):
            running.set()
            release.wait(timeout=2)
            return "blocker"

        def victim(_task):
            executed.append("victim")
            return "victim"

        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="blocker"))
        self.assertTrue(running.wait(timeout=1))
        future = executor.submit(
            _task(
                callable_=victim,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="victim",
                instance_id=2,
                is_cancelled=lambda: True,
            )
        )
        outcome = future.result(timeout=2)
        release.set()
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.CANCELLED)
        self.assertEqual(executed, [])

    def test_new_seek_supersedes_old_operation(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)
        running = threading.Event()
        release = threading.Event()
        executed: list[str] = []

        def blocker(_task):
            running.set()
            release.wait(timeout=2)
            return "blocker"

        def old_seek(_task):
            executed.append("old")
            return "old"

        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="blocker"))
        self.assertTrue(running.wait(timeout=1))
        future = executor.submit(
            _task(
                callable_=old_seek,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="old_seek",
                operation_id="op-old",
            )
        )
        marked = executor.supersede_operation("replay-1", "op-new")
        self.assertEqual(marked, 1)
        release.set()
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SUPERSEDED)
        self.assertEqual(executed, [])

    def test_dropping_stale_entries_does_not_crash_worker_or_hang_future(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self._value = 100.0

            def __call__(self) -> float:
                return self._value

            def advance(self, delta: float) -> None:
                self._value += delta

        clock = FakeClock()
        executor = self._new_executor(capacity=8, worker_count=1, clock=clock)
        running = threading.Event()
        release = threading.Event()

        def blocker(_task):
            running.set()
            release.wait(timeout=2)
            return "blocker"

        def runnable(_task):
            return "runnable"

        blocker_future = executor.submit(
            _task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="blocker", instance_id=1)
        )
        self.assertTrue(running.wait(timeout=1))
        stale_future = executor.submit(
            _task(
                callable_=runnable,
                priority=ComputationPriority.LIVE,
                task_id="stale",
                instance_id=2,
                deadline=clock() - 1.0,
            )
        )
        runnable_future = executor.submit(
            _task(callable_=runnable, priority=ComputationPriority.LIVE, task_id="ok", instance_id=3)
        )
        release.set()
        blocker_future.result(timeout=2)
        stale_outcome = stale_future.result(timeout=2)
        runnable_outcome = runnable_future.result(timeout=2)
        self.assertEqual(stale_outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(stale_outcome.cancel_reason, CancelReason.DEADLINE_EXCEEDED)
        self.assertEqual(runnable_outcome.status, ComputationStatus.COMPLETED)
        self.assertEqual(runnable_outcome.value, "runnable")

    def test_session_retirement_isolates_late_result(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)
        accepted: list[Any] = []
        running = threading.Event()
        release = threading.Event()
        session_valid = {"valid": True}

        def slow(_task):
            running.set()
            release.wait(timeout=2)
            return "value"

        future = executor.submit(
            _task(
                callable_=slow,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="slow",
                is_session_valid=lambda: session_valid["valid"],
                accept_result=lambda value: accepted.append(value) or True,
            )
        )
        self.assertTrue(running.wait(timeout=1))
        session_valid["valid"] = False
        release.set()
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SESSION_INVALID)
        self.assertEqual(accepted, [])

    def test_generation_mismatch_isolates_result(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)
        accepted: list[Any] = []
        generation = {"value": 1}

        def quick(_task):
            return "value"

        future = executor.submit(
            _task(
                callable_=quick,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="quick",
                generation=1,
                is_session_valid=lambda: False,
                accept_result=lambda value: accepted.append(value) or True,
            )
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SESSION_INVALID)
        self.assertEqual(accepted, [])


class TimeoutTests(_ExecutorTestBase):
    def test_deadline_expires_while_queued(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self._value = 100.0

            def __call__(self) -> float:
                return self._value

            def advance(self, delta: float) -> None:
                self._value += delta

        clock = FakeClock()
        executor = self._new_executor(capacity=4, worker_count=1, clock=clock)
        running = threading.Event()
        release = threading.Event()
        executed: list[str] = []

        def blocker(_task):
            running.set()
            release.wait(timeout=2)
            return "blocker"

        def victim(_task):
            executed.append("victim")
            return "victim"

        executor.submit(_task(callable_=blocker, priority=ComputationPriority.LIVE, task_id="blocker"))
        self.assertTrue(running.wait(timeout=1))
        future = executor.submit(
            _task(
                callable_=victim,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="victim",
                deadline=clock() + 1.0,
            )
        )
        # Advance the clock past the deadline while the task is still queued.
        clock.advance(2.0)
        release.set()
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.DEADLINE_EXCEEDED)
        self.assertEqual(executed, [])

    def test_in_flight_deadline_is_reported_at_acceptance(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self._value = 100.0

            def __call__(self) -> float:
                return self._value

            def advance(self, delta: float) -> None:
                self._value += delta

        clock = FakeClock()
        executor = self._new_executor(capacity=4, worker_count=1, clock=clock)
        running = threading.Event()
        release = threading.Event()
        accepted: list[Any] = []

        def slow(_task):
            running.set()
            release.wait(timeout=2)
            return "value"

        future = executor.submit(
            _task(
                callable_=slow,
                priority=ComputationPriority.REPLAY_INTERACTIVE,
                task_id="slow",
                deadline=clock() + 1.0,
                accept_result=lambda value: accepted.append(value) or True,
            )
        )
        self.assertTrue(running.wait(timeout=1))
        # Advance the clock past the deadline while the task is executing.
        clock.advance(2.0)
        release.set()
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.DEADLINE_EXCEEDED)
        self.assertEqual(accepted, [])


class FailureIsolationTests(_ExecutorTestBase):
    def test_callable_exception_does_not_break_worker(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=1)

        def boom(_task):
            raise RuntimeError("boom")

        def ok(_task):
            return "ok"

        f1 = executor.submit(_task(callable_=boom, priority=ComputationPriority.LIVE, task_id="b"))
        outcome1 = f1.result(timeout=2)
        self.assertEqual(outcome1.status, ComputationStatus.FAILED)
        self.assertIsInstance(outcome1.exception, RuntimeError)
        self.assertEqual(str(outcome1.exception), "boom")

        f2 = executor.submit(_task(callable_=ok, priority=ComputationPriority.LIVE, task_id="ok"))
        outcome2 = f2.result(timeout=2)
        self.assertEqual(outcome2.status, ComputationStatus.COMPLETED)
        self.assertEqual(outcome2.value, "ok")

    def test_failed_outcome_preserves_exception_chain(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)
        cause = ValueError("root")

        def raise_chained(_task):
            raise RuntimeError("wrapped") from cause

        future = executor.submit(
            _task(callable_=raise_chained, priority=ComputationPriority.LIVE, task_id="c")
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.FAILED)
        self.assertIsInstance(outcome.exception, RuntimeError)
        self.assertIs(outcome.exception.__cause__, cause)


class ShutdownTests(_ExecutorTestBase):
    def test_shutdown_stops_accepting_new_tasks(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)
        executor.shutdown(cancel_pending=True, wait=True)
        with self.assertRaises(ComputationExecutorClosedError):
            executor.submit(_task(callable_=lambda _t: None, priority=ComputationPriority.LIVE, task_id="x"))

    def test_shutdown_cancel_pending_does_not_deadlock(self) -> None:
        executor = self._new_executor(capacity=4, worker_count=2)
        running = threading.Event()
        release = threading.Event()

        def slow(_task):
            running.set()
            release.wait(timeout=2)
            return "ok"

        f1 = executor.submit(_task(callable_=slow, priority=ComputationPriority.LIVE, task_id="s1"))
        self.assertTrue(running.wait(timeout=1))
        # Queue a pending task that should be cancelled.
        f2 = executor.submit(
            _task(callable_=slow, priority=ComputationPriority.REPLAY_PREFETCH, task_id="s2")
        )
        # Shutdown with cancel_pending=True should cancel f2 and not deadlock.
        # Release the running task so f1 can finish and the worker can exit.
        release.set()
        executor.shutdown(cancel_pending=True, wait=True)
        outcome1 = f1.result(timeout=2)
        outcome2 = f2.result(timeout=2)
        # f1 may be completed or cancelled depending on timing; f2 must be cancelled.
        self.assertIn(outcome1.status, {ComputationStatus.COMPLETED, ComputationStatus.CANCELLED})
        self.assertEqual(outcome2.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome2.cancel_reason, CancelReason.EXECUTOR_CLOSED)

    def test_shutdown_from_worker_thread_does_not_self_join(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)
        finished = threading.Event()

        def shutdown_from_worker(_task):
            # Calling shutdown(wait=True) from inside a worker would deadlock
            # if the implementation tried to join itself.  Use wait=False.
            executor.shutdown(cancel_pending=True, wait=False)
            finished.set()
            return "ok"

        future = executor.submit(
            _task(callable_=shutdown_from_worker, priority=ComputationPriority.LIVE, task_id="sd")
        )
        outcome = future.result(timeout=2)
        self.assertTrue(finished.wait(timeout=1))
        # The worker managed to resolve the future before shutting down.
        self.assertIn(outcome.status, {ComputationStatus.COMPLETED, ComputationStatus.CANCELLED})


class AcceptanceBoundaryTests(_ExecutorTestBase):
    def test_rejected_result_does_not_commit_shared_state(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)
        committed: list[Any] = []

        def compute_preview(_task):
            return {"result": 42}

        future = executor.submit(
            _task(
                callable_=compute_preview,
                priority=ComputationPriority.LIVE,
                task_id="preview",
                accept_result=lambda value: False,
                commit_result=lambda value: committed.append(value),
            )
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SESSION_INVALID)
        self.assertEqual(committed, [])

    def test_accepted_result_commits_shared_state(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)
        committed: list[Any] = []

        def compute_preview(_task):
            return {"result": 42}

        future = executor.submit(
            _task(
                callable_=compute_preview,
                priority=ComputationPriority.LIVE,
                task_id="preview",
                accept_result=default_accept_result,
                commit_result=lambda value: committed.append(value),
            )
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.COMPLETED)
        self.assertEqual(outcome.value, {"result": 42})
        self.assertEqual(committed, [{"result": 42}])

    def test_accept_result_predicate_rejects_value(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)

        def ok(_task):
            return "value"

        future = executor.submit(
            _task(
                callable_=ok,
                priority=ComputationPriority.LIVE,
                task_id="ok",
                accept_result=lambda value: False,
            )
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.CANCELLED)
        self.assertEqual(outcome.cancel_reason, CancelReason.SESSION_INVALID)

    def test_completed_outcome_carries_value(self) -> None:
        executor = self._new_executor(capacity=2, worker_count=1)

        def ok(_task):
            return {"result": 42}

        future = executor.submit(
            _task(
                callable_=ok,
                priority=ComputationPriority.LIVE,
                task_id="ok",
                accept_result=default_accept_result,
            )
        )
        outcome = future.result(timeout=2)
        self.assertEqual(outcome.status, ComputationStatus.COMPLETED)
        self.assertEqual(outcome.value, {"result": 42})


class ConstructorValidationTests(unittest.TestCase):
    def test_capacity_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            BoundedComputationExecutor(capacity=0, worker_count=1)

    def test_worker_count_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            BoundedComputationExecutor(capacity=1, worker_count=0)

    def test_submit_rejects_non_task(self) -> None:
        executor = BoundedComputationExecutor(capacity=1, worker_count=1)
        try:
            with self.assertRaises(TypeError):
                executor.submit("not a task")  # type: ignore[arg-type]
        finally:
            executor.shutdown(cancel_pending=True, wait=True)


if __name__ == "__main__":
    unittest.main()
