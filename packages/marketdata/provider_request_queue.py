"""Bounded, shared coordination outside the market-data provider boundary.

The provider remains responsible for one remote operation and for mapping its
own failures to ``MarketDataResult``.  This queue only coordinates competing
Live/Replay callers: priority, duplicate request coalescing, bounded capacity,
coordinated retries, and late-result isolation for retired sessions.
"""

from __future__ import annotations

import atexit
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import IntEnum
import heapq
import itertools
import threading
from typing import Any, Callable, Hashable

from .provider_result import MarketDataResult


class ProviderRequestPriority(IntEnum):
    """Resource priority fixed by the T+0 architecture."""

    LIVE = 0
    REPLAY_INTERACTIVE = 1
    REPLAY_PREFETCH = 2


class ProviderQueueError(RuntimeError):
    """Base class for queue coordination failures."""


class ProviderQueueFullError(ProviderQueueError):
    """Raised when another unique request cannot be accepted."""


class ProviderQueueClosedError(ProviderQueueError):
    """Raised after the queue stops accepting requests."""


@dataclass(frozen=True)
class ProviderQueueOutcome:
    """One subscriber's view of a coordinated provider operation.

    ``result`` is the provider's unchanged return value.  A caller may persist
    it even when ``session_valid`` is false, but must not publish it into that
    retired Session.  ``executed`` is false when every subscriber became
    invalid before the provider operation started.
    """

    result: Any
    session_valid: bool
    coalesced: bool
    executed: bool = True


@dataclass
class _Subscriber:
    future: Future
    session_validator: Callable[[], bool] | None
    coalesced: bool

    def is_valid(self) -> bool:
        if self.session_validator is None:
            return True
        try:
            return bool(self.session_validator())
        except Exception:
            # Session lifecycle callbacks are coordination hints.  A broken
            # callback must isolate that subscriber, not kill the shared worker.
            return False


@dataclass
class _QueuedRequest:
    key: Hashable
    operation: Callable[[], Any]
    priority: ProviderRequestPriority
    max_attempts: int
    retry_predicate: Callable[[Any], bool]
    subscribers: list[_Subscriber] = field(default_factory=list)
    queue_version: int = 0
    started: bool = False


def _default_retry_predicate(result: Any) -> bool:
    return isinstance(result, MarketDataResult) and not result.success


class ProviderRequestQueue:
    """A small synchronous-friendly priority queue with one provider worker."""

    def __init__(self, capacity: int = 64):
        if capacity <= 0:
            raise ValueError("provider queue capacity must be positive")
        self.capacity = capacity
        self._condition = threading.Condition()
        self._requests: dict[Hashable, _QueuedRequest] = {}
        self._heap: list[tuple[int, int, int, Hashable]] = []
        self._sequence = itertools.count()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name="stockpilot-provider-queue",
            daemon=True,
        )
        self._worker.start()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def submit(
        self,
        key: Hashable,
        operation: Callable[[], Any],
        *,
        priority: ProviderRequestPriority = ProviderRequestPriority.REPLAY_INTERACTIVE,
        session_validator: Callable[[], bool] | None = None,
        max_attempts: int = 1,
        retry_predicate: Callable[[Any], bool] | None = None,
    ) -> Future:
        """Submit or join one unique provider request.

        Capacity is counted by unique queued/in-flight operations, so duplicate
        callers can still join an accepted request when the queue is full.
        The first submission owns ``operation``, ``max_attempts`` and
        ``retry_predicate`` for the shared execution.  Later subscribers share
        its result and may promote a queued request's priority, but they do not
        replace its provider or retry policy.
        """

        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if not callable(operation):
            raise TypeError("operation must be callable")
        if session_validator is not None and not callable(session_validator):
            raise TypeError("session_validator must be callable")

        future: Future = Future()
        with self._condition:
            if self._closed:
                raise ProviderQueueClosedError("provider queue is closed")

            existing = self._requests.get(key)
            if existing is not None:
                existing.subscribers.append(
                    _Subscriber(future, session_validator, coalesced=True)
                )
                joined_priority = ProviderRequestPriority(priority)
                if not existing.started and joined_priority < existing.priority:
                    existing.priority = joined_priority
                    existing.queue_version += 1
                    heapq.heappush(
                        self._heap,
                        (
                            int(existing.priority),
                            next(self._sequence),
                            existing.queue_version,
                            key,
                        ),
                    )
                    self._condition.notify()
                return future

            if len(self._requests) >= self.capacity:
                raise ProviderQueueFullError(
                    f"provider queue capacity {self.capacity} is exhausted"
                )

            request = _QueuedRequest(
                key=key,
                operation=operation,
                priority=ProviderRequestPriority(priority),
                max_attempts=max_attempts,
                retry_predicate=retry_predicate or _default_retry_predicate,
                subscribers=[
                    _Subscriber(future, session_validator, coalesced=False)
                ],
            )
            self._requests[key] = request
            heapq.heappush(
                self._heap,
                (
                    int(request.priority),
                    next(self._sequence),
                    request.queue_version,
                    key,
                ),
            )
            self._condition.notify()
        return future

    def execute(self, key: Hashable, operation: Callable[[], Any], **kwargs) -> ProviderQueueOutcome:
        """Submit and block until this subscriber receives its outcome."""

        timeout = kwargs.pop("timeout", None)
        return self.submit(key, operation, **kwargs).result(timeout=timeout)

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        """Stop accepting work and optionally cancel operations not yet started."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            if cancel_pending:
                while self._heap:
                    _priority, _sequence, _version, key = heapq.heappop(self._heap)
                    request = self._requests.pop(key, None)
                    if request is None:
                        continue
                    for subscriber in request.subscribers:
                        subscriber.future.cancel()
            self._condition.notify_all()
        if wait and threading.current_thread() is not self._worker:
            self._worker.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._heap and not self._closed:
                    self._condition.wait()
                if not self._heap:
                    return
                _priority, _sequence, version, key = heapq.heappop(self._heap)
                request = self._requests.get(key)
                if (
                    request is not None
                    and not request.started
                    and version == request.queue_version
                ):
                    request.started = True
                else:
                    request = None

            if request is None:
                continue
            active = [
                subscriber
                for subscriber in request.subscribers
                if not subscriber.future.cancelled() and subscriber.is_valid()
            ]
            if not active:
                self._finish(request, result=None, executed=False)
                continue

            try:
                result = self._execute_with_retry(request)
            except BaseException as exc:
                self._finish_with_exception(request, exc)
            else:
                self._finish(request, result=result, executed=True)

    @staticmethod
    def _execute_with_retry(request: _QueuedRequest) -> Any:
        result: Any = None
        for attempt in range(request.max_attempts):
            try:
                result = request.operation()
            except Exception:
                if attempt + 1 >= request.max_attempts:
                    raise
                continue
            if attempt + 1 >= request.max_attempts:
                break
            if not request.retry_predicate(result):
                break
        return result

    def _finish(self, request: _QueuedRequest, *, result: Any, executed: bool) -> None:
        with self._condition:
            self._requests.pop(request.key, None)
            subscribers = tuple(request.subscribers)
            self._condition.notify_all()
        for subscriber in subscribers:
            if subscriber.future.cancelled():
                continue
            valid = subscriber.is_valid()
            subscriber.future.set_result(
                ProviderQueueOutcome(
                    result=result,
                    session_valid=valid,
                    coalesced=subscriber.coalesced,
                    executed=executed,
                )
            )

    def _finish_with_exception(
        self,
        request: _QueuedRequest,
        exc: BaseException,
    ) -> None:
        with self._condition:
            self._requests.pop(request.key, None)
            subscribers = tuple(request.subscribers)
            self._condition.notify_all()
        for subscriber in subscribers:
            if not subscriber.future.cancelled():
                subscriber.future.set_exception(exc)


_SHARED_QUEUE: ProviderRequestQueue | None = None
_SHARED_QUEUE_LOCK = threading.Lock()


def get_shared_provider_request_queue() -> ProviderRequestQueue:
    """Return the process-wide provider coordination queue."""

    global _SHARED_QUEUE
    with _SHARED_QUEUE_LOCK:
        if _SHARED_QUEUE is None or _SHARED_QUEUE.closed:
            _SHARED_QUEUE = ProviderRequestQueue()
        return _SHARED_QUEUE


def _shutdown_shared_queue() -> None:
    with _SHARED_QUEUE_LOCK:
        queue = _SHARED_QUEUE
    if queue is not None:
        queue.shutdown(wait=False, cancel_pending=True)


atexit.register(_shutdown_shared_queue)
