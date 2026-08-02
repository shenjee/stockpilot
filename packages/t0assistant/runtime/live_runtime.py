"""Production composition for a Live initial load and independent refreshes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import Event, RLock, Thread, current_thread
from typing import Callable, Mapping, Protocol, Sequence

from .computation_executor import BoundedComputationExecutor
from .coordinator import SessionSpec
from .live_projection_store import LiveIncrementalUpdate
from .live_refresh import (
    LiveRefreshInputPort,
    LiveRefreshIntervals,
    LiveRefreshKind,
    LiveRefreshResult,
    LiveRefreshScheduler,
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
    WorkbenchPipeline,
)


class LiveBranchDataPort(LiveInitialInputPort, Protocol):
    """Initial input plus narrow normalized reads for each refresh branch."""

    def load_refresh_bars(
        self,
        spec: SessionSpec,
        *,
        timeframe: str,
    ) -> Sequence[Mapping[str, object]]: ...

    def load_refresh_quotes(
        self,
        spec: SessionSpec,
    ) -> Sequence[Mapping[str, object]]: ...


class BranchingLiveInput(LiveInitialInputPort, LiveRefreshInputPort):
    """Cache initial normalized input and refresh exactly one source branch.

    Provider reads happen outside the state lock, so one slow or failed branch
    cannot prevent the scheduler's other workers from reading their sources.
    The short locked section only merges normalized rows and rebuilds the
    shared workbench projection from that coherent prefix.
    """

    def __init__(
        self,
        source: LiveBranchDataPort,
        *,
        analyzer: CzscAnalyzerPort | None = None,
    ) -> None:
        self._source = source
        self._analyzer = analyzer
        self._lock = RLock()
        self._session = None
        self._market_input: PipelineMarketInput | None = None

    def prepare(
        self,
        spec: SessionSpec,
        *,
        minimum_preheat_5m: int,
    ) -> PreparedLiveWarmup:
        prepared = self._source.prepare(
            spec,
            minimum_preheat_5m=minimum_preheat_5m,
        )
        market_input = prepared.market_input_port.read(prepared.target_time)
        with self._lock:
            self._session = prepared.market_session
            self._market_input = market_input
        return prepared

    def refresh(
        self,
        kind: LiveRefreshKind,
        spec: SessionSpec,
        *,
        observed_at: datetime,
        latest_data_time: datetime | None,
    ) -> LiveRefreshResult:
        if kind is LiveRefreshKind.QUOTE:
            rows = tuple(self._source.load_refresh_quotes(spec))
        else:
            rows = tuple(
                self._source.load_refresh_bars(
                    spec,
                    timeframe=(
                        "1m"
                        if kind is LiveRefreshKind.ONE_MINUTE
                        else "5m"
                    ),
                )
            )
        data_time = _latest_row_time(
            rows,
            closed_only=kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
        )
        if (
            data_time is None
            or latest_data_time is not None
            and data_time <= latest_data_time
        ):
            return LiveRefreshResult.no_change()

        with self._lock:
            if self._market_input is None or self._session is None:
                raise RuntimeError("Live refresh cannot run before initial prepare")
            if kind is LiveRefreshKind.QUOTE:
                updated_input = replace(self._market_input, quote_snapshots=rows)
            elif kind is LiveRefreshKind.ONE_MINUTE:
                updated_input = replace(self._market_input, bars_1m=rows)
            else:
                updated_input = replace(self._market_input, official_5m_bars=rows)
            # This is the intentional cross-branch consistency boundary.
            # Provider I/O remains outside the lock and can fail independently;
            # merging the cached prefix and rebuilding the projection are
            # serialized so two successful branches cannot publish projections
            # from torn combinations of cached inputs.
            # Preview must stay on the effective session day (weekend / pre-open
            # wall clocks resolve to a prior trade_date via Live Market View).
            preview_at = observed_at
            if observed_at.date() != self._session.trade_date:
                preview_at = self._session.end
            result = WorkbenchPipeline(
                session=self._session,
                market_input_port=_FixedMarketInput(updated_input),
                analyzer=self._analyzer,
            ).preview(preview_at)
            self._market_input = updated_input

        snapshot = LiveSnapshotCandidate(
            session_id=spec.session_id,
            generation=spec.generation,
            symbol=spec.symbol,
            pipeline_result=result,
        ).build_projection(0).to_dict()
        return LiveRefreshResult(
            data_time=data_time,
            updates=_branch_updates(kind, spec, snapshot),
        )


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
        on_refresh_failure: Callable[[LiveRefreshKind, BaseException], None],
        on_state_change: Callable[[str, str], None] | None = None,
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
        self._intervals = intervals
        self._clock = clock or datetime.now
        self._poll_interval_seconds = poll_interval_seconds
        self._auto_poll = auto_poll
        self._lock = RLock()
        self._retired = Event()
        self._scheduler: LiveRefreshScheduler | None = None
        self._executor: BoundedComputationExecutor | None = None
        self._poll_thread: Thread | None = None
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

    def run_refresh_due(self, observed_at: datetime | None = None) -> Mapping:
        scheduler = self.refresh_scheduler
        return {} if scheduler is None else scheduler.run_due(observed_at)

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
            scheduler = self.refresh_scheduler
            if scheduler is not None:
                scheduler.run_due(self._clock())


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


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
    return _latest_row_time(
        market["bars_1m" if kind is LiveRefreshKind.ONE_MINUTE else "bars_5m"],
        closed_only=kind is LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
    )


def _branch_updates(
    kind: LiveRefreshKind,
    spec: SessionSpec,
    snapshot: dict,
) -> tuple[LiveIncrementalUpdate, ...]:
    identity = {"session_id": spec.session_id, "generation": spec.generation}
    market = snapshot["market"]
    if kind is LiveRefreshKind.QUOTE:
        return (
            LiveIncrementalUpdate(
                **identity,
                event_type="market_update",
                payload={"target": "quote", "bars": [], "quote": market["quote"]},
            ),
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
                event_type="indicators_updated",
                payload=snapshot["indicators"],
            ),
        )
    return (
        LiveIncrementalUpdate(
            **identity,
            event_type="market_update",
            payload={
                "target": "bars_5m",
                "bars": [
                    row for row in market["bars_5m"] if row.get("closed") is True
                ],
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
    )


def _initial_data_times(
    candidate: LiveSnapshotCandidate,
) -> dict[LiveRefreshKind, datetime | None]:
    snapshot = candidate.build_projection(0).to_dict()
    return {
        kind: _snapshot_branch_time(kind, snapshot)
        for kind in LiveRefreshKind
    }


__all__ = [
    "BranchingLiveInput",
    "LiveBranchDataPort",
    "LiveRuntimeSession",
]
