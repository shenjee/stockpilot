"""Live Session initial-load orchestration.

T0-023 requires Live startup to behave atomically:

* request at least 500 closed 5m preheat bars,
* finish all data preparation and computation before publishing anything
  user-visible, and
* never publish a partial workbench when the initial load fails or the Session
  retires while the load is still in flight.

This module keeps that responsibility transport-free.  It produces a complete
snapshot *candidate* that T0-026 can later accept, revision, and publish
through the backend event boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from threading import Condition, Event, RLock, Thread, current_thread
from typing import Callable, Protocol

from packages.marketdata.services.market_context_service import MarketSession

from .coordinator import SessionSpec, SessionType
from .live_market_view import (
    MINIMUM_PREHEAT_5M,
    MarketClosedReason,
    PollingProfile,
    SymbolAvailability,
    build_live_market_view,
    resolve_initial_polling_profile,
    resolve_market_closed_reason,
)
from .pipeline import CzscAnalyzerPort, MarketInputPort, PipelineResult, WorkbenchPipeline
from .workbench_projection import WorkbenchProjection, build_workbench_projection
from .workbench_projection import SessionProjectionInput


class LiveSessionError(RuntimeError):
    """Base class for Live Session startup failures."""


class LiveSessionValidationError(LiveSessionError, ValueError):
    """The supplied Session spec or warmup result is invalid."""


@dataclass(frozen=True, slots=True)
class PreparedLiveWarmup:
    """Network-free input prepared for the first Live computation."""

    market_session: MarketSession
    target_time: datetime
    market_input_port: MarketInputPort
    observed_now: datetime
    market_candidate_trade_date: date
    calendar_status: str = "available"
    market_phase: str = "closed"
    symbol_availability: SymbolAvailability | None = None


class LiveInitialInputPort(Protocol):
    """Prepare the first Live computation input.

    The implementation owns any backward backfill strategy needed to satisfy
    the requested 5m preheat depth.  The Live Session only states the minimum
    requirement and never publishes a partial result while preparation is still
    in progress.
    """

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
        target_trade_date: date | None = None,
    ) -> PreparedLiveWarmup:
        """Return the prepared warmup input for the first full computation.

        When ``target_trade_date`` is set (day-switch mode) the implementation
        must force the effective session to that calendar trading day and treat
        all market-data loading as best-effort: a suspended security, an empty
        quote, or a failed provider request must not prevent the session from
        being pinned to ``target_trade_date``.
        """


@dataclass(frozen=True, slots=True)
class LiveSnapshotCandidate:
    """Complete Live workbench snapshot candidate produced by T0-023."""

    session_id: str
    generation: int
    symbol: str
    pipeline_result: PipelineResult
    state: str = "ready"
    calendar_status: str = "available"
    market_phase: str = "closed"
    market_epoch: int = 0
    polling_profile: PollingProfile = "active"
    market_candidate_trade_date: date | None = None
    symbol_availability: SymbolAvailability | None = None
    market_closed_reason: MarketClosedReason | None = None

    def build_projection(self, revision: int) -> WorkbenchProjection:
        """Build a full workbench snapshot once a revision is assigned."""

        result_trade_date = self.pipeline_result.trade_date
        trade_date = (
            result_trade_date.isoformat()
            if isinstance(result_trade_date, date)
            else result_trade_date
        )
        session = SessionProjectionInput(
            session_id=self.session_id,
            session_type="live",
            symbol=self.symbol,
            trade_date=trade_date,
            state=self.state,
            revision=revision,
        )
        preview = self.pipeline_result.to_dict()
        live_market_view = build_live_market_view(
            effective_trade_date=trade_date,
            market_candidate_trade_date=self.market_candidate_trade_date,
            calendar_status=self.calendar_status,  # type: ignore[arg-type]
            market_phase=self.market_phase,  # type: ignore[arg-type]
            polling_profile=self.polling_profile,
            market={
                "bars_1m": preview["bars_1m"],
                "bars_5m": preview["bars_5m"],
                "daily_bars": preview["daily_bars"],
                "quote": preview["quote"],
            },
            indicators={
                "one_minute": preview["indicators_1m"],
                "five_minute": preview["indicators_5m"],
            },
            chan_analysis=preview["chan_analysis"],
            closed_5m_prefix=self.pipeline_result.closed_5m_prefix,
            closed_5m_prefix_count=len(self.pipeline_result.closed_5m_prefix),
            target_time=self.pipeline_result.target_time,
            market_session=MarketSession(
                market=str(self.symbol).split(".", 1)[0],
                trade_date=(
                    self.pipeline_result.trade_date
                    if isinstance(self.pipeline_result.trade_date, date)
                    else date.fromisoformat(str(self.pipeline_result.trade_date))
                ),
            ),
            symbol_availability=self.symbol_availability,
            market_closed_reason=self.market_closed_reason,
            minimum_preheat_5m=MINIMUM_PREHEAT_5M,
        )
        return build_workbench_projection(
            self.pipeline_result,
            session,
            live_market_view=live_market_view,
        )


LiveSnapshotCandidateHandler = Callable[[LiveSnapshotCandidate], None]
LiveStateHandler = Callable[[str, str], None]


class LiveSession:
    """Concrete Live Session that performs the initial full-load workflow."""

    MINIMUM_PREHEAT_5M = 500

    def __init__(
        self,
        spec: SessionSpec,
        initial_input_port: LiveInitialInputPort,
        *,
        on_snapshot_candidate: LiveSnapshotCandidateHandler,
        on_state_change: LiveStateHandler | None = None,
        analyzer: CzscAnalyzerPort | None = None,
        auto_start: bool = True,
    ) -> None:
        if not isinstance(spec, SessionSpec):
            raise TypeError("spec must be a SessionSpec")
        if spec.session_type is not SessionType.LIVE:
            raise LiveSessionValidationError("LiveSession requires a live SessionSpec")
        if spec.trade_date is not None:
            raise LiveSessionValidationError("live SessionSpec trade_date must be null")
        if not callable(on_snapshot_candidate):
            raise TypeError("on_snapshot_candidate must be callable")
        if on_state_change is not None and not callable(on_state_change):
            raise TypeError("on_state_change must be callable")

        self._spec = spec
        self._initial_input_port = initial_input_port
        self._on_snapshot_candidate = on_snapshot_candidate
        self._on_state_change = on_state_change
        self._analyzer = analyzer
        self._lock = RLock()
        self._publish_lock = RLock()
        self._publish_cond = Condition(self._publish_lock)
        self._publishing_candidates = 0
        self._retired = Event()
        self._completed = Event()
        self._state = "created"
        self._failure: BaseException | None = None
        self._last_candidate: LiveSnapshotCandidate | None = None
        self._started = False
        self._thread = Thread(
            target=self._run_initial_load,
            name=f"stockpilot-live-load-{spec.session_id}",
            daemon=True,
        )
        self._emit_state("created", "session_created")
        if auto_start:
            self.activate()

    @property
    def spec(self) -> SessionSpec:
        return self._spec

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            return self._failure

    @property
    def last_candidate(self) -> LiveSnapshotCandidate | None:
        with self._lock:
            return self._last_candidate

    def wait_for_completion(self, timeout: float | None = None) -> bool:
        """Wait until the initial load reaches a terminal state."""

        return self._completed.wait(timeout=timeout)

    def activate(self) -> None:
        """Start the initial load once the Session becomes active."""

        with self._lock:
            if self._started or self._retired.is_set():
                return
            self._started = True
        self._thread.start()

    def retire(self) -> None:
        """Cancel publication of any in-flight initial-load result."""

        with self._publish_lock:
            self._retired.set()
            if current_thread() is not self._thread:
                while self._publishing_candidates:
                    self._publish_cond.wait()
        try:
            self._emit_state("retired", "session_retired")
        finally:
            self._completed.set()

    def _run_initial_load(self) -> None:
        try:
            if self._retired.is_set():
                return
            self._emit_state("loading", "load_started")
            try:
                prepared = self._initial_input_port.prepare(
                    self._spec,
                    minimum_preheat_5m=self.MINIMUM_PREHEAT_5M,
                )
                self._validate_prepared(prepared)
                if self._retired.is_set():
                    return

                pipeline = WorkbenchPipeline(
                    session=prepared.market_session,
                    market_input_port=prepared.market_input_port,
                    analyzer=self._analyzer,
                )
                result = pipeline.preview(prepared.target_time)
                candidate = LiveSnapshotCandidate(
                    session_id=self._spec.session_id,
                    generation=self._spec.generation,
                    symbol=self._spec.symbol,
                    pipeline_result=result,
                    calendar_status=prepared.calendar_status,
                    market_phase=prepared.market_phase,
                    market_candidate_trade_date=prepared.market_candidate_trade_date,
                    symbol_availability=prepared.symbol_availability,
                    polling_profile=resolve_initial_polling_profile(
                        market_phase=prepared.market_phase,  # type: ignore[arg-type]
                        calendar_status=prepared.calendar_status,  # type: ignore[arg-type]
                        pinned_trade_date=prepared.market_session.trade_date,
                        market_candidate_trade_date=prepared.market_candidate_trade_date,
                        observed_now=prepared.observed_now,
                    ),
                    market_closed_reason=resolve_market_closed_reason(
                        observed_now=prepared.observed_now,
                        market_phase=prepared.market_phase,  # type: ignore[arg-type]
                        calendar_status=prepared.calendar_status,  # type: ignore[arg-type]
                    ),
                )
                if self._retired.is_set():
                    return
                self._before_publish_candidate()
                if self._retired.is_set():
                    return
                if not self._begin_publish_candidate():
                    return
                try:
                    self._on_snapshot_candidate(candidate)
                finally:
                    self._end_publish_candidate()
                if self._retired.is_set():
                    return
                with self._lock:
                    self._last_candidate = candidate
                self._emit_state("ready", "load_completed")
            except BaseException as exc:
                if self._retired.is_set():
                    return
                with self._lock:
                    self._failure = exc
                self._emit_state("failed", "operation_failed")
        finally:
            self._completed.set()

    def _before_publish_candidate(self) -> None:
        return

    def _emit_state(self, state: str, reason: str) -> None:
        callback = self._on_state_change
        with self._lock:
            if self._state == "retired" and state != "retired":
                return
            self._state = state
        if callback is not None:
            try:
                callback(state, reason)
            except Exception:
                return

    def _begin_publish_candidate(self) -> bool:
        with self._publish_lock:
            if self._retired.is_set():
                return False
            self._publishing_candidates += 1
            return True

    def _end_publish_candidate(self) -> None:
        with self._publish_lock:
            if self._publishing_candidates:
                self._publishing_candidates -= 1
            if not self._publishing_candidates:
                self._publish_cond.notify_all()

    def _validate_prepared(self, prepared: PreparedLiveWarmup) -> None:
        if not isinstance(prepared, PreparedLiveWarmup):
            raise LiveSessionValidationError(
                "initial_input_port.prepare must return PreparedLiveWarmup"
            )
        if prepared.market_session.trade_date != prepared.target_time.date():
            raise LiveSessionValidationError(
                "prepared target_time must belong to prepared market_session.trade_date"
            )
        if prepared.target_time.tzinfo is not None:
            raise LiveSessionValidationError(
                "prepared target_time must be a naive Asia/Shanghai timestamp"
            )
        if prepared.observed_now.tzinfo is not None:
            raise LiveSessionValidationError(
                "prepared observed_now must be a naive Asia/Shanghai timestamp"
            )
