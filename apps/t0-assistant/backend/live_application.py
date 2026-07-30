"""Narrow production composition for the Live T+0 application flow.

This module keeps HTTP/WebSocket details out of the runtime package while
connecting the existing Coordinator, LiveSession, projection authority, and
preference service.  All market access remains behind ``LiveInitialInputPort``;
tests can therefore drive the full flow without network access.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services.kline_data_service import KLineDataService
from packages.t0assistant.preferences import PreferenceService
from packages.t0assistant.repositories import (
    SqlitePreferenceRepository,
    open_app_database,
)
from packages.t0assistant.runtime import (
    AppCoordinator,
    BranchingLiveInput,
    CoordinatorStateError,
    CzscAnalyzerPort,
    LiveBranchDataPort,
    LiveDataPreparator,
    LiveIncrementalUpdate,
    LiveProjectionStore,
    LiveRefreshKind,
    LiveRuntimeSession,
    SessionSpec,
)

try:
    from backend.historical_snapshot_api import _build_market_context
except ImportError:
    from historical_snapshot_api import _build_market_context


class LiveSessionFactory:
    """Create Live initial-load plus production refresh runtimes."""

    def __init__(
        self,
        input_port: LiveBranchDataPort,
        *,
        analyzer: CzscAnalyzerPort | None = None,
        auto_poll: bool = True,
    ) -> None:
        self._input_port = input_port
        self._analyzer = analyzer
        self._auto_poll = auto_poll
        self._candidate_handler: Callable[[Any], None] | None = None
        self._incremental_handler: Callable[[LiveIncrementalUpdate], object] | None = None
        self._refresh_failure_handler: (
            Callable[[SessionSpec, LiveRefreshKind, BaseException], None] | None
        ) = None
        self._state_handler: Callable[[SessionSpec, str, str], None] | None = None
        self._latest_session: LiveRuntimeSession | None = None

    @property
    def latest_session(self) -> LiveRuntimeSession | None:
        return self._latest_session

    def bind(
        self,
        *,
        candidate_handler: Callable[[Any], None],
        incremental_handler: Callable[[LiveIncrementalUpdate], object],
        refresh_failure_handler: Callable[
            [SessionSpec, LiveRefreshKind, BaseException], None
        ],
        state_handler: Callable[[SessionSpec, str, str], None],
    ) -> None:
        self._candidate_handler = candidate_handler
        self._incremental_handler = incremental_handler
        self._refresh_failure_handler = refresh_failure_handler
        self._state_handler = state_handler

    def create_live(self, spec: SessionSpec) -> LiveRuntimeSession:
        if (
            self._candidate_handler is None
            or self._incremental_handler is None
            or self._refresh_failure_handler is None
            or self._state_handler is None
        ):
            raise RuntimeError("LiveSessionFactory must be bound before use")
        runtime_input = BranchingLiveInput(
            self._input_port,
            analyzer=self._analyzer,
        )
        session = LiveRuntimeSession(
            spec,
            runtime_input,
            on_snapshot_candidate=self._candidate_handler,
            on_incremental_update=self._incremental_handler,
            on_refresh_failure=lambda kind, failure: self._refresh_failure_handler(
                spec, kind, failure
            ),
            on_state_change=lambda state, reason: self._state_handler(
                spec, state, reason
            ),
            analyzer=self._analyzer,
            auto_poll=self._auto_poll,
        )
        self._latest_session = session
        return session

    def create_replay(self, spec: SessionSpec) -> Any:
        raise CoordinatorStateError(
            "Replay Session creation belongs to the Replay application adapter"
        )


class LiveApplicationApi:
    """Transport-neutral command surface for selection and Live recovery."""

    def __init__(
        self,
        *,
        service_generation: int,
        session_factory: LiveSessionFactory,
        preference_service: PreferenceService,
        event_publisher: Any,
        restore_on_startup: bool = True,
    ) -> None:
        self._service_generation = service_generation
        self._preference_service = preference_service
        self._event_publisher = event_publisher
        # Production composition assigns the shared SQLite connection here.
        # Its lifetime deliberately matches this API object and therefore the
        # local service process; repositories do not own/close it separately.
        self._database_keep_alive: Any | None = None
        self._coordinator = AppCoordinator(session_factory)
        self._store = LiveProjectionStore(
            self._coordinator,
            service_generation=service_generation,
        )
        session_factory.bind(
            candidate_handler=self._accept_candidate,
            incremental_handler=self._accept_incremental,
            refresh_failure_handler=self._on_refresh_failure,
            state_handler=self._on_state_change,
        )
        if restore_on_startup:
            self.restore_startup()

    @property
    def service_generation(self) -> int:
        return self._service_generation

    @property
    def coordinator(self) -> AppCoordinator:
        return self._coordinator

    @property
    def store(self) -> LiveProjectionStore:
        return self._store

    def restore_startup(self) -> dict[str, Any]:
        restored = self._preference_service.restore_for_startup()
        symbol = restored.snapshot.preferences.last_symbol
        if symbol is not None and self._coordinator.snapshot.current_symbol is None:
            self._coordinator.select_symbol(symbol)
        return {
            **restored.snapshot.to_dict(),
            "capability": {
                "readable": restored.capability.readable,
                "writable": restored.capability.writable,
                "reason": restored.capability.reason,
            },
        }

    def get_preferences(self, *, request_id: str) -> dict[str, Any]:
        return self._accepted(request_id, data=self.restore_startup())

    def save_preferences(
        self,
        *,
        request_id: str,
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            snapshot = self._preference_service.save(preferences)
        except (TypeError, ValueError) as exc:
            return self._rejected(
                request_id,
                "invalid_request",
                str(exc),
                category="validation",
                affected_capability="preferences",
                retryable=False,
            )
        except Exception:
            return self._rejected(
                request_id,
                "preference_persist_failed",
                "偏好设置保存失败",
                category="persistence",
                affected_capability="preferences",
                retryable=True,
            )
        self._event_publisher.publish(
            event_type="preferences_changed",
            payload=snapshot.to_dict(),
            session_id=None,
        )
        return self._accepted(request_id, data=snapshot.to_dict())

    def select_security(
        self,
        *,
        request_id: str,
        symbol: str,
    ) -> dict[str, Any]:
        try:
            before = self._coordinator.snapshot
            snapshot = self._coordinator.select_symbol(symbol)
        except (TypeError, ValueError) as exc:
            return self._rejected(
                request_id,
                "invalid_request",
                str(exc),
                category="validation",
                affected_capability="symbol_selection",
                retryable=False,
            )
        except Exception:
            return self._rejected(
                request_id,
                "service_unavailable",
                "Live 行情加载暂时不可用",
                category="service",
                affected_capability="live",
                retryable=True,
            )

        live = snapshot.live_session
        if live is None:
            return self._rejected(
                request_id,
                "service_unavailable",
                "Live Session 未能建立",
                category="service",
                affected_capability="live",
                retryable=True,
            )
        self._save_last_symbol_best_effort(symbol)
        if (
            before.live_session is not None
            and before.live_session.session_id == live.session_id
        ):
            self._republish_current_snapshot(live.session_id, live.generation)
        return self._accepted(
            request_id,
            operation_id=self._operation_id(live.session_id),
            data={"session_id": live.session_id},
        )

    def retry_live(
        self,
        *,
        request_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        current = self._coordinator.snapshot.live_session
        if current is None or current.session_id != session_id:
            return self._rejected(
                request_id,
                "session_not_found",
                "Live Session 不存在或已退休",
                category="session",
                affected_capability="live",
                retryable=False,
            )
        try:
            snapshot = self._coordinator.retry_live()
        except Exception:
            return self._rejected(
                request_id,
                "service_unavailable",
                "Live Session 恢复失败",
                category="service",
                affected_capability="live",
                retryable=True,
            )
        replacement = snapshot.live_session
        if replacement is None:
            return self._rejected(
                request_id,
                "service_unavailable",
                "Live Session 恢复结果不可用",
                category="service",
                affected_capability="live",
                retryable=True,
            )
        return self._accepted(
            request_id,
            operation_id=self._operation_id(replacement.session_id),
            data={"session_id": replacement.session_id},
        )

    def get_live_snapshot(
        self,
        *,
        request_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        current = self._store.current_session
        if current is None or current[0] != session_id:
            return self._rejected(
                request_id,
                "session_not_found",
                "Live Session 不存在或尚未就绪",
                category="session",
                affected_capability="live",
                retryable=False,
            )
        try:
            snapshot = self._store.get_live_snapshot(
                session_id=session_id,
                generation=current[1],
            )
        except Exception:
            return self._rejected(
                request_id,
                "service_unavailable",
                "Live 快照暂不可用",
                category="service",
                affected_capability="live",
                retryable=True,
            )
        return self._accepted(request_id, data=snapshot)

    def dispatch(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request["request_id"]
        payload = request.get("payload") or {}
        if command == "select_security":
            return self.select_security(
                request_id=request_id,
                symbol=payload["symbol"],
            )
        if command == "retry_live":
            return self.retry_live(
                request_id=request_id,
                session_id=request["session_id"],
            )
        if command == "get_live_snapshot":
            return self.get_live_snapshot(
                request_id=request_id,
                session_id=request["session_id"],
            )
        if command == "get_preferences":
            return self.get_preferences(request_id=request_id)
        if command == "save_preferences":
            return self.save_preferences(
                request_id=request_id,
                preferences=payload["preferences"],
            )
        raise ValueError(f"unsupported Live application command: {command}")

    def _accept_candidate(self, candidate: Any) -> None:
        accepted = self._store.accept_candidate(candidate)
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _accept_incremental(self, update: LiveIncrementalUpdate) -> None:
        accepted = self._store.accept_incremental(update)
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _on_refresh_failure(
        self,
        spec: SessionSpec,
        kind: LiveRefreshKind,
        failure: BaseException,
    ) -> None:
        operation_id = f"live-refresh-{kind.value}-{spec.session_id}"
        capabilities = {
            LiveRefreshKind.QUOTE: "live",
            LiveRefreshKind.ONE_MINUTE: "intraday_chart",
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE: "five_minute_chart",
        }
        accepted = self._store.accept_operation_failure(
            session_id=spec.session_id,
            generation=spec.generation,
            operation_id=operation_id,
            payload={
                "error_code": "calculation_failed",
                "category": "calculation",
                "severity": "error",
                "retryable": True,
                "affected_capability": capabilities[kind],
                "message": "Live 行情刷新失败，请重试",
                "request_id": operation_id,
                "operation_id": operation_id,
                "details": {"refresh_kind": kind.value},
            },
        )
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _on_state_change(
        self,
        spec: SessionSpec,
        state: str,
        reason: str,
    ) -> None:
        if state != "failed":
            return
        operation_id = self._operation_id(spec.session_id)
        accepted = self._store.accept_operation_failure(
            session_id=spec.session_id,
            generation=spec.generation,
            operation_id=operation_id,
            payload={
                "error_code": "calculation_failed",
                "category": "calculation",
                "severity": "error",
                "retryable": True,
                "affected_capability": "live",
                "message": "Live 行情加载失败，请重试",
                "request_id": operation_id,
                "operation_id": operation_id,
                "details": {},
            },
        )
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _save_last_symbol_best_effort(self, symbol: str) -> None:
        try:
            restored = self._preference_service.restore_for_startup().snapshot
            values = replace(restored.preferences, last_symbol=symbol)
            self._preference_service.save(values)
        except Exception:
            # The current Live Session remains useful when preference storage is
            # read-only or temporarily unavailable.
            return

    def _republish_current_snapshot(self, session_id: str, generation: int) -> None:
        try:
            snapshot = self._store.get_live_snapshot(
                session_id=session_id,
                generation=generation,
            )
        except Exception:
            return
        self._event_publisher.publish_envelope(
            {
                "schema_version": "t0_app_v1",
                "service_generation": self._service_generation,
                "session_id": session_id,
                "revision": snapshot["session"]["revision"],
                "event_type": "workbench_snapshot",
                "payload": snapshot,
            }
        )

    @staticmethod
    def _operation_id(session_id: str) -> str:
        return f"live-load-{session_id}"

    @staticmethod
    def _accepted(
        request_id: str,
        *,
        data: dict[str, Any],
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v1",
            "request_id": request_id,
            "accepted": True,
            "operation_id": operation_id,
            "data": data,
            "error": None,
        }

    @staticmethod
    def _rejected(
        request_id: str,
        error_code: str,
        message: str,
        *,
        category: str,
        affected_capability: str,
        retryable: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v1",
            "request_id": request_id,
            "accepted": False,
            "operation_id": None,
            "data": None,
            "error": {
                "error_code": error_code,
                "category": category,
                "severity": "error",
                "retryable": retryable,
                "affected_capability": affected_capability,
                "message": message,
                "request_id": request_id,
                "details": {},
            },
        }


class LazyLiveApplicationApi:
    """Defer market/database construction until the first Live command."""

    def __init__(
        self,
        service_generation: int,
        factory: Callable[[], LiveApplicationApi],
    ) -> None:
        self._service_generation = service_generation
        self._factory = factory
        self._lock = Lock()
        self._resolved: LiveApplicationApi | None = None

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def dispatch(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        return self._resolve().dispatch(command, request)

    def _resolve(self) -> LiveApplicationApi:
        with self._lock:
            if self._resolved is None:
                self._resolved = self._factory()
            return self._resolved


def create_live_application_api(
    service_generation: int,
    *,
    event_publisher: Any,
    market_db_path: Path | None = None,
    app_db_path: Path | None = None,
    provider: TencentStockDataProvider | None = None,
    clock: Callable[[], date] | None = None,
) -> LiveApplicationApi:
    """Build the real local-first Live composition used by ``service.py``."""

    paths = RuntimePaths()
    paths.ensure_dirs()
    resolved_provider = provider or TencentStockDataProvider()
    store = KLineStore(market_db_path or paths.db_dir / "market_data.sqlite")
    context = _build_market_context(
        resolved_provider,
        store,
        (clock or date.today)(),
    )
    market_data = KLineDataService(
        provider=resolved_provider,
        store=store,
        market_context=context,
    )
    database = open_app_database(
        app_db_path or paths.db_dir / "t0_assistant.sqlite"
    )
    preferences = PreferenceService(SqlitePreferenceRepository(database))
    preparator = LiveDataPreparator(
        market_data,
        context,
        quote_reader=resolved_provider,
    )
    api = LiveApplicationApi(
        service_generation=service_generation,
        session_factory=LiveSessionFactory(
            preparator
        ),
        preference_service=preferences,
        event_publisher=event_publisher,
        restore_on_startup=True,
    )
    # Explicit ownership: the API keeps the shared connection alive until the
    # service releases the API at process shutdown.
    api._database_keep_alive = database
    return api


__all__ = [
    "LiveApplicationApi",
    "LazyLiveApplicationApi",
    "LiveSessionFactory",
    "create_live_application_api",
]
