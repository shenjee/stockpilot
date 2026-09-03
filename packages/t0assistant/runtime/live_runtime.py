"""Production composition for a Live initial load and independent refreshes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from threading import Event, RLock, Thread, current_thread
from typing import Callable, Mapping, Protocol, Sequence

from packages.marketdata.calendar_query import CalendarQueryPort
from packages.marketdata.services.market_context_service import (
    MarketContextError,
    MarketSession,
)

from .computation_executor import BoundedComputationExecutor
from .coordinator import SessionSpec
from .live_projection_store import LiveIncrementalUpdate
from .live_refresh import (
    LiveRefreshBranchState,
    LiveRefreshInputPort,
    LiveRefreshIntervals,
    LiveRefreshKind,
    LiveRefreshResult,
    LiveRefreshScheduler,
)
from .live_market_view import (
    CloseReconcileStatus,
    LiveMarketViewError,
    MarketClosedReason,
    PollingProfile,
    day_switch_target_date,
    is_awaiting_day_switch,
    resolve_live_market_context,
    resolve_market_closed_reason,
    resolve_polling_profile,
    should_run_close_reconciliation,
)
from .live_session import (
    LiveInitialInputPort,
    LiveSession,
    LiveSnapshotCandidate,
    PreparedLiveWarmup,
)
from .pipeline import (
    CzscAnalyzerPort,
    MarketInputPort,
    PipelineMarketInput,
    PipelineResult,
    WorkbenchPipeline,
)


_CLOSE_RECONCILE_MAX_ATTEMPTS = 5
_CLOSE_RECONCILE_RETRY_INTERVAL = timedelta(seconds=30)


class LiveBranchDataPort(LiveInitialInputPort, Protocol):
    """Initial input plus narrow normalized reads for each refresh branch."""

    def load_refresh_bars(
        self,
        spec: SessionSpec,
        *,
        timeframe: str,
        trade_date: date | str,
    ) -> Sequence[Mapping[str, object]]: ...

    def load_refresh_quotes(
        self,
        spec: SessionSpec,
        *,
        trade_date: date | str,
    ) -> Sequence[Mapping[str, object]]: ...


class BranchingLiveInput(LiveInitialInputPort, LiveRefreshInputPort):
    """Cache initial normalized input and refresh exactly one source branch.

    Provider reads happen outside the state lock, so one slow or failed branch
    cannot prevent the scheduler's other workers from reading their sources.
    The short locked section only merges normalized rows and rebuilds the
    shared workbench projection from that coherent prefix.  Each successful
    rebuild stamps a monotonic ``projection_seq`` so the refresh scheduler can
    publish ``bars_5m`` updates in lock generation order even when futures are
    collected in kind order.

    ``market_phase`` advances on a dedicated, lock-serialized path that runs
    before provider I/O and publishes at most one full snapshot per transition.
    That keeps Calendar phase orthogonal to quote/1m/5m success and prevents
    concurrent workers from republishing inverted full snapshots.

    PR-B adds 09:30 atomic day switching with an internal ``market_epoch``,
    phase-aware polling, and one-shot post-close reconciliation.
    """

    def __init__(
        self,
        source: LiveBranchDataPort,
        *,
        analyzer: CzscAnalyzerPort | None = None,
        calendar: CalendarQueryPort | None = None,
        on_projection_refresh: Callable[[LiveSnapshotCandidate], None] | None = None,
        on_day_switched: Callable[[LiveSnapshotCandidate, int], None] | None = None,
    ) -> None:
        self._source = source
        self._analyzer = analyzer
        self._calendar = calendar
        self._on_projection_refresh = on_projection_refresh
        self._on_day_switched = on_day_switched
        self._lock = RLock()
        self._session = None
        self._market_input: PipelineMarketInput | None = None
        self._calendar_status = "available"
        self._market_phase = "closed"
        self._market: str | None = None
        self._market_epoch = 0
        self._projection_seq = 0
        self._day_switch_in_progress = False
        self._close_reconcile_status: CloseReconcileStatus = "not_started"
        self._close_reconcile_attempts = 0
        self._close_reconcile_next_retry_at: datetime | None = None

    def set_on_projection_refresh(
        self,
        handler: Callable[[LiveSnapshotCandidate], None] | None,
    ) -> None:
        """Publish full snapshots when pinned-day ``market_phase`` advances."""

        self._on_projection_refresh = handler

    def set_on_day_switched(
        self,
        handler: Callable[[LiveSnapshotCandidate, int], None] | None,
    ) -> None:
        """Notify the runtime when an atomic day switch completes."""

        self._on_day_switched = handler

    @property
    def market_epoch(self) -> int:
        with self._lock:
            return self._market_epoch

    @property
    def projection_seq(self) -> int:
        with self._lock:
            return self._projection_seq

    @property
    def close_reconcile_status(self) -> CloseReconcileStatus:
        with self._lock:
            return self._close_reconcile_status

    @property
    def market_session(self) -> MarketSession | None:
        with self._lock:
            return self._session

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
        target_trade_date: date | None = None,
    ) -> PreparedLiveWarmup:
        prepared = self._source.prepare(
            spec,
            minimum_preheat_5m=minimum_preheat_5m,
            target_trade_date=target_trade_date,
        )
        market_input = prepared.market_input_port.read(prepared.target_time)
        _, _, market = _parse_market_from_symbol(spec.symbol)
        with self._lock:
            self._session = prepared.market_session
            self._market_input = market_input
            self._calendar_status = prepared.calendar_status
            self._market_phase = prepared.market_phase
            self._market = market
            self._market_epoch = 0
            self._projection_seq = 0
            self._day_switch_in_progress = False
            self._close_reconcile_status = "not_started"
            self._close_reconcile_attempts = 0
            self._close_reconcile_next_retry_at = None
        return prepared

    def advance_view_status(
        self,
        spec: SessionSpec,
        observed_at: datetime,
    ) -> None:
        """Advance pinned-day phase without provider I/O."""

        self._publish_phase_if_advanced(spec, observed_at)

    def polling_profile(self, observed_at: datetime) -> PollingProfile:
        with self._lock:
            if self._session is None or self._market is None:
                return "idle"
            session = self._session
            calendar_status = self._calendar_status
            market_phase = self._market_phase
            market = self._market
            close_reconcile_status = self._close_reconcile_status
            close_reconcile_next_retry_at = self._close_reconcile_next_retry_at
            calendar = self._calendar
        awaiting = (
            calendar is not None
            and is_awaiting_day_switch(
                calendar,
                observed_at=observed_at,
                pinned_trade_date=session.trade_date,
                market=market,
                calendar_status=calendar_status,
            )
        )
        return resolve_polling_profile(
            market_phase=market_phase,
            calendar_status=calendar_status,
            pinned_trade_date=session.trade_date,
            observed_at=observed_at,
            calendar=calendar,
            market=market,
            awaiting_day_switch=awaiting,
            close_reconcile_status=close_reconcile_status,
            close_reconcile_retry_due=(
                close_reconcile_next_retry_at is None
                or observed_at >= close_reconcile_next_retry_at
            ),
        )

    def maybe_reconcile_close(
        self,
        spec: SessionSpec,
        observed_at: datetime,
    ) -> bool:
        """Return whether close reconciliation should run at ``observed_at``."""

        with self._lock:
            if self._session is None:
                return False
            status = self._close_reconcile_status
            if not should_run_close_reconciliation(
                market_phase=self._market_phase,
                observed_at=observed_at,
                close_reconcile_status=status,
            ):
                return False
            if status == "in_progress":
                return False
            if status == "retry_pending":
                if (
                    self._close_reconcile_next_retry_at is not None
                    and observed_at < self._close_reconcile_next_retry_at
                ):
                    return False
            if status in {"not_started", "retry_pending"}:
                self._close_reconcile_status = "in_progress"
            return True

    def finish_close_reconciliation(
        self,
        branch_states: Mapping[LiveRefreshKind, LiveRefreshBranchState],
        observed_at: datetime,
    ) -> None:
        """Finalize or reschedule close reconciliation after one pass."""

        with self._lock:
            if self._close_reconcile_status != "in_progress":
                return
            all_succeeded = all(
                state.last_success_at == observed_at and state.last_failure is None
                for state in branch_states.values()
            )
            if all_succeeded:
                self._close_reconcile_status = "completed"
                self._close_reconcile_next_retry_at = None
                return
            self._close_reconcile_attempts += 1
            if self._close_reconcile_attempts >= _CLOSE_RECONCILE_MAX_ATTEMPTS:
                self._close_reconcile_status = "exhausted"
                self._close_reconcile_next_retry_at = None
                return
            self._close_reconcile_status = "retry_pending"
            self._close_reconcile_next_retry_at = (
                observed_at + _CLOSE_RECONCILE_RETRY_INTERVAL
            )

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        epoch_at_start = self.market_epoch
        # Phase is Calendar/wall-clock state: advance it even when every market
        # branch later fails, and claim the transition under the lock so only
        # one worker publishes the full snapshot for that transition.
        self._publish_phase_if_advanced(spec, observed_at)

        with self._lock:
            if self._market_input is None or self._session is None:
                raise RuntimeError("Live refresh cannot run before initial prepare")
            trade_date = self._session.trade_date
            market = self._market
            calendar = self._calendar

        target_date = None
        if calendar is not None and market is not None:
            target_date = day_switch_target_date(
                calendar,
                observed_at=observed_at,
                pinned_trade_date=trade_date,
                market=market,
            )
        if target_date is not None:
            if self._maybe_switch_day(
                spec,
                observed_at=observed_at,
                epoch_at_start=epoch_at_start,
            ):
                return LiveRefreshResult.no_change(market_epoch=epoch_at_start)
            if epoch_at_start != self.market_epoch:
                return LiveRefreshResult.no_change(market_epoch=epoch_at_start)

        if kind is LiveRefreshKind.QUOTE:
            rows = tuple(
                self._source.load_refresh_quotes(spec, trade_date=trade_date)
            )
        elif kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
            rows = tuple(
                self._source.load_refresh_bars(
                    spec,
                    timeframe="30m",
                    trade_date=trade_date,
                )
            )
        else:
            rows = tuple(
                self._source.load_refresh_bars(
                    spec,
                    timeframe=(
                        "1m"
                        if kind is LiveRefreshKind.ONE_MINUTE
                        else "5m"
                    ),
                    trade_date=trade_date,
                )
            )
        if epoch_at_start != self.market_epoch:
            return LiveRefreshResult.no_change(market_epoch=epoch_at_start)

        data_time = _latest_row_time(
            rows,
            closed_only=kind
            in (
                LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
                LiveRefreshKind.OFFICIAL_THIRTY_MINUTE,
            ),
        )
        data_changed = not (
            data_time is None
            or latest_data_time is not None
            and data_time <= latest_data_time
        )
        if not data_changed:
            return LiveRefreshResult.no_change(market_epoch=epoch_at_start)

        with self._lock:
            if self._market_input is None or self._session is None:
                raise RuntimeError("Live refresh cannot run before initial prepare")
            if self._market_epoch != epoch_at_start:
                return LiveRefreshResult.no_change(market_epoch=epoch_at_start)
            if self._session.trade_date != trade_date:
                # Prepared day changed under us; drop this branch read.
                return LiveRefreshResult.no_change(market_epoch=epoch_at_start)
            if kind is LiveRefreshKind.QUOTE:
                updated_input = replace(self._market_input, quote_snapshots=rows)
            elif kind is LiveRefreshKind.ONE_MINUTE:
                updated_input = replace(self._market_input, bars_1m=rows)
            elif kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
                updated_input = replace(self._market_input, official_30m_bars=rows)
            else:
                updated_input = replace(self._market_input, official_5m_bars=rows)
            # This is the intentional cross-branch consistency boundary.
            # Provider I/O remains outside the lock and can fail independently;
            # merging the cached prefix and rebuilding the projection are
            # serialized so two successful branches cannot publish projections
            # from torn combinations of cached inputs.
            # Preview must stay on the prepared effective session day.
            preview_at = observed_at
            if observed_at.date() != self._session.trade_date:
                preview_at = self._session.end
            result = WorkbenchPipeline(
                session=self._session,
                market_input_port=_FixedMarketInput(updated_input),
                analyzer=self._analyzer,
            ).preview(preview_at)
            self._market_input = updated_input
            calendar_status = self._calendar_status
            market_phase = self._market_phase
            committed_epoch = self._market_epoch
            candidate = LiveSnapshotCandidate(
                session_id=spec.session_id,
                generation=spec.generation,
                symbol=spec.symbol,
                pipeline_result=result,
                calendar_status=calendar_status,
                market_phase=market_phase,
                market_epoch=committed_epoch,
                market_candidate_trade_date=self._market_candidate_trade_date(
                    observed_at
                ),
                **_live_view_extras(
                    self,
                    observed_at=observed_at,
                    calendar_status=calendar_status,
                    market_phase=market_phase,
                ),
            )
            snapshot = candidate.build_projection(0).to_dict()
            seq = self._projection_seq + 1
            updates = _branch_updates(
                kind,
                spec,
                snapshot,
                market_epoch=epoch_at_start,
                projection_seq=seq,
            )
            self._projection_seq = seq
            return LiveRefreshResult(
                data_time=data_time,
                updates=updates,
                market_epoch=epoch_at_start,
                projection_seq=seq,
            )

    def _publish_phase_if_advanced(
        self,
        spec: SessionSpec,
        observed_at: datetime,
    ) -> None:
        """Commit and publish at most one view-status transition before I/O.

        Atomically updates ``market_phase`` and ``calendar_status`` together.
        The handler runs while the state lock is held so publish order matches
        the cache commit that produced the candidate. Handlers must not re-enter
        :meth:`refresh`.
        """

        with self._lock:
            if self._market_input is None or self._session is None:
                raise RuntimeError("Live refresh cannot run before initial prepare")
            resolved = _resolve_pinned_live_view(
                self._session,
                observed_at=observed_at,
                calendar_status=self._calendar_status,
                calendar=self._calendar,
            )
            if (
                resolved.market_phase == self._market_phase
                and resolved.calendar_status == self._calendar_status
            ):
                return
            self._market_phase = resolved.market_phase
            self._calendar_status = resolved.calendar_status
            market_epoch = self._market_epoch
            preview_at = observed_at
            if observed_at.date() != self._session.trade_date:
                preview_at = self._session.end
            result = WorkbenchPipeline(
                session=self._session,
                market_input_port=_FixedMarketInput(self._market_input),
                analyzer=self._analyzer,
            ).preview(preview_at)
            candidate = LiveSnapshotCandidate(
                session_id=spec.session_id,
                generation=spec.generation,
                symbol=spec.symbol,
                pipeline_result=result,
                calendar_status=resolved.calendar_status,
                market_phase=resolved.market_phase,
                market_epoch=market_epoch,
                market_candidate_trade_date=self._market_candidate_trade_date(observed_at),
                **_live_view_extras(
                    self,
                    observed_at=observed_at,
                    calendar_status=resolved.calendar_status,
                    market_phase=resolved.market_phase,
                ),
            )
            handler = self._on_projection_refresh
            if handler is not None:
                # Bind full-snapshot publish to this commit so a slower worker
                # cannot later overwrite a newer coherent cache with an older
                # phase candidate built before sibling merges.
                handler(candidate)

    def _maybe_switch_day(
        self,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        epoch_at_start: int,
    ) -> bool:
        """Switch the pinned trade day on calendar + wall clock alone.

        The effective trade date is determined **only** by the calendar and the
        current wall-clock time (see :func:`day_switch_target_date`).  Market
        data is never used as evidence: a suspended security, a temporarily
        empty quote, or a failed provider request must not block the day
        switch.  After the switch commits, each refresh branch independently
        fetches quote / 1m / 5m data for the new day.
        """

        with self._lock:
            if (
                self._session is None
                or self._market is None
                or self._calendar is None
                or self._day_switch_in_progress
            ):
                return False
            pinned_trade_date = self._session.trade_date
            market = self._market
            calendar = self._calendar

        target_date = day_switch_target_date(
            calendar,
            observed_at=observed_at,
            pinned_trade_date=pinned_trade_date,
            market=market,
        )
        if target_date is None:
            return False

        with self._lock:
            if (
                self._day_switch_in_progress
                or self._market_epoch != epoch_at_start
                or self._session is None
                or self._session.trade_date != pinned_trade_date
            ):
                return False
            self._day_switch_in_progress = True

        try:
            prepared = self._source.prepare(
                spec,
                minimum_preheat_5m=LiveSession.MINIMUM_PREHEAT_5M,
                target_trade_date=target_date,
            )
        except BaseException:
            # Provider failure must not block the calendar-driven day switch.
            # Fall back to a calendar-only prepared warmup with the previous
            # day's preheat / daily context carried over (#133).
            try:
                prepared = self._build_calendar_only_prepared(
                    spec,
                    target_date=target_date,
                    observed_at=observed_at,
                    calendar=calendar,
                    market=market,
                )
            except BaseException:
                with self._lock:
                    self._day_switch_in_progress = False
                return False

        try:
            market_input = prepared.market_input_port.read(prepared.target_time)
            candidate = self._commit_day_switch(
                spec,
                prepared=prepared,
                market_input=market_input,
                epoch_at_start=epoch_at_start,
            )
        finally:
            with self._lock:
                self._day_switch_in_progress = False

        if candidate is None:
            return False
        switch_handler = self._on_day_switched
        if switch_handler is not None:
            switch_handler(candidate, self.market_epoch)
        else:
            refresh_handler = self._on_projection_refresh
            if refresh_handler is not None:
                refresh_handler(candidate)
        return True

    def _commit_day_switch(
        self,
        spec: SessionSpec,
        *,
        prepared: PreparedLiveWarmup,
        market_input: PipelineMarketInput,
        epoch_at_start: int,
    ) -> LiveSnapshotCandidate | None:
        """Commit the calendar-driven day switch, then best-effort project.

        The effective trade date, session, epoch, and all derived state are
        committed atomically from the calendar + wall clock **before** any
        projection computation.  This ensures that indicator, CZSC analyzer,
        or projection-building failures can never roll back the date update
        (#133).  When the projection fails, a degraded candidate with empty
        data and a warning is published; the session stays on the new trading
        day and subsequent refreshes use the new date.
        """

        # Step 1: Calendar + wall clock atomically commits the effective trade
        # date, session, epoch, and derived state.
        with self._lock:
            if (
                self._market_epoch != epoch_at_start
                or self._session is None
                or prepared.market_session.trade_date <= self._session.trade_date
            ):
                return None
            self._market_epoch += 1
            new_epoch = self._market_epoch
            self._session = prepared.market_session
            self._market_input = market_input
            self._calendar_status = prepared.calendar_status
            self._market_phase = prepared.market_phase
            self._close_reconcile_status = "not_started"
            self._close_reconcile_attempts = 0
            self._close_reconcile_next_retry_at = None

        # Step 2: Best-effort projection.  If the pipeline fails (indicator
        # calculation, CZSC analyzer, or projection building), publish a
        # degraded candidate so the workbench shows the new trading day with
        # empty data and a warning.
        preview_at = prepared.target_time
        try:
            result = WorkbenchPipeline(
                session=prepared.market_session,
                market_input_port=_FixedMarketInput(market_input),
                analyzer=self._analyzer,
            ).preview(preview_at)
        except BaseException:
            result = PipelineResult.degraded(
                session=prepared.market_session,
                symbol=market_input.symbol,
                target_time=preview_at,
            )

        candidate = LiveSnapshotCandidate(
            session_id=spec.session_id,
            generation=spec.generation,
            symbol=spec.symbol,
            pipeline_result=result,
            calendar_status=prepared.calendar_status,
            market_phase=prepared.market_phase,
            market_candidate_trade_date=prepared.market_candidate_trade_date,
            symbol_availability=prepared.symbol_availability,
            **_live_view_extras(
                self,
                observed_at=prepared.target_time,
                calendar_status=prepared.calendar_status,
                market_phase=prepared.market_phase,
                pinned_trade_date=prepared.market_session.trade_date,
                awaiting_day_switch=False,
            ),
        )

        return replace(candidate, market_epoch=new_epoch)

    def _build_calendar_only_prepared(
        self,
        spec: SessionSpec,
        *,
        target_date: date,
        observed_at: datetime,
        calendar: CalendarQueryPort,
        market: str,
    ) -> PreparedLiveWarmup:
        """Build a calendar-only ``PreparedLiveWarmup`` when prepare() fails.

        The session is forced to ``target_date`` from the calendar.  Intraday
        data is empty (``symbol_availability = no_current_data``) and preheat /
        daily history are carried over from the previous day's market input so
        the workbench projection can still be built (#133).
        """

        resolved = resolve_live_market_context(
            calendar,
            observed_now=observed_at,
            market=market,
        )
        session = resolved.market_session
        target_time = min(observed_at, session.end)
        with self._lock:
            previous_input = self._market_input
        if previous_input is not None:
            fallback_input = replace(
                previous_input,
                trade_date=target_date,
                bars_1m=(),
                official_5m_bars=(),
                quote_snapshots=(),
            )
        else:
            fallback_input = PipelineMarketInput(
                symbol=spec.symbol,
                trade_date=target_date,
            )
        return PreparedLiveWarmup(
            market_session=session,
            target_time=target_time,
            observed_now=observed_at,
            market_candidate_trade_date=target_date,
            market_input_port=_FixedMarketInput(fallback_input),
            calendar_status=resolved.calendar_status,
            market_phase=resolved.market_phase,
            symbol_availability="no_current_data",
        )

    def _market_candidate_trade_date(self, observed_at: datetime) -> date | None:
        with self._lock:
            calendar = self._calendar
            market = self._market
            session = self._session
        if calendar is None or market is None:
            return session.trade_date if session is not None else None
        try:
            return resolve_live_market_context(
                calendar,
                observed_now=observed_at,
                market=market,
            ).effective_trade_date
        except LiveMarketViewError:
            return session.trade_date if session is not None else None


class _FixedMarketInput(MarketInputPort):
    def __init__(self, value: PipelineMarketInput) -> None:
        self._value = value

    def read(self, target_time: datetime) -> PipelineMarketInput:
        return self._value


class LiveRuntimeSession:
    """Own one Live initial session and its production refresh scheduler."""

    def __init__(
        self,
        spec: SessionSpec,
        input_port: BranchingLiveInput,
        *,
        on_snapshot_candidate: Callable[[LiveSnapshotCandidate], None],
        on_incremental_update: Callable[[LiveIncrementalUpdate], object],
        on_refresh_failure: Callable[
            [LiveRefreshKind, BaseException, int | None], None
        ],
        on_state_change: Callable[[str, str], None] | None = None,
        on_thirty_minute_delayed: Callable[[bool], None] | None = None,
        analyzer: CzscAnalyzerPort | None = None,
        intervals: LiveRefreshIntervals = LiveRefreshIntervals(),
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 1.0,
        auto_poll: bool = True,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._spec = spec
        self._input_port = input_port
        self._external_candidate = on_snapshot_candidate
        self._on_incremental_update = on_incremental_update
        self._on_refresh_failure = on_refresh_failure
        self._on_thirty_minute_delayed = on_thirty_minute_delayed
        self._intervals = intervals
        self._clock = clock or datetime.now
        self._poll_interval_seconds = poll_interval_seconds
        self._auto_poll = auto_poll
        self._lock = RLock()
        self._retired = Event()
        self._scheduler: LiveRefreshScheduler | None = None
        self._executor: BoundedComputationExecutor | None = None
        self._poll_thread: Thread | None = None
        if isinstance(input_port, BranchingLiveInput):
            input_port.set_on_projection_refresh(on_snapshot_candidate)
            input_port.set_on_day_switched(self._on_day_switched)
        self._initial = LiveSession(
            spec,
            input_port,
            on_snapshot_candidate=self._initial_ready,
            on_state_change=on_state_change,
            analyzer=analyzer,
            auto_start=False,
        )

    @property
    def spec(self) -> SessionSpec:
        return self._spec

    @property
    def retired(self) -> bool:
        return self._retired.is_set()

    @property
    def refresh_scheduler(self) -> LiveRefreshScheduler | None:
        with self._lock:
            return self._scheduler

    def activate(self) -> None:
        self._initial.activate()

    def wait_for_completion(self, timeout: float | None = None) -> bool:
        return self._initial.wait_for_completion(timeout)

    @property
    def failure(self) -> BaseException | None:
        return self._initial.failure

    def run_refresh_due(self, observed_at: datetime | None = None) -> Mapping:
        scheduler = self.refresh_scheduler
        if scheduler is None:
            return {}
        now = observed_at or self._clock()
        if isinstance(self._input_port, BranchingLiveInput):
            self._input_port.advance_view_status(self._spec, now)
            profile = self._input_port.polling_profile(now)
            if self._input_port.maybe_reconcile_close(self._spec, now):
                states = scheduler.run_reconciliation(now)
                self._input_port.finish_close_reconciliation(states, now)
                return states
            scheduler.set_polling_profile(profile)
            return scheduler.run_due(now)
        return scheduler.run_due(now)

    def _on_day_switched(
        self,
        candidate: LiveSnapshotCandidate,
        market_epoch: int,
    ) -> None:
        scheduler = self.refresh_scheduler
        if scheduler is None:
            return
        scheduler.reset_branch_watermarks(
            _initial_data_times(candidate),
            market_epoch=market_epoch,
        )
        self._external_candidate(candidate)

    def retire(self) -> None:
        # Retirement is cooperative for already-running Python/provider work:
        # the scheduler rejects late results and the executor cancels queued
        # work, while an in-flight provider call completes under whatever
        # request-timeout/session-validator boundary its data port configured.
        # Python worker threads are never force-killed.
        self._retired.set()
        with self._lock:
            scheduler = self._scheduler
            executor = self._executor
            poll_thread = self._poll_thread
        if scheduler is not None:
            scheduler.retire()
        self._initial.retire()
        if (
            poll_thread is not None
            and poll_thread is not current_thread()
            and poll_thread.is_alive()
        ):
            poll_thread.join(timeout=max(1.0, self._poll_interval_seconds * 2))
        if executor is not None:
            executor.shutdown(cancel_pending=True, wait=True)

    def _initial_ready(self, candidate: LiveSnapshotCandidate) -> None:
        self._external_candidate(candidate)
        if self._retired.is_set():
            return
        executor = BoundedComputationExecutor(capacity=12, worker_count=3)
        scheduler = LiveRefreshScheduler(
            self._spec,
            self._input_port,
            executor,
            on_update=self._on_incremental_update,
            intervals=self._intervals,
            clock=self._clock,
            on_failure=self._on_refresh_failure,
            initial_data_times=_initial_data_times(candidate),
            thirty_minute_boundary_provider=_make_thirty_minute_boundary_provider(
                self._input_port
            ),
            on_thirty_minute_delayed=self._on_thirty_minute_delayed,
        )
        with self._lock:
            if self._retired.is_set():
                scheduler.retire()
                executor.shutdown(cancel_pending=True, wait=True)
                return
            self._executor = executor
            self._scheduler = scheduler
            if self._auto_poll:
                self._poll_thread = Thread(
                    target=self._poll,
                    name=f"stockpilot-live-refresh-{self._spec.session_id}",
                    daemon=True,
                )
                self._poll_thread.start()

    def _poll(self) -> None:
        while not self._retired.wait(self._poll_interval_seconds):
            self.run_refresh_due(self._clock())


@dataclass(frozen=True, slots=True)
class _PinnedLiveView:
    """Wall-clock view status for a Session still pinned to its prepare day."""

    market_phase: str
    calendar_status: str


def _market_closed_reason(
    observed_at: datetime,
    market_phase: str,
    calendar_status: str,
) -> MarketClosedReason | None:
    return resolve_market_closed_reason(
        observed_now=observed_at,
        market_phase=market_phase,  # type: ignore[arg-type]
        calendar_status=calendar_status,  # type: ignore[arg-type]
    )


def _live_view_extras(
    input_port: BranchingLiveInput,
    *,
    observed_at: datetime,
    calendar_status: str,
    market_phase: str,
    pinned_trade_date: date | None = None,
    awaiting_day_switch: bool | None = None,
) -> dict[str, PollingProfile | MarketClosedReason | None]:
    with input_port._lock:
        calendar = input_port._calendar
        market = input_port._market
        session = input_port._session
        close_reconcile_status = input_port._close_reconcile_status
        close_reconcile_next_retry_at = input_port._close_reconcile_next_retry_at
    effective_pinned = (
        pinned_trade_date
        if pinned_trade_date is not None
        else (session.trade_date if session is not None else observed_at.date())
    )
    effective_market = market or (
        session.market if session is not None else "sh"
    )
    if awaiting_day_switch is None:
        awaiting = (
            calendar is not None
            and session is not None
            and is_awaiting_day_switch(
                calendar,
                observed_at=observed_at,
                pinned_trade_date=session.trade_date,
                market=effective_market,
                calendar_status=calendar_status,  # type: ignore[arg-type]
            )
        )
    else:
        awaiting = awaiting_day_switch
    return {
        "polling_profile": resolve_polling_profile(
            market_phase=market_phase,  # type: ignore[arg-type]
            calendar_status=calendar_status,  # type: ignore[arg-type]
            pinned_trade_date=effective_pinned,
            observed_at=observed_at,
            calendar=calendar,
            market=effective_market,
            awaiting_day_switch=awaiting,
            close_reconcile_status=close_reconcile_status,
            close_reconcile_retry_due=(
                close_reconcile_next_retry_at is None
                or observed_at >= close_reconcile_next_retry_at
            ),
        ),
        "market_closed_reason": _market_closed_reason(
            observed_at,
            market_phase,
            calendar_status,
        ),
    }


def _resolve_pinned_live_view(
    session: MarketSession,
    *,
    observed_at: datetime,
    calendar_status: str,
    calendar: CalendarQueryPort | None = None,
) -> _PinnedLiveView:
    """Resolve pinned-day ``market_phase`` and ``calendar_status`` together.

    Trade-date switching is handled by PR-B ``_maybe_switch_day``. Cross-day
    classification consults the calendar: confirmed closed days stay
    ``market_closed`` with ``calendar_status=available``; out-of-coverage
    dates (missing year JSON) degrade to ``unavailable`` + ``unknown``.
    Without open evidence, never claim ``pre_open``.
    """

    if calendar_status == "unavailable":
        return _PinnedLiveView(market_phase="unknown", calendar_status="unavailable")
    observed_date = observed_at.date()
    if observed_date == session.trade_date:
        return _PinnedLiveView(
            market_phase=session.phase_at(observed_at),
            calendar_status="available",
        )
    if observed_date < session.trade_date:
        return _PinnedLiveView(market_phase="pre_open", calendar_status="available")
    if calendar is None:
        # Cannot verify coverage; do not keep a stale available claim.
        return _PinnedLiveView(market_phase="unknown", calendar_status="unavailable")
    try:
        day_status = calendar.day_status(observed_date, session.market)
    except MarketContextError:
        return _PinnedLiveView(market_phase="unknown", calendar_status="unavailable")
    if day_status == "open":
        phase = (
            "pre_open"
            if observed_at.time() < time(9, 30)
            else "market_closed"
        )
        return _PinnedLiveView(market_phase=phase, calendar_status="available")
    # day_status == "closed"
    return _PinnedLiveView(
        market_phase="market_closed",
        calendar_status="available",
    )


def _parse_market_from_symbol(symbol: str) -> tuple[str, str, str]:
    parts = str(symbol).strip().lower().split(".", 1)
    if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) != 6:
        raise ValueError(f"invalid symbol: {symbol!r}")
    return f"{parts[0]}.{parts[1]}", parts[1], parts[0]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def _unclosed_bars(rows: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [row for row in rows if row.get("closed") is False]


def _latest_row_time(
    rows: Sequence[Mapping[str, object]],
    *,
    closed_only: bool = False,
) -> datetime | None:
    values = [
        parsed
        for row in rows
        if not closed_only or row.get("closed") is True
        if (parsed := _timestamp(row.get("timestamp"))) is not None
    ]
    return max(values) if values else None


def _snapshot_branch_time(
    kind: LiveRefreshKind,
    snapshot: dict,
) -> datetime | None:
    market = snapshot["market"]
    if kind is LiveRefreshKind.QUOTE:
        quote = market.get("quote")
        return (
            _timestamp(quote.get("timestamp"))
            if isinstance(quote, dict)
            else None
        )
    if kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
        return _latest_row_time(
            market["bars_30m"],
            closed_only=True,
        )
    return _latest_row_time(
        market["bars_1m" if kind is LiveRefreshKind.ONE_MINUTE else "bars_5m"],
        closed_only=kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
    )


def _branch_updates(
    kind: LiveRefreshKind,
    spec: SessionSpec,
    snapshot: dict,
    *,
    market_epoch: int,
    projection_seq: int,
) -> tuple[LiveIncrementalUpdate, ...]:
    identity = {
        "session_id": spec.session_id,
        "generation": spec.generation,
        "market_epoch": market_epoch,
        "projection_seq": projection_seq,
    }
    live_view_update = LiveIncrementalUpdate(
        **identity,
        event_type="live_market_view_updated",
        payload=snapshot["live_market_view"],
    )
    market = snapshot["market"]
    if kind is LiveRefreshKind.QUOTE:
        return (
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": market["quote"]},
            ),
            live_view_update,
        )
    if kind is LiveRefreshKind.ONE_MINUTE:
        return (
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={
                    "target": "bars_1m",
                    "bars": market["bars_1m"],
                    "quote": None,
                },
            ),
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={
                    "target": "daily_bars",
                    "bars": market["daily_bars"],
                    "quote": None,
                },
            ),
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={
                    "target": "bars_5m",
                    # Only the current unclosed 5m bar.  Store/Renderer drop
                    # unclosed rows absent from this payload so a new bucket
                    # can delete the previous dynamic K without a schema change.
                    "bars": _unclosed_bars(market["bars_5m"]),
                    "quote": None,
                },
            ),
            LiveIncrementalUpdate(
                **identity,
                event_type="indicators_updated",
                payload=snapshot["indicators"],
            ),
            live_view_update,
        )
    if kind is LiveRefreshKind.OFFICIAL_THIRTY_MINUTE:
        return (
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={
                    "target": "bars_30m",
                    # Full display series, including the current dynamic bar.
                    # A closed-only replace would wipe the next-bucket dynamic K.
                    "bars": list(market["bars_30m"]),
                    "quote": None,
                },
            ),
            LiveIncrementalUpdate(
                **identity,
                event_type="indicators_updated",
                payload=snapshot["indicators"],
            ),
            LiveIncrementalUpdate(
                **identity,
                event_type="chan_analysis_30m_replaced",
                payload=snapshot["chan_analysis_30m"],
            ),
            live_view_update,
        )
    return (
        LiveIncrementalUpdate(
            **identity,
            event_type="market_update",
            payload={
                "target": "bars_5m",
                # Full display series, including the current dynamic bar.
                # A closed-only replace would wipe the next-bucket dynamic K.
                "bars": list(market["bars_5m"]),
                "quote": None,
            },
        ),
        LiveIncrementalUpdate(
            **identity,
            event_type="indicators_updated",
            payload=snapshot["indicators"],
        ),
        LiveIncrementalUpdate(
            **identity,
            event_type="chan_analysis_replaced",
            payload=snapshot["chan_analysis"],
        ),
        live_view_update,
    )


def _initial_data_times(
    candidate: LiveSnapshotCandidate,
) -> dict[LiveRefreshKind, datetime | None]:
    snapshot = candidate.build_projection(0).to_dict()
    return {
        kind: _snapshot_branch_time(kind, snapshot)
        for kind in LiveRefreshKind
    }


def _make_thirty_minute_boundary_provider(
    input_port: BranchingLiveInput,
) -> Callable[[datetime], datetime | None]:
    """Create a 30m boundary resolver backed by the Live session's MarketSession.

    Given a wall-clock ``now``, returns the next 30m close boundary that is
    strictly later than ``now``.  Returns ``None`` if the session is not yet
    prepared or ``now`` is past the last boundary (15:00).
    """

    def resolve(now: datetime) -> datetime | None:
        session = input_port.market_session
        if session is None:
            return None
        boundaries = session.bar_close_times(30)
        for boundary in boundaries:
            if boundary > now:
                return boundary
        return None

    return resolve


__all__ = [
    "BranchingLiveInput",
    "LiveBranchDataPort",
    "LiveRuntimeSession",
]
