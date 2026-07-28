"""Bounded computation executor for Live and Replay workbench tasks.

The executor coordinates competing Live and Replay computation requests on a
single shared worker pool.  It is intentionally smaller than a general-purpose
scheduler:

* The shared-resource priority is fixed by :class:`ComputationPriority`
  (Live > Replay interactive > Replay prefetch) and is enforced through a
  bounded priority queue.
* Tasks targeting the same :class:`PipelineInstanceIdentity` are always
  serialised: a task cannot start until every earlier task sharing the same
  identity has reached a terminal state.  Different pipeline instances may run
  in parallel up to the configured worker count.
* Cancellation is cooperative: queued tasks can be skipped, running tasks are
  expected to honour their ``is_cancelled`` predicate, and every result is
  re-validated at the acceptance boundary so a late or superseded task can
  never mutate a pipeline instance that has moved on.
* Timeouts use absolute monotonic deadlines.  The deadline covers both queue
  time and execution time, so an expired deadline prevents a task from
  starting and an in-flight task whose deadline elapses is reported as
  ``DEADLINE_EXCEEDED`` at acceptance time.

The executor never publishes events.  It returns a :class:`ComputationOutcome`
for every submitted task and leaves the translation to stable Replay errors to
the caller.  This keeps Electron, HTTP, React and the Replay JSON Schema out of
the runtime package.
"""

from __future__ import annotations

import atexit
import heapq
import itertools
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from .computation_contract import (
    CancelReason,
    ComputationFuture,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
    default_accept_result,
    new_task_id,
)


class ComputationExecutorError(RuntimeError):
    """Base class for executor-level failures."""


class ComputationQueueFullError(ComputationExecutorError):
    """Raised when another task cannot be accepted without exceeding capacity."""


class ComputationExecutorClosedError(ComputationExecutorError):
    """Raised after :meth:`BoundedComputationExecutor.shutdown`."""


@dataclass(order=True, slots=True)
class _PendingEntry:
    """Heap entry for a queued task.

    The heap is ordered by ``(priority, sequence)`` so that a higher-priority
    task always pops first regardless of submission order.  ``sequence`` is a
    monotonically increasing counter that preserves FIFO order within the same
    priority.  ``task`` and ``future`` are excluded from the comparison so two
    entries never need to compare :class:`ComputationTask` instances.
    """

    priority: int
    sequence: int
    task: ComputationTask = field(compare=False)
    future: Future = field(compare=False)


@dataclass
class _RunningTask:
    """Bookkeeping for a task currently executing on a worker."""

    task: ComputationTask
    future: Future
    worker_index: int


def _now() -> float:
    """Monotonic clock read so tests can patch ``time.monotonic``."""

    return time.monotonic()


class _ComputationFutureImpl:
    """Concrete future returned by the executor.

    ``result()`` blocks until the executor resolves the outcome.  A
    ``timeout`` only bounds the wait; it does not cancel the task.  This keeps
    the cancellation contract explicit: cancellation happens through the task
    predicate or through ``shutdown(cancel_pending=True)``.
    """

    __slots__ = ("_future",)

    def __init__(self, future: Future) -> None:
        self._future = future

    def result(self, *, timeout: float | None = None) -> ComputationOutcome:
        return self._future.result(timeout=timeout)

    def done(self) -> bool:
        return self._future.done()


class BoundedComputationExecutor:
    """Bounded priority executor with per-pipeline serialisation.

    Args:
        capacity: maximum number of queued tasks.  Must be positive.  When the
            queue is full, :meth:`submit` raises :class:`ComputationQueueFullError`
            instead of growing unbounded.
        worker_count: number of worker threads.  Must be positive.  Workers
            pull from the priority queue, respecting per-pipeline serialisation
            and absolute deadlines.
        clock: injectable monotonic clock for deterministic tests.
    """

    def __init__(
        self,
        *,
        capacity: int = 32,
        worker_count: int = 2,
        clock=None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if worker_count <= 0:
            raise ValueError("worker_count must be positive")
        self._capacity = capacity
        self._worker_count = worker_count
        self._clock = clock or _now
        self._condition = threading.Condition()
        self._heap: list[_PendingEntry] = []
        self._sequence = itertools.count()
        self._closed = False
        self._running: dict[PipelineInstanceIdentity, _RunningTask] = {}
        self._running_count = 0
        self._superseded_operations: dict[str, str] = {}
        self._workers: list[threading.Thread] = []
        self._shutdown_complete = threading.Event()
        for index in range(worker_count):
            thread = threading.Thread(
                target=self._run,
                name=f"stockpilot-compute-{index}",
                daemon=True,
            )
            thread.start()
            self._workers.append(thread)
        atexit.register(self._atexit_shutdown)

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def worker_count(self) -> int:
        return self._worker_count

    def submit(self, task: ComputationTask) -> ComputationFuture:
        """Submit a task, returning a future resolved with a ComputationOutcome.

        Raises:
            ComputationQueueFullError: when the pending queue is at capacity.
            ComputationExecutorClosedError: after shutdown.
            TypeError: when ``task`` is not a :class:`ComputationTask`.
        """

        if not isinstance(task, ComputationTask):
            raise TypeError("task must be a ComputationTask")
        if not callable(task.callable):
            raise TypeError("task.callable must be callable")
        if task.is_cancelled is not None and not callable(task.is_cancelled):
            raise TypeError("task.is_cancelled must be callable")
        if task.is_session_valid is not None and not callable(task.is_session_valid):
            raise TypeError("task.is_session_valid must be callable")
        if task.accept_result is not None and not callable(task.accept_result):
            raise TypeError("task.accept_result must be callable")
        if task.commit_result is not None and not callable(task.commit_result):
            raise TypeError("task.commit_result must be callable")

        future: Future = Future()
        # Assign an opaque id if the caller did not supply one, without
        # mutating the frozen task.
        effective_task = task if task.task_id else task.with_task_id(new_task_id())

        with self._condition:
            if self._closed:
                raise ComputationExecutorClosedError("computation executor is closed")
            if len(self._heap) >= self._capacity:
                raise ComputationQueueFullError(
                    f"computation queue capacity {self._capacity} is exhausted"
                )
            entry = _PendingEntry(
                priority=int(effective_task.priority),
                sequence=next(self._sequence),
                task=effective_task,
                future=future,
            )
            heapq.heappush(self._heap, entry)
            self._condition.notify()

        return _ComputationFutureImpl(future)

    def supersede_operation(
        self,
        session_id: str,
        new_operation_id: str,
    ) -> int:
        """Mark every outstanding earlier operation on a Session as superseded.

        A newer ``seek_replay`` on the same Session invalidates every queued or
        running task whose ``operation_id`` differs from ``new_operation_id``.
        Superseded tasks are resolved with
        ``CancelReason.SUPERSEDED`` and may be delivered at most once.

        Returns the number of tasks marked superseded.
        """

        if not session_id or not new_operation_id:
            raise ValueError("session_id and new_operation_id must be non-empty")
        with self._condition:
            self._superseded_operations[session_id] = new_operation_id
            count = 0
            for entry in self._heap:
                if (
                    entry.task.session_id == session_id
                    and entry.task.operation_id is not None
                    and entry.task.operation_id != new_operation_id
                ):
                    count += 1
            return count

    def shutdown(
        self,
        *,
        cancel_pending: bool = False,
        wait: bool = True,
    ) -> None:
        """Stop accepting work.

        Args:
            cancel_pending: when ``True``, queued tasks that have not started
                are cancelled with ``CancelReason.EXECUTOR_CLOSED``.  Running
                tasks are left to finish because Python threads cannot be
                safely force-killed; their results are still subject to the
                acceptance predicate.
            wait: when ``True``, block until every worker has joined.  Must be
                ``False`` when called from a worker thread to avoid a
                self-join deadlock.
        """

        with self._condition:
            if self._closed:
                return
            self._closed = True
            if cancel_pending:
                while self._heap:
                    entry = heapq.heappop(self._heap)
                    self._resolve_cancelled(
                        entry.future,
                        entry.task,
                        CancelReason.EXECUTOR_CLOSED,
                    )
            self._condition.notify_all()
        if wait and threading.current_thread() not in self._workers:
            for worker in self._workers:
                if worker.is_alive() and worker is not threading.current_thread():
                    worker.join()
        self._shutdown_complete.set()

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._condition:
                task, future = self._wait_for_runnable_locked()
                if task is None:
                    return
                # Hold the lock only for the bookkeeping update; the actual
                # callable runs outside the lock.
                running = _RunningTask(
                    task=task,
                    future=future,
                    worker_index=0,
                )
                self._running[task.pipeline_identity] = running
                self._running_count += 1

            try:
                outcome = self._execute_task(task)
            except BaseException as exc:  # pragma: no cover - defensive
                outcome = ComputationOutcome(
                    task_id=task.task_id,
                    status=ComputationStatus.FAILED,
                    exception=exc,
                )
            with self._condition:
                self._running.pop(task.pipeline_identity, None)
                self._running_count -= 1
                self._condition.notify_all()

            if not future.done():
                future.set_result(outcome)

    def _wait_for_runnable_locked(self) -> tuple[ComputationTask | None, Future | None]:
        """Pop the next runnable task, skipping stale ones and respecting serialisation.

        Returns ``(None, None)`` when the executor has shut down and no more
        tasks remain.  When the highest-priority entry belongs to a pipeline
        that is currently running, the worker continues scanning the rest of
        the heap for the next-highest-priority runnable entry instead of
        blocking the whole queue.  Busy entries are left in the heap (in their
        original priority order) for a later pass.

        Stale tasks (cancelled, deadline-expired or superseded while queued)
        are resolved immediately without executing them.
        """

        while True:
            while not self._heap and not self._closed:
                self._condition.wait()
            if not self._heap:
                if self._closed:
                    return None, None
                continue

            # First drop stale entries, then rescan from the current heap.
            # This avoids keeping an array index that becomes invalid after the
            # rebuild and also avoids relying on the heap's internal array
            # order, which is not globally sorted beyond the root element.
            entries_to_drop: list[_PendingEntry] = []
            for entry in self._heap:
                if entry.future.cancelled():
                    entries_to_drop.append(entry)
                    continue
                skip_reason = self._validate_before_run(entry.task)
                if skip_reason is not None:
                    # Resolve now and remove from the heap.
                    self._resolve_cancelled(entry.future, entry.task, skip_reason)
                    entries_to_drop.append(entry)
                    continue
            if entries_to_drop:
                self._rebuild_heap(entries_to_drop)
                continue

            candidate = min(
                (
                    entry
                    for entry in self._heap
                    if entry.task.pipeline_identity not in self._running
                ),
                default=None,
            )
            if candidate is not None:
                return self._pop_entry_locked(candidate)

            # Every remaining entry is busy (or the heap is empty after drops).
            if not self._heap:
                if self._closed:
                    return None, None
                continue
            # Wait for a running task to finish or a new submission.  A spurious
            # wake is fine: we re-scan the heap from the top.
            self._condition.wait()

    def _pop_entry_locked(self, candidate: _PendingEntry) -> tuple[ComputationTask, Future]:
        """Remove and return ``candidate``, preserving heap order."""

        for index, entry in enumerate(self._heap):
            if entry is candidate:
                removed = self._heap.pop(index)
                heapq.heapify(self._heap)
                return removed.task, removed.future
        raise RuntimeError("candidate entry disappeared from heap")

    def _rebuild_heap(self, drop_entries: list[_PendingEntry]) -> None:
        """Drop the given entries and restore the heap invariant."""

        if not drop_entries:
            return
        drop_ids = {id(entry) for entry in drop_entries}
        self._heap = [entry for entry in self._heap if id(entry) not in drop_ids]
        heapq.heapify(self._heap)

    def _validate_before_run(self, task: ComputationTask) -> CancelReason | None:
        """Return a cancel reason when the task must not start."""

        if task.is_cancelled is not None:
            try:
                if task.is_cancelled():
                    return CancelReason.CANCELLED
            except Exception:
                # A broken predicate must isolate the task, not kill the worker.
                return CancelReason.CANCELLED
        if task.deadline is not None and self._clock() >= task.deadline:
            return CancelReason.DEADLINE_EXCEEDED
        reason = self._supersession_reason(task)
        if reason is not None:
            return reason
        return None

    def _supersession_reason(self, task: ComputationTask) -> CancelReason | None:
        if task.operation_id is None:
            return None
        latest = self._superseded_operations.get(task.session_id)
        if latest is not None and latest != task.operation_id:
            return CancelReason.SUPERSEDED
        return None

    def _execute_task(self, task: ComputationTask) -> ComputationOutcome:
        """Run the callable and apply the acceptance boundary."""

        try:
            value = task.callable(task)
        except Exception as exc:
            return ComputationOutcome(
                task_id=task.task_id,
                status=ComputationStatus.FAILED,
                exception=exc,
            )

        # Re-validate everything at the acceptance boundary.  A late or
        # superseded task must never reach the publisher or mutate shared
        # state even if the callable happened to return a value.
        with self._condition:
            accept_reason = self._acceptance_reason(task)
            if accept_reason is not None:
                return ComputationOutcome(
                    task_id=task.task_id,
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=accept_reason,
                )

            accept_predicate = task.accept_result or default_accept_result
            try:
                accepted = accept_predicate(value)
            except Exception:
                accepted = False
            if not accepted:
                return ComputationOutcome(
                    task_id=task.task_id,
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.SESSION_INVALID,
                )

            if task.commit_result is not None:
                try:
                    task.commit_result(value)
                except Exception as exc:
                    return ComputationOutcome(
                        task_id=task.task_id,
                        status=ComputationStatus.FAILED,
                        exception=exc,
                    )
        return ComputationOutcome(
            task_id=task.task_id,
            status=ComputationStatus.COMPLETED,
            value=value,
        )

    def _acceptance_reason(self, task: ComputationTask) -> CancelReason | None:
        """Final acceptance check performed under the state lock."""

        if self._closed:
            return CancelReason.EXECUTOR_CLOSED
        if task.is_cancelled is not None:
            try:
                if task.is_cancelled():
                    return CancelReason.CANCELLED
            except Exception:
                return CancelReason.CANCELLED
        if task.is_session_valid is not None:
            try:
                if not task.is_session_valid():
                    return CancelReason.SESSION_INVALID
            except Exception:
                return CancelReason.SESSION_INVALID
        if task.deadline is not None and self._clock() >= task.deadline:
            return CancelReason.DEADLINE_EXCEEDED
        return self._supersession_reason(task)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_cancelled(
        future: Future,
        task: ComputationTask,
        reason: CancelReason,
    ) -> None:
        if future.done():
            return
        future.set_result(
            ComputationOutcome(
                task_id=task.task_id,
                status=ComputationStatus.CANCELLED,
                cancel_reason=reason,
            )
        )

    def _atexit_shutdown(self) -> None:
        try:
            self.shutdown(cancel_pending=True, wait=False)
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


__all__ = [
    "BoundedComputationExecutor",
    "ComputationExecutorClosedError",
    "ComputationExecutorError",
    "ComputationQueueFullError",
]
