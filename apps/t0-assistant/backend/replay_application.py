"""Production composition for Replay commands."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.repositories.securities_store import SecuritiesStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services import KLineDataService, SecuritiesSearchService
from packages.t0assistant.replay import (
    ReplayAccepted,
    ReplayApiError,
    ReplayCommandApi,
)
from packages.t0assistant.replay.api import (
    map_computation_outcome_to_replay_error,
    map_replay_prepare_error_to_replay_error,
)
from packages.t0assistant.runtime import (
    BoundedComputationExecutor,
    ReplayDataPreparator,
    ReplayPreparationConfig,
    ReplaySession,
    ReplaySessionStateError,
)
from packages.t0assistant.trading import SimulatedTradeCommandApi

try:
    from backend.historical_snapshot_api import (
        _build_market_context,
        _ensure_context_covers,
    )
except ImportError:
    from historical_snapshot_api import _build_market_context, _ensure_context_covers


class ReplayApplication:
    def __init__(
        self,
        *,
        service_generation: int,
        prepare: Callable[[str, str], Any],
        resolve_security: Callable[[str], dict[str, str] | None],
        publish_event: Callable[[dict[str, Any]], None],
    ) -> None:
        self.service_generation = service_generation
        self._prepare = prepare
        self._resolve_security = resolve_security
        self._publish_event = publish_event
        self._executor = BoundedComputationExecutor(capacity=32, worker_count=1)
        self._sessions: dict[str, ReplaySession] = {}
        self._api: ReplayCommandApi | None = None

    def bind(self, api: ReplayCommandApi) -> None:
        self._api = api

    def session(self, session_id: str) -> ReplaySession | None:
        session = self._sessions.get(session_id)
        return None if session is None or session.retired else session

    def execute(
        self,
        command: str,
        request: Mapping[str, Any],
    ) -> ReplayAccepted:
        if command == "select_symbol":
            security = self._security(request["symbol"])
            return ReplayAccepted(data={"security": security})
        if command == "begin_replay":
            security = self._security(request["symbol"])
            session_id = f"replay-{uuid4().hex}"
            operation_id = f"replay-load-{uuid4().hex}"
            return ReplayAccepted(
                session_id=session_id,
                operation_id=operation_id,
                start_operation=lambda: self._begin(
                    session_id,
                    operation_id,
                    security["symbol"],
                    request["trade_date"],
                ),
            )

        session = self.session(request["session_id"])
        if session is None:
            raise ReplayApiError("session_not_found")
        try:
            if command == "get_replay_snapshot":
                return ReplayAccepted(
                    session_id=session.session_id,
                    data={"snapshot": session.snapshot()},
                )
            if command == "set_replay_playback":
                session.play() if request["playing"] else session.pause()
                return ReplayAccepted(session_id=session.session_id)
            if command == "set_replay_speed":
                session.set_playback_speed(request["playback_speed"])
                return ReplayAccepted(session_id=session.session_id)
        except ReplaySessionStateError as exc:
            code = "replay_busy" if "busy" in str(exc) else "invalid_replay_state"
            raise ReplayApiError(code) from exc
        if command == "end_replay":
            return ReplayAccepted(
                session_id=session.session_id,
                commit=lambda: self._end(session.session_id),
            )

        operation_id = f"replay-cursor-{uuid4().hex}"
        if command == "step_replay" and session.next_bar_time is None:
            return ReplayAccepted(session_id=session.session_id)
        action = (
            (lambda: session.step(operation_id))
            if command == "step_replay"
            else (lambda: session.seek(request["target_time"], operation_id))
        )
        return ReplayAccepted(
            session_id=session.session_id,
            operation_id=operation_id,
            start_operation=lambda: self._cursor(session, operation_id, action),
        )

    def _security(self, symbol: str) -> dict[str, str]:
        security = self._resolve_security(symbol)
        if security is None or security["symbol"] != symbol:
            raise ReplayApiError("symbol_not_found")
        return security

    def _begin(
        self,
        session_id: str,
        operation_id: str,
        symbol: str,
        trade_date: str,
    ) -> None:
        try:
            prepared = self._prepare(symbol, trade_date)
            session = ReplaySession(
                session_id,
                self.service_generation,
                prepared,
                self._executor,
                on_event=self._publish_event,
                on_trade_event=self._publish_event,
                initial_operation_id=operation_id,
            )
            self._sessions[session_id] = session
            initial = session.take_initial_result()
            error = (
                None
                if initial is None
                else map_computation_outcome_to_replay_error(initial.outcome)[0]
            )
            if error is None:
                self._api_or_raise().complete_operation(operation_id)
            else:
                self._api_or_raise().deliver_operation_failure(
                    operation_id=operation_id,
                    session_id=session_id,
                    revision=initial.revision,
                    error=error,
                )
        except Exception as exc:
            error = map_replay_prepare_error_to_replay_error(exc)[0]
            self._api_or_raise().deliver_operation_failure(
                operation_id=operation_id,
                session_id=session_id,
                revision=0,
                error=error,
            )

    def _cursor(
        self,
        session: ReplaySession,
        operation_id: str,
        action: Callable[[], Any],
    ) -> None:
        try:
            result = action()
            error = (
                None
                if result.outcome is None
                else map_computation_outcome_to_replay_error(result.outcome)[0]
            )
            if error is None:
                self._api_or_raise().complete_operation(operation_id)
            else:
                self._api_or_raise().deliver_operation_failure(
                    operation_id=operation_id,
                    session_id=session.session_id,
                    revision=result.revision,
                    error=error,
                )
        except Exception:
            self._api_or_raise().deliver_operation_failure(
                operation_id=operation_id,
                session_id=session.session_id,
                revision=session.revision + 1,
                error=ReplayApiError("invalid_replay_state"),
            )

    def _end(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.retire()

    def close(self) -> None:
        for session in tuple(self._sessions.values()):
            session.retire()
        self._sessions.clear()
        self._executor.shutdown(cancel_pending=True, wait=True)

    def _api_or_raise(self) -> ReplayCommandApi:
        if self._api is None:
            raise RuntimeError("ReplayApplication is not bound")
        return self._api


def create_replay_application(
    service_generation: int,
    *,
    publish_event: Callable[[dict[str, Any]], None],
    db_path: Path | None = None,
    provider: TencentStockDataProvider | None = None,
    clock: Callable[[], date] | None = None,
) -> tuple[ReplayCommandApi, SimulatedTradeCommandApi]:
    paths = RuntimePaths()
    paths.ensure_dirs()
    market_db = db_path or paths.db_dir / "market_data.sqlite"
    store = KLineStore(market_db)
    resolved_provider = provider or TencentStockDataProvider()
    context = _build_market_context(
        resolved_provider,
        store,
        (clock or date.today)(),
    )
    securities = SecuritiesSearchService(SecuritiesStore(market_db))

    def prepare(symbol: str, trade_date: str) -> Any:
        effective_context = _ensure_context_covers(
            context,
            date.fromisoformat(trade_date),
        )
        market_data = KLineDataService(
            provider=resolved_provider,
            store=store,
            market_context=effective_context,
        )
        return ReplayDataPreparator(
            market_data,
            effective_context,
        ).prepare(
            symbol,
            trade_date,
            config=ReplayPreparationConfig(
                deadline_monotonic=time.monotonic() + 8,
            ),
        )

    application = ReplayApplication(
        service_generation=service_generation,
        prepare=prepare,
        resolve_security=lambda symbol: (
            securities.get(symbol[3:], symbol[:2])
            if len(symbol) == 9 and symbol[2] == "."
            else None
        ),
        publish_event=publish_event,
    )
    api = ReplayCommandApi(
        application,
        service_generation=service_generation,
        publish_event=publish_event,
    )
    application.bind(api)
    return api, SimulatedTradeCommandApi(application.session)


__all__ = ["ReplayApplication", "create_replay_application"]
