"""Narrow production composition for the Live T+0 application flow.

This module keeps HTTP/WebSocket details out of the runtime package while
connecting the existing Coordinator, LiveSession, projection authority, and
preference service.  All market access remains behind ``LiveInitialInputPort``;
tests can therefore drive the full flow without network access.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from packages.marketdata.calendar_query import (
    CalendarQueryPort,
    MarketContextCalendarAdapter,
)
from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.repositories.securities_store import SecuritiesStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services.kline_data_service import KLineDataService
from packages.marketdata.services.securities_search_service import SecuritiesSearchService
from packages.marketdata.t0_schema import InstrumentIdentity
from packages.t0assistant.preferences import (
    PreferencePersistenceError,
    PreferenceService,
    PreferencesReadOnlyError,
)
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
    LiveCalendarUnavailableError,
    LiveDataPreparator,
    LiveIncrementalUpdate,
    LiveProjectionStore,
    LiveRefreshKind,
    LiveRuntimeSession,
    SessionSpec,
)
from packages.t0assistant.runtime.thirty_minute_warnings import (
    THIRTY_MINUTE_OFFICIAL_DELAYED,
    warning_dict,
)

try:
    from backend.historical_snapshot_api import _build_live_market_context
except ImportError:
    from historical_snapshot_api import _build_live_market_context


class LiveSessionFactory:
    """Create Live initial-load plus production refresh runtimes."""

    def __init__(
        self,
        input_port: LiveBranchDataPort,
        *,
        analyzer: CzscAnalyzerPort | None = None,
        calendar: CalendarQueryPort | None = None,
        auto_poll: bool = True,
    ) -> None:
        self._input_port = input_port
        self._analyzer = analyzer
        self._calendar = calendar
        self._auto_poll = auto_poll
        self._candidate_handler: Callable[[Any], None] | None = None
        self._incremental_handler: Callable[[LiveIncrementalUpdate], object] | None = None
        self._refresh_failure_handler: (
            Callable[
                [SessionSpec, LiveRefreshKind, BaseException, int | None], None
            ]
            | None
        ) = None
        self._state_handler: Callable[[SessionSpec, str, str], None] | None = None
        self._thirty_minute_delayed_handler: Callable[[bool], None] | None = None
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
            [SessionSpec, LiveRefreshKind, BaseException, int | None], None
        ],
        state_handler: Callable[[SessionSpec, str, str], None],
        thirty_minute_delayed_handler: Callable[[bool], None] | None = None,
    ) -> None:
        self._candidate_handler = candidate_handler
        self._incremental_handler = incremental_handler
        self._refresh_failure_handler = refresh_failure_handler
        self._state_handler = state_handler
        self._thirty_minute_delayed_handler = thirty_minute_delayed_handler

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
            calendar=self._calendar,
        )
        session = LiveRuntimeSession(
            spec,
            runtime_input,
            on_snapshot_candidate=self._candidate_handler,
            on_incremental_update=self._incremental_handler,
            on_refresh_failure=lambda kind, failure, market_epoch=None: (
                self._refresh_failure_handler(spec, kind, failure, market_epoch)
            ),
            on_state_change=lambda state, reason: self._state_handler(
                spec, state, reason
            ),
            on_thirty_minute_delayed=self._thirty_minute_delayed_handler,
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
        resolve_security: Callable[[str], InstrumentIdentity | None] | None = None,
        restore_on_startup: bool = True,
    ) -> None:
        self._service_generation = service_generation
        self._preference_service = preference_service
        self._event_publisher = event_publisher
        self._session_factory = session_factory
        self._resolve_security = resolve_security
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
            thirty_minute_delayed_handler=self._on_thirty_minute_delayed,
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
        restored_security: InstrumentIdentity | None = None
        startup_restore: dict[str, Any] = {"status": "none"}
        if symbol is not None:
            if self._resolve_security is not None:
                restored_security = self._resolve_security(symbol)
            if restored_security is None:
                startup_restore = {"status": "invalid_symbol", "symbol": symbol}
            elif self._coordinator.snapshot.current_symbol is None:
                self._coordinator.select_symbol(
                    symbol,
                    instrument=restored_security,
                )
                live = self._coordinator.snapshot.live_session
                startup_restore = {
                    "status": "restored",
                    "symbol": symbol,
                    "session_id": live.session_id if live is not None else None,
                }
            else:
                live = self._coordinator.snapshot.live_session
                startup_restore = {
                    "status": "already_active",
                    "symbol": symbol,
                    "session_id": live.session_id if live is not None else None,
                }
        return {
            **restored.snapshot.to_dict(),
            "capability": {
                "readable": restored.capability.readable,
                "writable": restored.capability.writable,
                "reason": restored.capability.reason,
            },
            "restored_security": (
                restored_security.to_dict()
                if restored_security is not None
                else None
            ),
            "startup_restore": startup_restore,
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
            # Layout/layer saves never touch last_symbol; select_security owns it.
            snapshot = self._preference_service.save_layout_layers(
                preferences["layout"],
                preferences["layers"],
            )
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
        identity: InstrumentIdentity | None = None
        if self._resolve_security is not None:
            identity = self._resolve_security(symbol)
        if identity is None:
            return self._rejected(
                request_id,
                "security_not_found",
                f"证券 {symbol} 未在证券主数据中找到",
                category="validation",
                affected_capability="symbol_selection",
                retryable=False,
            )
        try:
            before = self._coordinator.snapshot
            snapshot = self._coordinator.select_symbol(
                symbol,
                instrument=identity,
            )
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
        data: dict[str, Any] = {
            "session_id": live.session_id,
            "security": identity.to_dict(),
        }
        warning = self._save_last_symbol(symbol)
        if (
            before.live_session is not None
            and before.live_session.session_id == live.session_id
        ):
            self._republish_current_snapshot(live.session_id, live.generation)
        if warning is not None:
            data["preference_warning"] = warning
            self._publish_preference_warning(warning, request_id=request_id)
        return self._accepted(
            request_id,
            operation_id=self._operation_id(live.session_id),
            data=data,
        )

    def resolve_security_identity(
        self,
        *,
        request_id: str,
        symbol: str,
    ) -> dict[str, Any]:
        """Resolve security master identity without changing Live state."""

        if self._resolve_security is None:
            return self._rejected(
                request_id,
                "service_unavailable",
                "证券主数据服务暂时不可用",
                category="service",
                affected_capability="security_identity",
                retryable=True,
            )
        identity = self._resolve_security(symbol)
        if identity is None:
            return self._rejected(
                request_id,
                "security_not_found",
                f"证券 {symbol} 未在证券主数据中找到",
                category="validation",
                affected_capability="security_identity",
                retryable=False,
            )
        return self._accepted(request_id, data={"security": identity.to_dict()})

    def save_last_symbol(
        self,
        *,
        request_id: str,
        symbol: str,
    ) -> dict[str, Any]:
        warning = self._save_last_symbol(symbol)
        if warning is not None:
            self._publish_preference_warning(warning, request_id=request_id)
            return self._rejected(
                request_id,
                warning["error_code"],
                warning["message"],
                category=warning["category"],
                affected_capability=warning["affected_capability"],
                retryable=warning["retryable"],
            )
        return self._accepted(request_id, data={})

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
        if command == "resolve_security_identity":
            return self.resolve_security_identity(
                request_id=request_id,
                symbol=payload["symbol"],
            )
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
        if command == "save_last_symbol":
            return self.save_last_symbol(
                request_id=request_id,
                symbol=payload["symbol"],
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

    def _on_thirty_minute_delayed(self, delayed: bool) -> None:
        session = self._session_factory.latest_session
        if session is None:
            return
        spec = session.spec
        try:
            snapshot = self._store.get_live_snapshot(
                session_id=spec.session_id,
                generation=spec.generation,
            )
        except Exception:
            return
        warnings = [
            warning
            for warning in snapshot.get("warnings") or []
            if warning.get("warning_code") != "thirty_minute_official_delayed"
        ]
        if delayed:
            warnings.append(warning_dict(THIRTY_MINUTE_OFFICIAL_DELAYED))
        accepted = self._store.sync_warnings(
            session_id=spec.session_id,
            generation=spec.generation,
            warnings=warnings,
        )
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _on_refresh_failure(
        self,
        spec: SessionSpec,
        kind: LiveRefreshKind,
        failure: BaseException,
        market_epoch: int | None = None,
    ) -> None:
        operation_id = f"live-refresh-{kind.value}-{spec.session_id}"
        capabilities = {
            LiveRefreshKind.QUOTE: "live",
            LiveRefreshKind.ONE_MINUTE: "intraday_chart",
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE: "five_minute_chart",
            LiveRefreshKind.OFFICIAL_THIRTY_MINUTE: "thirty_minute_chart",
        }
        accepted = self._store.accept_operation_failure(
            session_id=spec.session_id,
            generation=spec.generation,
            operation_id=operation_id,
            market_epoch=market_epoch,
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
        failure = self._session_factory.latest_session
        calendar_failure = (
            isinstance(failure, LiveRuntimeSession)
            and isinstance(failure.failure, LiveCalendarUnavailableError)
        )
        accepted = self._store.accept_operation_failure(
            session_id=spec.session_id,
            generation=spec.generation,
            operation_id=operation_id,
            payload={
                "error_code": (
                    "calendar_unavailable"
                    if calendar_failure
                    else "calculation_failed"
                ),
                "category": "data" if calendar_failure else "calculation",
                "severity": "error",
                "retryable": False if calendar_failure else True,
                "affected_capability": (
                    "market_calendar" if calendar_failure else "live"
                ),
                "message": (
                    "交易日历覆盖不足，无法权威解析有效交易日"
                    if calendar_failure
                    else "Live 行情加载失败，请重试"
                ),
                "request_id": operation_id,
                "operation_id": operation_id,
                "details": {},
            },
        )
        if accepted is not None:
            self._event_publisher.publish_envelope(accepted.to_envelope())

    def _save_last_symbol(self, symbol: str) -> dict[str, Any] | None:
        try:
            self._preference_service.save_last_symbol(symbol)
            return None
        except PreferencesReadOnlyError as exc:
            return {
                "error_code": "preference_read_only",
                "category": "persistence",
                "severity": "error",
                "retryable": False,
                "affected_capability": "preferences",
                "message": str(exc),
                "details": {},
            }
        except PreferencePersistenceError:
            return {
                "error_code": "preference_persist_failed",
                "category": "persistence",
                "severity": "error",
                "retryable": True,
                "affected_capability": "preferences",
                "message": "最后选择的股票未能保存，重启后可能需要重新选择",
                "details": {},
            }
        except Exception:
            return {
                "error_code": "preference_persist_failed",
                "category": "persistence",
                "severity": "error",
                "retryable": True,
                "affected_capability": "preferences",
                "message": "最后选择的股票未能保存，重启后可能需要重新选择",
                "details": {},
            }

    def _publish_preference_warning(
        self,
        warning: dict[str, Any],
        *,
        request_id: str,
    ) -> None:
        self._event_publisher.publish(
            event_type="operation_failed",
            payload={
                **warning,
                "request_id": request_id,
            },
            session_id=None,
        )

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
                "schema_version": "t0_app_v2",
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
            "schema_version": "t0_app_v2",
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
            "schema_version": "t0_app_v2",
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
    context = _build_live_market_context(
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
    securities_store = SecuritiesStore(paths.db_dir / "market_data.sqlite")
    search_service = SecuritiesSearchService(securities_store)
    calendar = MarketContextCalendarAdapter(context)
    preparator = LiveDataPreparator(
        market_data,
        context,
        calendar=calendar,
        quote_reader=resolved_provider,
    )
    api = LiveApplicationApi(
        service_generation=service_generation,
        session_factory=LiveSessionFactory(
            preparator,
            calendar=calendar,
        ),
        preference_service=preferences,
        event_publisher=event_publisher,
        resolve_security=search_service.resolve,
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
