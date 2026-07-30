"""Transport-independent Replay playback engine (T0-046).

:class:`ReplaySession` consumes the :class:`PreparedReplayData` produced by
T0-045 and drives a Replay-dedicated :class:`WorkbenchPipeline` through the
T0-020 :class:`ComputationExecutorPort`.  It owns the frozen Replay v1.0 state
machine (``loading -> ready -> playing <-> paused -> failed/retired``), the
simulated cursor, playback cadence, revision authority and the atomic
workbench-snapshot publishing boundary.

Design rules enforced here:

* The cursor advances strictly along ``prepared.actual_bar_times``.  Lunch
  breaks, suspensions and other gaps are crossed without fabricating bars, and
  ``end_time`` always comes from the market calendar, never from the data tail.
* Every pipeline computation is submitted as a :class:`ComputationTask` with
  :attr:`ComputationPriority.REPLAY_INTERACTIVE`, an isolated
  :meth:`WorkbenchPipeline.preview` callable, ``commit_preview`` as the commit
  callback, and ``is_cancelled`` / ``is_session_valid`` / ``accept_result``
  predicates that isolate retired, paused, superseded or otherwise stale
  results.  The session never calls ``pipeline.compute`` directly.
* Auto-playback keeps at most one in-flight advance per Session.  When a
  computation is slow the pump simply waits (backpressure); it never queues a
  second task to "catch up" with the selected speed.
* ``session_status`` and ``workbench_snapshot`` share one strictly monotonic
  revision sequence.  A snapshot's ``session.revision`` always equals the event
  envelope ``revision``, and ``replay.playing`` is ``True`` only while the
  Session state is ``playing``.
* Reaching the sequence end converges to ``paused`` exactly once; later timer
  ticks and steps are idempotent no-ops that do not allocate a revision or
  publish an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Event, RLock
from typing import Any, Callable

from ._market_bars import MARKET_TIMESTAMP_FORMAT
from .computation_contract import (
    CancelReason,
    ComputationExecutorPort,
    ComputationOutcome,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
    PreparedReplayData,
    new_task_id,
)
from .computation_executor import (
    ComputationExecutorClosedError,
    ComputationQueueFullError,
)
from .pipeline import CzscAnalyzerPort, PipelineResult, WorkbenchPipeline
from .replay_clock import (
    MonotonicClockPort,
    NullPlaybackScheduler,
    PlaybackSchedulerPort,
    SystemMonotonicClock,
    TimerPlaybackScheduler,
)
from .workbench_projection import (
    ReplayProjectionInput,
    SessionProjectionInput,
    build_workbench_projection,
)


REPLAY_SCHEMA_VERSION = "t0_replay_v1"

#: Frozen Replay v1.0 playback speeds.
_ALLOWED_PLAYBACK_SPEEDS = frozenset({1, 2, 5, 10})
_DEFAULT_PLAYBACK_SPEED = 1

_STEP_SECONDS_BY_GRANULARITY = {"one_minute": 60, "five_minute": 300}


class ReplaySessionError(RuntimeError):
    """Base class for Replay Session failures."""


class ReplaySessionStateError(ReplaySessionError):
    """The current Session state does not allow the requested operation."""


@dataclass(frozen=True, slots=True)
class ReplayStepResult:
    """Outcome of one :meth:`ReplaySession.step` call.

    ``advanced`` is ``False`` for the idempotent end-of-sequence no-op and for
    any dropped or failed result; in those cases ``outcome_status`` is
    ``"no_op"``, ``"dropped"`` or ``"failed"`` and the cursor is unchanged.
    ``outcome`` carries the raw :class:`ComputationOutcome` when one was
    produced, so the command adapter can map it to a stable Replay error
    without recomputing or losing the original ``cancel_reason``.
    """

    advanced: bool
    revision: int
    operation_id: str | None
    outcome_status: str
    outcome: ComputationOutcome | None = None


@dataclass(frozen=True, slots=True)
class ReplayInitialResult:
    """One-shot result of the initial Replay ready computation.

    ``revision`` is owned by the Session.  For a failed initialization it is
    reserved for the corresponding T0-044 ``operation_failed`` event; for a
    successful initialization it is the emitted ready snapshot revision.
    """

    outcome: ComputationOutcome
    revision: int


@dataclass(frozen=True, slots=True)
class PlaybackPumpResult:
    """Outcome of one :meth:`ReplaySession.pump_playback` call.

    ``next_due_mono`` is the monotonic time at which the pump should run again,
    or ``None`` when playback has stopped (paused, retired, converged, or not
    currently playing).
    """

    action: str
    next_due_mono: float | None


EventPublisher = Callable[[dict[str, Any]], None]


class ReplaySession:
    """Replay playback engine owning the cursor, cadence and revision authority.

    The Session is constructed with already-prepared data; it performs the
    initial ``ready`` computation through the executor and then exposes
    :meth:`play`, :meth:`pause`, :meth:`step`, :meth:`set_playback_speed`,
    :meth:`pump_playback`, :meth:`retire` and :meth:`snapshot`.

    Args:
        session_id: opaque Replay Session identifier.
        service_generation: Python service generation, echoed in every event.
        prepared: the immutable :class:`PreparedReplayData` from T0-045.
        executor: the T0-020 bounded computation executor.
        clock: injectable monotonic clock for playback pacing and deadlines.
        scheduler: injectable playback scheduler.  Defaults to a
            :class:`TimerPlaybackScheduler` for production; tests inject a
            :class:`NullPlaybackScheduler` and drive :meth:`pump_playback`.
        on_event: callback invoked synchronously under the Session lock for
            every published event envelope.  It must be non-blocking and must
            not re-enter the Session (no play/pause/step/snapshot inside).
        analyzer: injectable CZSC analyzer for the dedicated pipeline.
        initial_operation_id: optional ``begin_replay`` operation id carried by
            the initial ``ready`` workbench snapshot.
        auto_ready: when ``True`` (default) compute and publish the initial
            ``ready`` snapshot at ``start_time`` during construction.
        computation_timeout: maximum seconds to wait for one computation future.
        deadline_seconds: monotonic deadline for each computation task.  ``None``
            disables the deadline (used by deterministic speed tests).
    """

    def __init__(
        self,
        session_id: str,
        service_generation: int,
        prepared: PreparedReplayData,
        executor: ComputationExecutorPort,
        *,
        clock: MonotonicClockPort | None = None,
        scheduler: PlaybackSchedulerPort | None = None,
        on_event: EventPublisher | None = None,
        analyzer: CzscAnalyzerPort | None = None,
        initial_operation_id: str | None = None,
        auto_ready: bool = True,
        computation_timeout: float | None = None,
        deadline_seconds: float | None = None,
    ) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if isinstance(service_generation, bool) or not isinstance(
            service_generation, int
        ):
            raise TypeError("service_generation must be an integer")
        if service_generation < 1:
            raise ValueError("service_generation must be a positive integer")
        if not isinstance(prepared, PreparedReplayData):
            raise TypeError("prepared must be a PreparedReplayData")
        if not hasattr(executor, "submit") or not callable(executor.submit):
            raise TypeError("executor must implement ComputationExecutorPort")

        self._session_id = session_id
        self._service_generation = service_generation
        self._prepared = prepared
        self._executor = executor
        self._clock = clock or SystemMonotonicClock()
        self._on_event: EventPublisher = on_event or (lambda _event: None)
        self._initial_operation_id = initial_operation_id
        self._computation_timeout = computation_timeout
        self._deadline_seconds = deadline_seconds

        self._market_session = prepared.market_session
        self._actual_bar_times: tuple[datetime, ...] = tuple(
            prepared.actual_bar_times
        )
        self._granularity = prepared.granularity
        self._step_seconds = _STEP_SECONDS_BY_GRANULARITY[self._granularity]
        self._start_time = prepared.start_time
        self._end_time = prepared.end_time

        self._pipeline = WorkbenchPipeline(
            session=self._market_session,
            market_input_port=prepared.market_input_port,
            analyzer=analyzer,
        )
        self._pipeline_identity = PipelineInstanceIdentity(
            instance_id=id(self._pipeline),
            generation=service_generation,
            session_id=session_id,
        )

        self._lock = RLock()
        self._state = "loading"
        self._revision = -1
        self._playback_speed = _DEFAULT_PLAYBACK_SPEED
        self._current_time = self._start_time
        self._next_bar_time: datetime | None = self._resolve_next_bar(
            self._start_time, consumed=False
        )
        self._last_pipeline_result: PipelineResult | None = None
        self._retired = False
        self._ended_converged = False

        self._next_ticket = 1
        self._inflight_ticket: int | None = None
        self._inflight_committed_ticket: int | None = None
        self._inflight_committed_value: PipelineResult | None = None
        self._inflight_committed_revision: int | None = None
        self._initial_result: ReplayInitialResult | None = None
        self._last_commit_mono: float = 0.0

        self._owns_scheduler = scheduler is None
        self._scheduler: PlaybackSchedulerPort = (
            scheduler
            if scheduler is not None
            else TimerPlaybackScheduler(self.pump_playback, clock=self._clock)
        )

        if auto_ready:
            self._initialize_ready()

    # ------------------------------------------------------------------
    # Read-only projection
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def service_generation(self) -> int:
        return self._service_generation

    @property
    def symbol(self) -> str:
        return self._prepared.symbol

    @property
    def trade_date(self) -> str:
        return self._prepared.trade_date

    @property
    def granularity(self) -> str:
        return self._granularity

    @property
    def step_seconds(self) -> int:
        return self._step_seconds

    @property
    def start_time(self) -> datetime:
        return self._start_time

    @property
    def end_time(self) -> datetime:
        return self._end_time

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def revision(self) -> int:
        with self._lock:
            return max(self._revision, 0)

    @property
    def current_time(self) -> datetime:
        with self._lock:
            return self._current_time

    @property
    def next_bar_time(self) -> datetime | None:
        with self._lock:
            return self._next_bar_time

    @property
    def playback_speed(self) -> int:
        with self._lock:
            return self._playback_speed

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._state == "playing"

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired

    def snapshot(self) -> dict[str, Any]:
        """Return the current complete workbench snapshot payload.

        Uses the last committed pipeline result (already produced through the
        executor); no new computation is performed.
        """

        with self._lock:
            if self._last_pipeline_result is None:
                raise ReplaySessionStateError(
                    "no snapshot available before the ready computation"
                )
            return self._build_snapshot_payload_locked()

    def take_initial_result(self) -> ReplayInitialResult | None:
        """Return and consume the initial ``ready`` computation result.

        The result is available after construction when ``auto_ready`` is
        ``True``. It can be retrieved only once; subsequent calls return
        ``None``. The command adapter maps ``result.outcome`` through T0-044 and
        uses ``result.revision`` for the resulting ``operation_failed`` event.
        """

        with self._lock:
            result = self._initial_result
            self._initial_result = None
            return result

    # ------------------------------------------------------------------
    # Cursor operations
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Begin auto-playback from a stationary state (``ready``/``paused``).

        If a cursor operation (step/auto advance) is already in flight, ``play``
        returns ``replay_busy`` semantics by raising :class:`ReplaySessionStateError`.
        """

        with self._lock:
            if self._retired:
                raise ReplaySessionStateError("session is retired")
            if self._state == "playing":
                return  # idempotent
            if self._state not in {"ready", "paused"}:
                raise ReplaySessionStateError(
                    f"play requires ready or paused state, not {self._state}"
                )
            if self._inflight_ticket is not None:
                raise ReplaySessionStateError("replay_busy")
            if self._next_bar_time is None:
                # Already at the sequence end: nothing to play.  Converge once
                # to paused without consuming a bar or growing the revision.
                self._converge_to_paused_locked()
                return
            self._state = "playing"
            self._ended_converged = False
            self._last_commit_mono = self._clock.now()
            self._publish_session_status_locked(
                "playing", "user_command", None, None
            )
            first_due = self._last_commit_mono + (1.0 / self._playback_speed)
        self._scheduler.start()
        self._scheduler.schedule(first_due)

    def pause(self) -> None:
        """Pause auto-playback.  Idempotent from ``ready``/``paused``."""

        with self._lock:
            if self._retired:
                raise ReplaySessionStateError("session is retired")
            if self._state in {"ready", "paused"}:
                return  # idempotent no-op
            if self._state != "playing":
                raise ReplaySessionStateError(
                    f"pause requires playing state, not {self._state}"
                )
            self._state = "paused"
            self._publish_session_status_locked(
                "paused", "user_command", None, None
            )
        self._scheduler.cancel()

    def set_playback_speed(self, speed: int) -> None:
        """Set the authoritative playback speed.

        A legal new speed becomes authoritative immediately, increments the
        revision once and publishes a ``playback_speed_changed``
        ``session_status`` without an ``operation_id``.  Repeating the current
        speed is an idempotent no-op.  An illegal speed is rejected without
        mutating any state.
        """

        if type(speed) is not int or speed not in _ALLOWED_PLAYBACK_SPEEDS:
            raise ReplaySessionStateError(
                "playback_speed must be one of 1, 2, 5, 10"
            )
        with self._lock:
            if self._retired:
                raise ReplaySessionStateError("session is retired")
            if self._state not in {"ready", "playing", "paused"}:
                raise ReplaySessionStateError(
                    f"set_playback_speed not allowed in state {self._state}"
                )
            if speed == self._playback_speed:
                return  # idempotent no-op
            self._playback_speed = speed
            self._publish_session_status_locked(
                self._state, "playback_speed_changed", None, speed
            )
            was_playing = self._state == "playing"
            just_scheduled = False
            if was_playing and self._inflight_ticket is None:
                # Re-calculate the next wake according to the new speed so the
                # change takes effect immediately instead of waiting for the old
                # schedule.  If a computation is in flight the pump will re-schedule
                # after it finishes; we do not double-schedule here.
                now = self._clock.now()
                next_due = now + (1.0 / self._playback_speed)
                self._scheduler.schedule(next_due)
                just_scheduled = True
        if just_scheduled:
            self._scheduler.start()

    def step(self, operation_id: str) -> ReplayStepResult:
        """Advance the cursor by one actual bar synchronously.

        Requires a stationary state (``ready``/``paused``).  When
        ``next_bar_time`` is ``None`` this is an idempotent no-op: no
        ``operation_id`` is created, no computation is submitted, no revision is
        allocated and no event is published.
        """

        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("operation_id must be a non-empty string")
        with self._lock:
            if self._retired:
                raise ReplaySessionStateError("session is retired")
            if self._state == "playing":
                raise ReplaySessionStateError("step is not allowed while playing")
            if self._state not in {"ready", "paused"}:
                raise ReplaySessionStateError(
                    f"step requires ready or paused state, not {self._state}"
                )
            if self._next_bar_time is None:
                return ReplayStepResult(False, self.revision, None, "no_op")
            if self._inflight_ticket is not None:
                raise ReplaySessionStateError("replay_busy")
            target = self._next_bar_time
            # Cursor operation begins only after transitioning to paused.
            previous_state = self._state
            self._state = "paused"
            if previous_state == "ready":
                # Replay v1.0: a cursor operation out of ready must first
                # publish a stationary paused state with its own revision.
                self._publish_session_status_locked(
                    "paused", "user_command", operation_id, None
                )
            ticket = self._next_ticket
            self._next_ticket += 1
            self._inflight_ticket = ticket
        outcome: ComputationOutcome | None = None
        try:
            outcome = self._submit_advance(
                target, operation_id, None, ticket, "step"
            )
        finally:
            # If submit raised, we must release the ticket because the result
            # handler below will not run.  Otherwise the result handler keeps
            # the cursor busy and clears the ticket itself.
            if outcome is None:
                with self._lock:
                    self._inflight_ticket = None

        try:
            with self._lock:
                if outcome is None:
                    # Submit or future.result raised; cursor did not advance.
                    if self._retired:
                        return ReplayStepResult(False, self.revision, operation_id, "dropped")
                    self._state = "failed"
                    self._publish_session_status_locked(
                        "failed", "operation_failed", operation_id, None
                    )
                    return ReplayStepResult(False, self.revision, operation_id, "failed")
                revision = self.revision
                if outcome.status is ComputationStatus.COMPLETED:
                    # commit_result advances the cursor and pipeline atomically.
                    # If it recorded a committed value, the operation succeeded
                    # even if a later pause()/retire() changed the Session state.
                    if self._inflight_committed_ticket == ticket:
                        assert self._inflight_committed_revision is not None
                        return ReplayStepResult(
                            True,
                            self._inflight_committed_revision,
                            operation_id,
                            "completed",
                            outcome,
                        )
                    if not self._retired and self._state == "paused":
                        # Minimal/fake executors may return COMPLETED without
                        # invoking the task's commit callback.
                        self._commit_advance_locked(
                            outcome.value,
                            target=target,
                            operation_id=operation_id,
                            ticket=ticket,
                            commit_kind="step",
                        )
                        assert self._inflight_committed_revision is not None
                        return ReplayStepResult(
                            True,
                            self._inflight_committed_revision,
                            operation_id,
                            "completed",
                            outcome,
                        )
                    # The executor accepted the result but the Session-side
                    # commit boundary rejected it (e.g. retired between accept
                    # and commit). Drop without advancing.
                    return ReplayStepResult(False, self.revision, operation_id, "dropped", outcome)
                # Non-completed: never commit the cursor or pipeline state.
                if self._retired:
                    return ReplayStepResult(False, revision, operation_id, "dropped", outcome)
                if _outcome_is_failure(outcome):
                    self._state = "failed"
                    self._publish_session_status_locked(
                        "failed", "operation_failed", operation_id, None
                    )
                    return ReplayStepResult(False, self.revision, operation_id, "failed", outcome)
                return ReplayStepResult(False, revision, operation_id, "dropped", outcome)
        finally:
            with self._lock:
                self._inflight_ticket = None

    def pump_playback(self) -> PlaybackPumpResult:
        """Perform at most one due playback advance.

        This is the single testable/auto-playback entry point.  It is safe to
        call from any thread; when called while not playing, paused, retired or
        with an in-flight advance it is a no-op.  When the next bar is not yet
        due it re-schedules itself and returns ``"not_due"``.
        """

        with self._lock:
            if self._retired or self._state != "playing":
                return PlaybackPumpResult("no_op", None)
            if self._inflight_ticket is not None:
                # Backpressure: a previous advance is still in flight.
                return PlaybackPumpResult("no_op", None)
            if self._next_bar_time is None:
                if not self._ended_converged:
                    self._converge_to_paused_locked()
                return PlaybackPumpResult("converged", None)
            due = self._last_commit_mono + (1.0 / self._playback_speed)
            now = self._clock.now()
            if now < due:
                self._scheduler.schedule(due)
                return PlaybackPumpResult("not_due", due)
            target = self._next_bar_time
            ticket = self._next_ticket
            self._next_ticket += 1
            self._inflight_ticket = ticket
        outcome: ComputationOutcome | None = None
        try:
            outcome = self._submit_advance(
                target, None, "playing", ticket, "playback"
            )
        finally:
            if outcome is None:
                with self._lock:
                    self._inflight_ticket = None

        try:
            with self._lock:
                if outcome is None:
                    if self._retired:
                        return PlaybackPumpResult("dropped", None)
                    self._state = "failed"
                    self._publish_session_status_locked(
                        "failed", "operation_failed", None, None
                    )
                    return PlaybackPumpResult("failed", None)
                if outcome.status is ComputationStatus.COMPLETED:
                    # commit_result advances the cursor and pipeline atomically.
                    # If it recorded a committed value, the operation
                    # succeeded even if a later pause()/retire() changed the
                    # Session state.
                    if self._inflight_committed_ticket == ticket:
                        if self._state == "playing":
                            if self._next_bar_time is None:
                                next_due = self._clock.now()
                            else:
                                next_due = self._last_commit_mono + (
                                    1.0 / self._playback_speed
                                )
                            self._scheduler.schedule(next_due)
                            return PlaybackPumpResult("advanced", next_due)
                        return PlaybackPumpResult("advanced", None)
                    if self._state == "playing" and not self._retired:
                        self._commit_advance_locked(
                            outcome.value,
                            target=target,
                            operation_id=None,
                            ticket=ticket,
                            commit_kind="playback",
                        )
                        if self._next_bar_time is None:
                            next_due = self._clock.now()
                        else:
                            next_due = self._last_commit_mono + (
                                1.0 / self._playback_speed
                            )
                        self._scheduler.schedule(next_due)
                        return PlaybackPumpResult("advanced", next_due)
                    # The executor accepted the result but the Session-side
                    # commit boundary rejected it (e.g. retired between accept
                    # and commit). Drop without advancing.
                    return PlaybackPumpResult("dropped", None)
                # Non-completed: never advance.
                if self._retired:
                    return PlaybackPumpResult("dropped", None)
                if _outcome_is_failure(outcome):
                    self._state = "failed"
                    self._publish_session_status_locked(
                        "failed", "operation_failed", None, None
                    )
                return PlaybackPumpResult("failed" if _outcome_is_failure(outcome) else "dropped", None)
        finally:
            with self._lock:
                self._inflight_ticket = None

    def retire(self) -> None:
        """Retire the Session and isolate any late result."""

        with self._lock:
            if self._retired:
                return
            self._retired = True
            # Replay v1.0: retired is the terminal state, even from failed.
            self._state = "retired"
            self._publish_session_status_locked("retired", None, None, None)
        self._scheduler.cancel()
        if self._owns_scheduler:
            self._scheduler.stop()

    # ------------------------------------------------------------------
    # Internal: computation submission
    # ------------------------------------------------------------------

    def _submit_advance(
        self,
        target: datetime,
        operation_id: str | None,
        expected_state: str | None,
        ticket: int,
        commit_kind: str = "step",
    ) -> ComputationOutcome:
        """Submit one advance computation and block until it resolves."""

        pipeline = self._pipeline
        identity = self._pipeline_identity
        session_id = self._session_id
        generation = self._service_generation
        deadline = self._deadline_for_now()

        def callable(_task: ComputationTask) -> PipelineResult:
            return pipeline.preview(target)

        # Per-task cancellation flag.  Once the Session gives up waiting for a
        # result (e.g. future.result timeout), this flag is set so that any late
        # accept_result/commit_result callback from the executor is rejected
        # before it can mutate the pipeline or cursor.
        _task_cancelled = Event()

        def commit_result(value: PipelineResult) -> None:
            # Re-validate under the Session lock: only commit if the ticket is
            # still in flight, the Session has not been retired, the expected
            # state is still valid, and this task has not been explicitly
            # cancelled.  This closes the race where pause()/retire() or a local
            # timeout happens between accept_result and commit_result.
            with self._lock:
                if (
                    self._retired
                    or _task_cancelled.is_set()
                    or self._inflight_ticket != ticket
                ):
                    return
                if expected_state is not None and self._state != expected_state:
                    return
                self._commit_advance_locked(
                    value,
                    target=target,
                    operation_id=operation_id,
                    ticket=ticket,
                    commit_kind=commit_kind,
                )

        def accept_result(_value: Any) -> bool:
            if self._retired or _task_cancelled.is_set():
                return False
            if self._inflight_ticket != ticket:
                return False
            if expected_state is not None and self._state != expected_state:
                return False
            return True

        def is_session_valid() -> bool:
            # The Session remains valid while not retired.  State changes such
            # as ready->paused or playing->paused do not invalidate the result
            # by themselves; the commit_result guard below checks state when
            # needed.
            return not self._retired

        def is_cancelled() -> bool:
            return self._retired or _task_cancelled.is_set()

        with self._lock:
            self._inflight_committed_ticket = None
            self._inflight_committed_value = None
            self._inflight_committed_revision = None

        task = ComputationTask(
            task_id=new_task_id(),
            session_id=session_id,
            session_generation=generation,
            pipeline_identity=identity,
            priority=ComputationPriority.REPLAY_INTERACTIVE,
            callable=callable,
            operation_id=operation_id,
            deadline=deadline,
            is_cancelled=is_cancelled,
            is_session_valid=is_session_valid,
            accept_result=accept_result,
            commit_result=commit_result,
        )
        try:
            future = self._executor.submit(task)
            return future.result(timeout=self._computation_timeout)
        except TimeoutError as exc:
            # T0-020 ordering: commit_result() may have already published the
            # preview before future.set_result() resolves.  If the preview was
            # committed, we must treat the step as completed and advance the
            # cursor rather than failing with an inconsistent pipeline.
            with self._lock:
                committed = self._inflight_committed_value
                if (
                    self._inflight_committed_ticket == ticket
                    and committed is not None
                ):
                    return ComputationOutcome(
                        task_id=task.task_id,
                        status=ComputationStatus.COMPLETED,
                        value=committed,
                    )
                _task_cancelled.set()
                return ComputationOutcome(
                    task_id=task.task_id,
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.DEADLINE_EXCEEDED,
                    exception=exc,
                )
        except ComputationQueueFullError as exc:
            _task_cancelled.set()
            return ComputationOutcome(
                task_id=task.task_id,
                status=ComputationStatus.CANCELLED,
                cancel_reason=CancelReason.EXECUTOR_CLOSED,
                exception=exc,
            )
        except ComputationExecutorClosedError as exc:
            _task_cancelled.set()
            return ComputationOutcome(
                task_id=task.task_id,
                status=ComputationStatus.CANCELLED,
                cancel_reason=CancelReason.EXECUTOR_CLOSED,
                exception=exc,
            )
        except Exception as exc:
            # Treat any other infrastructure failure as an closed/unavailable
            # executor.  The original exception is preserved for T0-044 mapping.
            _task_cancelled.set()
            return ComputationOutcome(
                task_id=task.task_id,
                status=ComputationStatus.CANCELLED,
                cancel_reason=CancelReason.EXECUTOR_CLOSED,
                exception=exc,
            )

    def _deadline_for_now(self) -> float | None:
        if self._deadline_seconds is None:
            return None
        return self._clock.now() + self._deadline_seconds

    def _initialize_ready(self) -> None:
        """Compute and publish the initial ``ready`` snapshot at ``start_time``."""

        target = self._start_time
        with self._lock:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._inflight_ticket = ticket
        outcome: ComputationOutcome | None = None
        try:
            outcome = self._submit_advance(
                target,
                self._initial_operation_id,
                None,
                ticket,
                "initialize",
            )
        finally:
            if outcome is None:
                with self._lock:
                    self._inflight_ticket = None
        with self._lock:
            self._inflight_ticket = None
            if outcome is None:
                outcome = ComputationOutcome(
                    task_id="",
                    status=ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.EXECUTOR_CLOSED,
                )
            if (
                outcome.status is ComputationStatus.COMPLETED
                and not self._retired
            ):
                if self._inflight_committed_ticket != ticket:
                    self._commit_advance_locked(
                        outcome.value,
                        target=target,
                        operation_id=self._initial_operation_id,
                        ticket=ticket,
                        commit_kind="initialize",
                    )
                assert self._inflight_committed_revision is not None
                result_revision = self._inflight_committed_revision
            else:
                self._state = "failed"
                self._publish_session_status_locked(
                    "failed", "operation_failed", self._initial_operation_id, None
                )
                # T0-044 publishes operation_failed outside this runtime layer.
                # Reserve its revision now so later Session events (especially
                # retire) cannot reuse the same number.
                result_revision = self._allocate_revision_locked()
            self._initial_result = ReplayInitialResult(
                outcome=outcome,
                revision=result_revision,
            )

    # ------------------------------------------------------------------
    # Internal: state helpers (all called under self._lock)
    # ------------------------------------------------------------------

    def _commit_advance_locked(
        self,
        value: PipelineResult,
        *,
        target: datetime,
        operation_id: str | None,
        ticket: int,
        commit_kind: str,
    ) -> None:
        """Atomically publish one accepted preview into all Session state.

        The pipeline, cursor, last result, state projection and snapshot share
        this linearization point.  A concurrent pause/retire therefore happens
        wholly before the commit (and rejects it) or wholly after the committed
        snapshot; it cannot split pipeline state from the Replay cursor.
        """

        if commit_kind not in {"initialize", "step", "playback"}:
            raise ValueError(f"unsupported commit_kind: {commit_kind}")

        self._pipeline.commit_preview(value)
        self._last_pipeline_result = value
        self._current_time = target
        self._next_bar_time = self._resolve_next_bar(
            target,
            consumed=(commit_kind != "initialize"),
        )
        if commit_kind == "initialize":
            self._state = "ready"
        elif commit_kind == "playback":
            self._last_commit_mono = self._clock.now()
        self._inflight_committed_ticket = ticket
        self._inflight_committed_value = value
        self._publish_workbench_snapshot_locked(operation_id)
        self._inflight_committed_revision = self._revision

    def _resolve_next_bar(self, current: datetime, *, consumed: bool) -> datetime | None:
        if not self._actual_bar_times:
            return None
        return self._market_session.next_actual_bar_time(
            current,
            self._actual_bar_times,
            current_time_consumed=consumed,
        )

    def _converge_to_paused_locked(self) -> None:
        """Converge to ``paused`` once when the sequence end is reached."""

        if self._ended_converged or self._retired:
            return
        self._ended_converged = True
        if self._state == "playing":
            self._state = "paused"
            self._publish_session_status_locked("paused", None, None, None)
        self._scheduler.cancel()

    def _allocate_revision_locked(self) -> int:
        self._revision += 1
        return self._revision

    def _publish_session_status_locked(
        self,
        state: str,
        reason: str | None,
        operation_id: str | None,
        playback_speed: int | None,
    ) -> None:
        payload: dict[str, Any] = {"state": state}
        if reason is not None:
            payload["reason"] = reason
        if playback_speed is not None:
            payload["playback_speed"] = playback_speed
        revision = self._allocate_revision_locked()
        self._emit_event_locked("session_status", payload, operation_id, revision)

    def _publish_workbench_snapshot_locked(
        self, operation_id: str | None
    ) -> None:
        revision = self._allocate_revision_locked()
        payload = self._build_snapshot_payload_locked(revision=revision)
        self._emit_event_locked(
            "workbench_snapshot", payload, operation_id, revision
        )

    def _build_snapshot_payload_locked(
        self, *, revision: int | None = None
    ) -> dict[str, Any]:
        if self._last_pipeline_result is None:
            raise ReplaySessionStateError("no pipeline result to project")
        if revision is None:
            revision = self.revision
        session_input = SessionProjectionInput(
            session_id=self._session_id,
            session_type="replay",
            symbol=self._prepared.symbol,
            trade_date=self._prepared.trade_date,
            state=self._state,
            revision=revision,
        )
        replay_input = ReplayProjectionInput(
            granularity=self._granularity,
            current_time=self._current_time.strftime(MARKET_TIMESTAMP_FORMAT),
            next_bar_time=(
                None
                if self._next_bar_time is None
                else self._next_bar_time.strftime(MARKET_TIMESTAMP_FORMAT)
            ),
            start_time=self._start_time.strftime(MARKET_TIMESTAMP_FORMAT),
            end_time=self._end_time.strftime(MARKET_TIMESTAMP_FORMAT),
            playing=(self._state == "playing"),
            playback_speed=self._playback_speed,
            step_seconds=self._step_seconds,
        )
        projection = build_workbench_projection(
            self._last_pipeline_result, session_input, replay_input
        )
        return projection.to_dict()

    def _emit_event_locked(
        self,
        event_type: str,
        payload: dict[str, Any],
        operation_id: str | None,
        revision: int,
    ) -> None:
        envelope: dict[str, Any] = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "service_generation": self._service_generation,
            "session_id": self._session_id,
            "revision": revision,
            "event_type": event_type,
            "payload": payload,
        }
        if operation_id is not None:
            envelope["operation_id"] = operation_id
        self._on_event(envelope)


def _outcome_is_failure(outcome: ComputationOutcome) -> bool:
    """Return whether a non-completed outcome should fail the Session.

    Explicit cancellations, session invalidation and supersession are dropped
    silently (the owning transition is responsible for the state).  Failed
    callables, expired deadlines and a closed executor transition the Session
    to ``failed``.
    """

    if outcome.status is ComputationStatus.FAILED:
        return True
    if outcome.status is not ComputationStatus.CANCELLED:
        return False
    return outcome.cancel_reason in {
        CancelReason.DEADLINE_EXCEEDED,
        CancelReason.EXECUTOR_CLOSED,
    }


__all__ = [
    "EventPublisher",
    "PlaybackPumpResult",
    "ReplayInitialResult",
    "ReplaySession",
    "ReplaySessionError",
    "ReplaySessionStateError",
    "ReplayStepResult",
    "REPLAY_SCHEMA_VERSION",
]
