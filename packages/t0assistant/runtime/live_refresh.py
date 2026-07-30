"""Independent refresh orchestration for a running Live Session.

The quote, one-minute, and official five-minute feeds deliberately have
separate schedules and watermarks.  A slow or empty branch therefore cannot
hold back another branch, and an empty official-five-minute response is a
successful no-op rather than an error.

This module owns scheduling only.  Provider access, market normalization, and
pipeline calculation remain behind :class:`LiveRefreshInputPort`; accepted
updates continue through the existing Live projection revision authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Callable, Mapping, Protocol, Sequence

from .computation_contract import (
    ComputationExecutorPort,
    ComputationPriority,
    ComputationStatus,
    ComputationTask,
    PipelineInstanceIdentity,
    new_task_id,
)
from .coordinator import SessionSpec, SessionType
from .live_projection_store import LiveIncrementalUpdate


class LiveRefreshError(RuntimeError):
    """Base class for stable Live refresh orchestration failures."""


class LiveRefreshValidationError(LiveRefreshError, ValueError):
    """A refresh input or result violates the Live refresh contract."""


class LiveRefreshKind(str, Enum):
    """The three independently scheduled Live data branches."""

    QUOTE = "quote"
    ONE_MINUTE = "one_minute"
    OFFICIAL_FIVE_MINUTE = "official_five_minute"


@dataclass(frozen=True, slots=True)
class LiveRefreshIntervals:
    """Wall-clock cadence for each Live refresh branch."""

    quote: timedelta = timedelta(seconds=3)
    one_minute: timedelta = timedelta(seconds=15)
    official_five_minute: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        for name, value in (
            ("quote", self.quote),
            ("one_minute", self.one_minute),
            ("official_five_minute", self.official_five_minute),
        ):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise LiveRefreshValidationError(
                    f"{name} interval must be a positive timedelta"
                )

    def for_kind(self, kind: LiveRefreshKind) -> timedelta:
        if kind is LiveRefreshKind.QUOTE:
            return self.quote
        if kind is LiveRefreshKind.ONE_MINUTE:
            return self.one_minute
        return self.official_five_minute


@dataclass(frozen=True, slots=True)
class LiveRefreshResult:
    """One branch's normalized result.

    ``data_time`` is the newest source timestamp observed by this branch.
    ``None`` means that no newer valid data exists.  This is the normal result
    while waiting for the next official 5m bar to close.
    """

    data_time: datetime | None = None
    updates: Sequence[LiveIncrementalUpdate] = ()

    @classmethod
    def no_change(cls) -> "LiveRefreshResult":
        return cls()


class LiveRefreshInputPort(Protocol):
    """Provider/pipeline boundary consumed by the refresh scheduler.

    Calls for different kinds may run concurrently.  Implementations must keep
    branch-local provider/calculation state isolated, or use their own narrow
    serialization boundary before touching shared mutable pipeline state.
    """

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        """Return validated increments newer than the branch watermark."""


LiveRefreshUpdateHandler = Callable[[LiveIncrementalUpdate], object]
LiveRefreshFailureHandler = Callable[[LiveRefreshKind, BaseException], None]


@dataclass(frozen=True, slots=True)
class LiveRefreshBranchState:
    """Public immutable state for one independent refresh branch."""

    kind: LiveRefreshKind
    latest_data_time: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    next_due_at: datetime | None
    last_failure: BaseException | None
    in_flight: bool


@dataclass(slots=True)
class _MutableBranchState:
    latest_data_time: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_due_at: datetime | None = None
    last_failure: BaseException | None = None


class LiveRefreshScheduler:
    """Run the three Live refresh branches on independent cadences.

    ``run_due`` is intentionally driven by an injected/caller-supplied
    timestamp.  Production can call it from a timer, while tests advance a
    deterministic clock without sleeping.  Every due branch is submitted with
    Live priority before results are collected, allowing a multi-worker
    executor to run independent provider/pipeline work concurrently.
    """

    _KINDS = (
        LiveRefreshKind.QUOTE,
        LiveRefreshKind.ONE_MINUTE,
        LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
    )

    def __init__(
        self,
        spec: SessionSpec,
        input_port: LiveRefreshInputPort,
        executor: ComputationExecutorPort,
        *,
        on_update: LiveRefreshUpdateHandler,
        intervals: LiveRefreshIntervals = LiveRefreshIntervals(),
        clock: Callable[[], datetime] | None = None,
        on_failure: LiveRefreshFailureHandler | None = None,
        initial_data_times: Mapping[LiveRefreshKind | str, datetime | None]
        | None = None,
    ) -> None:
        if not isinstance(spec, SessionSpec):
            raise TypeError("spec must be a SessionSpec")
        if spec.session_type is not SessionType.LIVE or spec.trade_date is not None:
            raise LiveRefreshValidationError(
                "LiveRefreshScheduler requires a live SessionSpec"
            )
        if not callable(getattr(input_port, "refresh", None)):
            raise TypeError("input_port must implement refresh")
        if not callable(getattr(executor, "submit", None)):
            raise TypeError("executor must implement submit")
        if not callable(on_update):
            raise TypeError("on_update must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if on_failure is not None and not callable(on_failure):
            raise TypeError("on_failure must be callable")

        self._spec = spec
        self._input_port = input_port
        self._executor = executor
        self._on_update = on_update
        self._intervals = intervals
        self._clock = clock or datetime.now
        self._on_failure = on_failure
        self._lock = RLock()
        self._retired = False
        self._states = {kind: _MutableBranchState() for kind in self._KINDS}
        self._active_kinds: set[LiveRefreshKind] = set()
        for raw_kind, data_time in (initial_data_times or {}).items():
            kind = _coerce_kind(raw_kind)
            if data_time is not None and (
                not isinstance(data_time, datetime) or data_time.tzinfo is not None
            ):
                raise LiveRefreshValidationError(
                    "initial data times must be naive Asia/Shanghai datetimes"
                )
            self._states[kind].latest_data_time = data_time
        identity_seed = id(self)
        self._identities = {
            kind: PipelineInstanceIdentity(
                instance_id=identity_seed + offset,
                generation=spec.generation,
                session_id=spec.session_id,
            )
            for offset, kind in enumerate(self._KINDS)
        }

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired

    @property
    def states(self) -> Mapping[LiveRefreshKind, LiveRefreshBranchState]:
        with self._lock:
            return {
                kind: self._freeze_state(kind, state)
                for kind, state in self._states.items()
            }

    def state_for(self, kind: LiveRefreshKind | str) -> LiveRefreshBranchState:
        resolved = _coerce_kind(kind)
        with self._lock:
            return self._freeze_state(resolved, self._states[resolved])

    def retire(self) -> None:
        """Prevent new work and reject results from already submitted work."""

        with self._lock:
            self._retired = True

    def run_due(
        self,
        observed_at: datetime | None = None,
    ) -> Mapping[LiveRefreshKind, LiveRefreshBranchState]:
        """Run every branch due at ``observed_at`` and return all branch states."""

        now = self._resolve_now(observed_at)
        with self._lock:
            if self._retired:
                return self.states
            due = [
                kind
                for kind in self._KINDS
                if self._states[kind].next_due_at is None
                or now >= self._states[kind].next_due_at
            ]
        self._run(now, due)
        return self.states

    def retry(
        self,
        kind: LiveRefreshKind | str,
        observed_at: datetime | None = None,
    ) -> LiveRefreshBranchState:
        """Force one branch immediately without disturbing other schedules."""

        resolved = _coerce_kind(kind)
        now = self._resolve_now(observed_at)
        with self._lock:
            if self._retired:
                return self._freeze_state(resolved, self._states[resolved])
        self._run(now, [resolved])
        return self.state_for(resolved)

    def _run(self, observed_at: datetime, kinds: Sequence[LiveRefreshKind]) -> None:
        futures = []
        for kind in kinds:
            with self._lock:
                if self._retired or kind in self._active_kinds:
                    continue
                self._active_kinds.add(kind)
                state = self._states[kind]
                state.last_attempt_at = observed_at
                state.next_due_at = observed_at + self._intervals.for_kind(kind)
                watermark = state.latest_data_time
            task = ComputationTask(
                task_id=new_task_id(),
                session_id=self._spec.session_id,
                session_generation=self._spec.generation,
                pipeline_identity=self._identities[kind],
                priority=ComputationPriority.LIVE,
                callable=lambda _task, branch=kind, latest=watermark: (
                    self._input_port.refresh(
                        branch,
                        self._spec,
                        observed_at=observed_at,
                        latest_data_time=latest,
                    )
                ),
                is_session_valid=lambda: not self.retired,
            )
            try:
                futures.append((kind, self._executor.submit(task)))
            except BaseException as exc:
                with self._lock:
                    self._active_kinds.discard(kind)
                self._record_failure(kind, exc)

        for kind, future in futures:
            try:
                outcome = future.result()
                if outcome.status is ComputationStatus.FAILED:
                    assert outcome.exception is not None
                    raise outcome.exception
                if outcome.status is not ComputationStatus.COMPLETED:
                    continue
                self._accept_result(kind, observed_at, outcome.value)
            except BaseException as exc:
                self._record_failure(kind, exc)
            finally:
                with self._lock:
                    self._active_kinds.discard(kind)

    def _accept_result(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        result: object,
    ) -> None:
        if not isinstance(result, LiveRefreshResult):
            raise LiveRefreshValidationError(
                "refresh must return LiveRefreshResult"
            )
        data_time = result.data_time
        if data_time is not None and (
            not isinstance(data_time, datetime) or data_time.tzinfo is not None
        ):
            raise LiveRefreshValidationError(
                "refresh data_time must be a naive Asia/Shanghai datetime"
            )

        with self._lock:
            if self._retired:
                return
            state = self._states[kind]
            current = state.latest_data_time
            if data_time is not None and current is not None and data_time < current:
                raise LiveRefreshValidationError(
                    f"{kind.value} data_time cannot move backwards"
                )

        updates = tuple(result.updates)
        for update in updates:
            self._validate_update(kind, update)
        if data_time is None and updates:
            raise LiveRefreshValidationError(
                "updates require a non-null data_time"
            )

        for update in updates:
            with self._lock:
                if self._retired:
                    return
            self._on_update(update)

        with self._lock:
            if self._retired:
                return
            state = self._states[kind]
            if data_time is not None:
                state.latest_data_time = data_time
            state.last_success_at = observed_at
            state.last_failure = None

    def _validate_update(
        self,
        kind: LiveRefreshKind,
        update: LiveIncrementalUpdate,
    ) -> None:
        if not isinstance(update, LiveIncrementalUpdate):
            raise LiveRefreshValidationError(
                "refresh updates must be LiveIncrementalUpdate values"
            )
        if (
            update.session_id != self._spec.session_id
            or update.generation != self._spec.generation
        ):
            raise LiveRefreshValidationError(
                "refresh update does not belong to this Live Session"
            )
        allowed = {
            LiveRefreshKind.QUOTE: {"market_update"},
            LiveRefreshKind.ONE_MINUTE: {"market_update", "indicators_updated"},
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE: {
                "market_update",
                "indicators_updated",
                "chan_analysis_replaced",
            },
        }[kind]
        if update.event_type not in allowed:
            raise LiveRefreshValidationError(
                f"{kind.value} cannot publish {update.event_type}"
            )

    def _record_failure(
        self,
        kind: LiveRefreshKind,
        failure: BaseException,
    ) -> None:
        with self._lock:
            if self._retired:
                return
            self._states[kind].last_failure = failure
        if self._on_failure is not None:
            try:
                self._on_failure(kind, failure)
            except Exception:
                return

    def _resolve_now(self, observed_at: datetime | None) -> datetime:
        resolved = self._clock() if observed_at is None else observed_at
        if not isinstance(resolved, datetime) or resolved.tzinfo is not None:
            raise LiveRefreshValidationError(
                "observed_at must be a naive Asia/Shanghai datetime"
            )
        return resolved

    def _freeze_state(
        self,
        kind: LiveRefreshKind,
        state: _MutableBranchState,
    ) -> LiveRefreshBranchState:
        return LiveRefreshBranchState(
            kind=kind,
            latest_data_time=state.latest_data_time,
            last_attempt_at=state.last_attempt_at,
            last_success_at=state.last_success_at,
            next_due_at=state.next_due_at,
            last_failure=state.last_failure,
            in_flight=kind in self._active_kinds,
        )


def _coerce_kind(kind: LiveRefreshKind | str) -> LiveRefreshKind:
    try:
        return kind if isinstance(kind, LiveRefreshKind) else LiveRefreshKind(kind)
    except ValueError as exc:
        raise LiveRefreshValidationError(f"unknown refresh kind: {kind!r}") from exc


__all__ = [
    "LiveRefreshBranchState",
    "LiveRefreshError",
    "LiveRefreshInputPort",
    "LiveRefreshIntervals",
    "LiveRefreshKind",
    "LiveRefreshResult",
    "LiveRefreshScheduler",
    "LiveRefreshValidationError",
]
