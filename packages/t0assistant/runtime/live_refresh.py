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

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from threading import Lock, RLock
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
from .live_market_view import PollingProfile
from .live_projection_store import LiveIncrementalUpdate


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class LiveRefreshError(RuntimeError):
    """Base class for stable Live refresh orchestration failures."""


class LiveRefreshValidationError(LiveRefreshError, ValueError):
    """A refresh input or result violates the Live refresh contract."""


class LiveRefreshKind(str, Enum):
    """The independently scheduled Live data branches."""

    QUOTE = "quote"
    ONE_MINUTE = "one_minute"
    OFFICIAL_FIVE_MINUTE = "official_five_minute"
    OFFICIAL_THIRTY_MINUTE = "official_thirty_minute"


@dataclass(frozen=True, slots=True)
class LiveRefreshIntervals:
    """Wall-clock cadence for each Live refresh branch."""

    quote: timedelta = timedelta(seconds=3)
    one_minute: timedelta = timedelta(seconds=15)
    official_five_minute: timedelta = timedelta(seconds=30)
    official_thirty_minute: timedelta = timedelta(seconds=15)
    reduced_quote: timedelta = timedelta(seconds=15)
    reduced_one_minute: timedelta = timedelta(seconds=30)
    reduced_official_five_minute: timedelta = timedelta(seconds=60)
    reduced_official_thirty_minute: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        for name, value in (
            ("quote", self.quote),
            ("one_minute", self.one_minute),
            ("official_five_minute", self.official_five_minute),
            ("official_thirty_minute", self.official_thirty_minute),
            ("reduced_quote", self.reduced_quote),
            ("reduced_one_minute", self.reduced_one_minute),
            ("reduced_official_five_minute", self.reduced_official_five_minute),
            ("reduced_official_thirty_minute", self.reduced_official_thirty_minute),
        ):
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise LiveRefreshValidationError(
                    f"{name} interval must be a positive timedelta"
                )

    def for_kind(
        self,
        kind: LiveRefreshKind,
        *,
        polling_profile: PollingProfile = "active",
    ) -> timedelta:
        if polling_profile == "reduced":
            if kind is LiveRefreshKind.QUOTE:
                return self.reduced_quote
            if kind is LiveRefreshKind.ONE_MINUTE:
                return self.reduced_one_minute
            if kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE:
                return self.reduced_official_five_minute
            return self.reduced_official_thirty_minute
        if kind is LiveRefreshKind.QUOTE:
            return self.quote
        if kind is LiveRefreshKind.ONE_MINUTE:
            return self.one_minute
        if kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE:
            return self.official_five_minute
        return self.official_thirty_minute


@dataclass(frozen=True, slots=True)
class LiveRefreshBackoff:
    """Bound consecutive branch failures without coupling their schedules."""

    multiplier: float = 2.0
    maximum: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if (
            isinstance(self.multiplier, bool)
            or not isinstance(self.multiplier, (int, float))
            or self.multiplier < 1
        ):
            raise LiveRefreshValidationError(
                "backoff multiplier must be a number >= 1"
            )
        if not isinstance(self.maximum, timedelta) or self.maximum <= timedelta(0):
            raise LiveRefreshValidationError(
                "backoff maximum must be a positive timedelta"
            )

    def delay(
        self,
        interval: timedelta,
        consecutive_failures: int,
    ) -> timedelta:
        delay = interval * (self.multiplier ** consecutive_failures)
        return min(delay, self.maximum)


@dataclass(frozen=True, slots=True)
class LiveRefreshResult:
    """One branch's normalized result.

    ``data_time`` is the newest source timestamp observed by this branch.
    ``None`` means that no newer valid data exists.  This is the normal result
    while waiting for the next official 5m bar to close.

    ``market_epoch`` stamps the Live market epoch active when this branch
    finished.  The scheduler rejects stale epochs at the accept boundary so a
    late result cannot mutate a newer trading-day baseline.

    ``projection_seq`` is the lock-order snapshot generation number assigned
    when a branch rebuilds the shared projection.  The scheduler publishes
    sequenced results in that order even when futures complete in kind order,
    so an older official ``bars_5m`` payload cannot delete a newer dynamic 5m
    bar.  ``None`` means the result has no ordering constraint (no-op and
    test doubles).
    """

    data_time: datetime | None = None
    updates: Sequence[LiveIncrementalUpdate] = ()
    market_epoch: int | None = None
    projection_seq: int | None = None

    @classmethod
    def no_change(cls, *, market_epoch: int | None = None) -> "LiveRefreshResult":
        return cls(market_epoch=market_epoch)


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
LiveRefreshFailureHandler = Callable[
    [LiveRefreshKind, BaseException, int | None], None
]
LiveThirtyMinuteDelayedHandler = Callable[[bool], None]


@dataclass(frozen=True, slots=True)
class LiveRefreshBranchState:
    """Public immutable state for one independent refresh branch."""

    kind: LiveRefreshKind
    latest_data_time: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    next_due_at: datetime | None
    last_failure: BaseException | None
    consecutive_failures: int
    in_flight: bool
    thirty_minute_delayed: bool = False


@dataclass(slots=True)
class _MutableBranchState:
    latest_data_time: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    next_due_at: datetime | None = None
    last_failure: BaseException | None = None
    consecutive_failures: int = 0
    # 30m boundary-triggered scheduling (design §10).
    # ``thirty_minute_pending_boundary`` is the 30m close boundary whose
    # official bar we are currently waiting for.  ``boundary_first_attempt_at``
    # records when we first tried to fetch it, so we can detect the 2-minute
    # delay threshold.
    thirty_minute_pending_boundary: datetime | None = None
    boundary_first_attempt_at: datetime | None = None
    thirty_minute_delayed: bool = False


class LiveRefreshScheduler:
    """Run the three Live refresh branches on independent cadences.

    ``run_due`` is intentionally driven by an injected/caller-supplied
    timestamp.  Production can call it from a timer, while tests advance a
    deterministic clock without sleeping.  Every due branch is submitted with
    Live priority before results are collected, allowing a multi-worker
    executor to run independent provider/pipeline work concurrently.

    Futures are still waited in kind order, but sequenced results are not
    published until every earlier ``projection_seq`` has been accepted.  Each
    result's updates are then emitted as one batch under a dedicated publish
    lock so a concurrent ``run_due``/``retry`` cannot insert the next sequence
    between ``bars_5m`` and ``chan_analysis_replaced``.  ``on_update`` runs
    without the scheduler state lock and must not re-enter publish on the
    same thread.
    """

    _KINDS = (
        LiveRefreshKind.QUOTE,
        LiveRefreshKind.ONE_MINUTE,
        LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
        LiveRefreshKind.OFFICIAL_THIRTY_MINUTE,
    )

    def __init__(
        self,
        spec: SessionSpec,
        input_port: LiveRefreshInputPort,
        executor: ComputationExecutorPort,
        *,
        on_update: LiveRefreshUpdateHandler,
        intervals: LiveRefreshIntervals = LiveRefreshIntervals(),
        backoff: LiveRefreshBackoff = LiveRefreshBackoff(),
        clock: Callable[[], datetime] | None = None,
        on_failure: LiveRefreshFailureHandler | None = None,
        initial_data_times: Mapping[LiveRefreshKind | str, datetime | None]
        | None = None,
        thirty_minute_boundary_provider: Callable[[datetime], datetime | None]
        | None = None,
        on_thirty_minute_delayed: LiveThirtyMinuteDelayedHandler | None = None,
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
        if (
            thirty_minute_boundary_provider is not None
            and not callable(thirty_minute_boundary_provider)
        ):
            raise TypeError("thirty_minute_boundary_provider must be callable")
        if (
            on_thirty_minute_delayed is not None
            and not callable(on_thirty_minute_delayed)
        ):
            raise TypeError("on_thirty_minute_delayed must be callable")

        self._spec = spec
        self._input_port = input_port
        self._executor = executor
        self._on_update = on_update
        self._intervals = intervals
        self._backoff = backoff
        self._clock = clock or datetime.now
        self._on_failure = on_failure
        self._thirty_minute_boundary_provider = thirty_minute_boundary_provider
        self._on_thirty_minute_delayed = on_thirty_minute_delayed
        self._lock = RLock()
        self._publish_lock = Lock()
        self._retired = False
        self._polling_profile: PollingProfile = "active"
        self._scheduler_market_epoch = self._read_input_market_epoch()
        self._next_projection_seq = _next_projection_seq(
            self._read_input_projection_seq()
        )
        self._pending_by_seq: dict[
            int, tuple[LiveRefreshKind, datetime, LiveRefreshResult]
        ] = {}
        self._pending_unsequenced: list[
            tuple[LiveRefreshKind, datetime, LiveRefreshResult]
        ] = []
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
            self._pending_by_seq.clear()
            self._pending_unsequenced.clear()

    def set_polling_profile(self, profile: PollingProfile) -> None:
        with self._lock:
            if profile not in {"active", "reduced", "idle"}:
                raise LiveRefreshValidationError(
                    f"unknown polling profile: {profile!r}"
                )
            self._polling_profile = profile

    @property
    def polling_profile(self) -> PollingProfile:
        with self._lock:
            return self._polling_profile

    def run_reconciliation(
        self,
        observed_at: datetime | None = None,
    ) -> Mapping[LiveRefreshKind, LiveRefreshBranchState]:
        """Force one refresh pass for every branch (#130 PR-B close reconcile)."""

        now = self._resolve_now(observed_at)
        with self._lock:
            if self._retired:
                return self.states
            previous = self._polling_profile
            self._polling_profile = "active"
        try:
            self._run(now, list(self._KINDS))
        finally:
            with self._lock:
                if not self._retired:
                    self._polling_profile = previous
        return self.states

    def reset_branch_watermarks(
        self,
        data_times: Mapping[LiveRefreshKind | str, datetime | None] | None = None,
        *,
        market_epoch: int | None = None,
    ) -> None:
        """Clear branch schedules after an atomic day switch."""

        with self._lock:
            if market_epoch is not None:
                self._scheduler_market_epoch = market_epoch
            self._pending_by_seq.clear()
            self._pending_unsequenced.clear()
            self._next_projection_seq = _next_projection_seq(
                self._read_input_projection_seq()
            )
            for kind in self._KINDS:
                state = self._states[kind]
                state.latest_data_time = None
                state.last_attempt_at = None
                state.last_success_at = None
                state.next_due_at = None
                state.last_failure = None
                state.consecutive_failures = 0
                state.thirty_minute_pending_boundary = None
                state.boundary_first_attempt_at = None
                state.thirty_minute_delayed = False
            for raw_kind, data_time in (data_times or {}).items():
                kind = _coerce_kind(raw_kind)
                if data_time is not None and (
                    not isinstance(data_time, datetime)
                    or data_time.tzinfo is not None
                ):
                    raise LiveRefreshValidationError(
                        "reset data times must be naive Asia/Shanghai datetimes"
                    )
                self._states[kind].latest_data_time = data_time

    def run_due(
        self,
        observed_at: datetime | None = None,
        *,
        polling_profile: PollingProfile | None = None,
    ) -> Mapping[LiveRefreshKind, LiveRefreshBranchState]:
        """Run every branch due at ``observed_at`` and return all branch states."""

        now = self._resolve_now(observed_at)
        with self._lock:
            if self._retired:
                return self.states
            if polling_profile is not None:
                self._polling_profile = polling_profile
            profile = self._polling_profile
            if profile == "idle":
                return self.states
            due = [
                kind
                for kind in self._KINDS
                if self._states[kind].next_due_at is None
                or now >= self._states[kind].next_due_at
            ]
        self._run(now, due, polling_profile=profile)
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
        self._run(now, [resolved], polling_profile=self.polling_profile)
        return self.state_for(resolved)

    def _run(
        self,
        observed_at: datetime,
        kinds: Sequence[LiveRefreshKind],
        *,
        polling_profile: PollingProfile | None = None,
    ) -> None:
        profile = polling_profile or self.polling_profile
        futures: list[tuple[LiveRefreshKind, object, int | None]] = []
        for kind in kinds:
            with self._lock:
                if self._retired or kind in self._active_kinds:
                    continue
                self._active_kinds.add(kind)
                state = self._states[kind]
                state.last_attempt_at = observed_at
                state.next_due_at = self._compute_next_due_at(
                    kind,
                    state,
                    observed_at,
                    polling_profile=profile,
                )
                watermark = state.latest_data_time
                task_epoch = self._scheduler_market_epoch
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
                futures.append((kind, self._executor.submit(task), task_epoch))
            except BaseException as exc:
                with self._lock:
                    self._active_kinds.discard(kind)
                self._record_failure(
                    kind,
                    observed_at,
                    exc,
                    market_epoch=task_epoch,
                )

        for kind, future, task_epoch in futures:
            try:
                outcome = future.result()
                if outcome.status is ComputationStatus.FAILED:
                    assert outcome.exception is not None
                    raise outcome.exception
                if outcome.status is not ComputationStatus.COMPLETED:
                    continue
                self._accept_result(kind, observed_at, outcome.value)
            except BaseException as exc:
                self._record_failure(
                    kind,
                    observed_at,
                    exc,
                    market_epoch=task_epoch,
                )
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

        result_epoch = result.market_epoch

        with self._lock:
            if self._retired:
                return
            if (
                result_epoch is not None
                and self._scheduler_market_epoch is not None
                and result_epoch != self._scheduler_market_epoch
            ):
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
        seq = result.projection_seq
        if seq is not None and (
            isinstance(seq, bool) or not isinstance(seq, int) or seq < 1
        ):
            raise LiveRefreshValidationError(
                "refresh projection_seq must be a positive int"
            )

        with self._lock:
            if self._retired:
                return
            if seq is None:
                self._pending_unsequenced.append((kind, observed_at, result))
            elif seq < self._next_projection_seq:
                # Superseded by a later lock-order snapshot.  Keep the branch
                # watermark so this feed does not refetch the same rows.
                self._store_watermark(kind, observed_at, result)
            else:
                self._pending_by_seq[seq] = (kind, observed_at, result)

        # Claim-and-publish under one publisher so concurrent acceptors cannot
        # invert complete projection batches after advancing the seq cursor.
        self._drain_publish()

    def _drain_publish(self) -> None:
        """Publish pending results in projection_seq order under one publisher.

        Advancing ``_next_projection_seq`` and emitting that result's full
        update batch both happen while holding ``_publish_lock``.  Claiming the
        next seq under the state lock alone (then racing for publish rights)
        would let seq=2 publish completely before seq=1.  ``on_update`` still
        runs without the state lock to avoid re-entrant lock hazards.
        """

        with self._publish_lock:
            while True:
                with self._lock:
                    if self._retired:
                        return
                    next_seq = self._next_projection_seq
                    if next_seq in self._pending_by_seq:
                        kind, observed_at, result = self._pending_by_seq.pop(next_seq)
                        self._next_projection_seq = next_seq + 1
                    elif self._pending_unsequenced:
                        kind, observed_at, result = self._pending_unsequenced.pop(0)
                    else:
                        break
                if not self._emit_result(kind, observed_at, result):
                    return

    def _emit_result(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        result: LiveRefreshResult,
    ) -> bool:
        # Caller holds ``_publish_lock``.  Do not take the state lock around
        # ``on_update`` — callbacks may re-enter scheduler APIs.
        with self._lock:
            if self._retired:
                return False
        for update in result.updates:
            self._on_update(update)
        delayed_changed: bool | None = None
        with self._lock:
            if self._retired:
                return False
            delayed_changed = self._store_watermark(kind, observed_at, result)
        if (
            delayed_changed is not None
            and self._on_thirty_minute_delayed is not None
        ):
            self._on_thirty_minute_delayed(delayed_changed)
        return True

    def _store_watermark(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        result: LiveRefreshResult,
    ) -> bool | None:
        state = self._states[kind]
        if result.data_time is not None:
            state.latest_data_time = result.data_time
        state.last_success_at = observed_at
        state.last_failure = None
        state.consecutive_failures = 0
        if kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
            return self._advance_thirty_minute_schedule(
                state,
                observed_at,
                data_received=result.data_time is not None,
            )
        return None

    def _compute_next_due_at(
        self,
        kind: LiveRefreshKind,
        state: _MutableBranchState,
        observed_at: datetime,
        *,
        polling_profile: PollingProfile,
    ) -> datetime:
        """Compute the next due time for a branch.

        For fixed-interval branches (quote, 1m, 5m) this is simply
        ``observed_at + interval``.  For 30m, if a boundary provider is
        available, the next due time is the next 30m boundary + 5s (design
        §10).  If no provider is configured, 30m falls back to fixed-interval
        scheduling like the other branches.
        """

        if (
            kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE
            and self._thirty_minute_boundary_provider is not None
        ):
            next_boundary = self._thirty_minute_boundary_provider(observed_at)
            if next_boundary is not None:
                if state.thirty_minute_pending_boundary is None:
                    state.thirty_minute_pending_boundary = next_boundary
                return next_boundary + timedelta(seconds=5)
        return observed_at + self._intervals.for_kind(
            kind,
            polling_profile=polling_profile,
        )

    def _advance_thirty_minute_schedule(
        self,
        state: _MutableBranchState,
        observed_at: datetime,
        *,
        data_received: bool,
    ) -> bool | None:
        """Advance the 30m boundary-triggered schedule after a refresh result.

        Returns ``True`` when the branch newly enters the delayed state,
        ``False`` when it leaves that state, and ``None`` when the delayed
        flag does not change.

        When official data is received, the watermark advances to the new
        boundary and the next due time is the *next* 30-minute boundary + 5s.
        When no data is returned (the official bar is not yet available), the
        branch retries after the retry interval (15s active, 60s reduced).  If
        2 minutes have elapsed since the boundary's first attempt without
        receiving data, the branch enters the delayed state and switches to
        the 60s reduced interval.
        """

        if self._thirty_minute_boundary_provider is None:
            return None
        was_delayed = state.thirty_minute_delayed
        if data_received:
            state.thirty_minute_pending_boundary = None
            state.boundary_first_attempt_at = None
            state.thirty_minute_delayed = False
            next_boundary = self._thirty_minute_boundary_provider(observed_at)
            if next_boundary is not None:
                state.thirty_minute_pending_boundary = next_boundary
                state.next_due_at = next_boundary + timedelta(seconds=5)
            if was_delayed and not state.thirty_minute_delayed:
                return False
            return None
        # No data — the official bar is not yet available.  Track the boundary
        # we are waiting for and when we first tried.
        if state.thirty_minute_pending_boundary is None:
            boundary = self._thirty_minute_boundary_provider(observed_at)
            if boundary is not None:
                state.thirty_minute_pending_boundary = boundary
        if (
            state.boundary_first_attempt_at is None
            and state.thirty_minute_pending_boundary is not None
            and observed_at
            >= state.thirty_minute_pending_boundary + timedelta(seconds=5)
        ):
            state.boundary_first_attempt_at = observed_at
        if state.boundary_first_attempt_at is None:
            return None
        # Check the 2-minute delay threshold.
        if (
            state.boundary_first_attempt_at is not None
            and observed_at
            >= state.boundary_first_attempt_at + timedelta(minutes=2)
        ):
            state.thirty_minute_delayed = True
            state.next_due_at = observed_at + self._intervals.reduced_official_thirty_minute
        else:
            state.next_due_at = observed_at + self._intervals.official_thirty_minute
        if state.thirty_minute_delayed and not was_delayed:
            return True
        if was_delayed and not state.thirty_minute_delayed:
            return False
        return None

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
            LiveRefreshKind.QUOTE: {"market_update", "live_market_view_updated"},
            LiveRefreshKind.ONE_MINUTE: {
                "market_update",
                "indicators_updated",
                "live_market_view_updated",
            },
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE: {
                "market_update",
                "indicators_updated",
                "chan_analysis_replaced",
                "live_market_view_updated",
            },
            LiveRefreshKind.OFFICIAL_THIRTY_MINUTE: {
                "market_update",
                "indicators_updated",
                "chan_analysis_30m_replaced",
                "live_market_view_updated",
            },
        }[kind]
        if update.event_type not in allowed:
            raise LiveRefreshValidationError(
                f"{kind.value} cannot publish {update.event_type}"
            )

    def _record_failure(
        self,
        kind: LiveRefreshKind,
        observed_at: datetime,
        failure: BaseException,
        *,
        market_epoch: int | None = None,
    ) -> None:
        """Record a failed branch attempt and schedule the next retry.

        A failed OFFICIAL_THIRTY_MINUTE attempt is treated as a no-data
        attempt: the boundary wait state advances exactly as it does when a
        refresh succeeds without official data (15s retries, 60s once the
        2-minute delay threshold trips).  Generic exponential backoff would
        silently stretch the retry past the boundary window and never raise
        the delayed-official warning, so it is only used when the boundary
        schedule cannot determine a wait (no pending boundary, e.g. outside
        the trading session).
        """

        publish_failure = False
        delayed_changed: bool | None = None
        with self._lock:
            if self._retired:
                return
            if (
                market_epoch is not None
                and self._scheduler_market_epoch is not None
                and market_epoch != self._scheduler_market_epoch
            ):
                return
            state = self._states[kind]
            state.last_failure = failure
            state.consecutive_failures += 1
            base_interval = self._intervals.for_kind(
                kind,
                polling_profile=self._polling_profile,
            )
            if (
                kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE
                and self._thirty_minute_boundary_provider is not None
            ):
                delayed_changed = self._advance_thirty_minute_schedule(
                    state,
                    observed_at,
                    data_received=False,
                )
                if state.boundary_first_attempt_at is None:
                    # The boundary schedule could not determine a wait (no
                    # pending boundary, or still before the boundary window);
                    # fall back to the generic backoff so the branch still
                    # retries.
                    state.next_due_at = observed_at + self._backoff.delay(
                        base_interval,
                        state.consecutive_failures,
                    )
            else:
                state.next_due_at = observed_at + self._backoff.delay(
                    base_interval,
                    state.consecutive_failures,
                )
            publish_failure = True
        if publish_failure and self._on_failure is not None:
            try:
                self._on_failure(kind, failure, market_epoch)
            except Exception:
                logger.exception(
                    "live refresh failure callback raised",
                    extra={
                        "refresh_kind": kind.value,
                        "session_id": self._spec.session_id,
                        "session_generation": self._spec.generation,
                        "original_failure_type": type(failure).__name__,
                    },
                )
        if delayed_changed is not None and self._on_thirty_minute_delayed is not None:
            self._on_thirty_minute_delayed(delayed_changed)

    def _read_input_market_epoch(self) -> int | None:
        return _read_input_int_attr(self._input_port, "market_epoch")

    def _read_input_projection_seq(self) -> int | None:
        return _read_input_int_attr(self._input_port, "projection_seq")

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
            consecutive_failures=state.consecutive_failures,
            in_flight=kind in self._active_kinds,
            thirty_minute_delayed=state.thirty_minute_delayed,
        )


def _coerce_kind(kind: LiveRefreshKind | str) -> LiveRefreshKind:
    try:
        return kind if isinstance(kind, LiveRefreshKind) else LiveRefreshKind(kind)
    except ValueError as exc:
        raise LiveRefreshValidationError(f"unknown refresh kind: {kind!r}") from exc


def _read_input_int_attr(input_port: object, name: str) -> int | None:
    getter = getattr(input_port, name, None)
    if getter is None:
        return None
    if callable(getter):
        return getter()
    return getter


def _next_projection_seq(current: int | None) -> int:
    if current is None or isinstance(current, bool) or not isinstance(current, int):
        return 1
    return max(current, 0) + 1


__all__ = [
    "LiveRefreshBranchState",
    "LiveRefreshBackoff",
    "LiveRefreshError",
    "LiveRefreshInputPort",
    "LiveRefreshIntervals",
    "LiveRefreshKind",
    "LiveRefreshResult",
    "LiveRefreshScheduler",
    "LiveRefreshValidationError",
]
