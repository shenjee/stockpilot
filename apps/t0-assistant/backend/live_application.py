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
    CoordinatorStateError,
    CzscAnalyzerPort,
    LiveInitialInputPort,
    LiveDataPreparator,
    LiveProjectionStore,
    LiveSession,
    SessionSpec,
)

try:
    from backend.historical_snapshot_api import _build_market_context
except ImportError:
    from historical_snapshot_api import _build_market_context


class LiveSessionFactory:
    """Create real ``LiveSession`` instances over an injected market port."""

    def __init__(
        self,
        initial_input_port: LiveInitialInputPort,
        *,
        analyzer: CzscAnalyzerPort | None = None,
    ) -> None:
        self._initial_input_port = initial_input_port
        self._analyzer = analyzer
        self._candidate_handler: Callable[[Any], None] | None = None
        self._state_handler: Callable[[SessionSpec, str, str], None] | None = None

    def bind(
        self,
        *,
        candidate_handler: Callable[[Any], None],
        state_handler: Callable[[SessionSpec, str, str], None],
    ) -> None:
        self._candidate_handler = candidate_handler
        self._state_handler = state_handler

    def create_live(self, spec: SessionSpec) -> LiveSession:
        if self._candidate_handler is None or self._state_handler is None:
            raise RuntimeError("LiveSessionFactory must be bound before use")
        return LiveSession(
            spec,
            self._initial_input_port,
            on_snapshot_candidate=self._candidate_handler,
            on_state_change=lambda state, reason: self._state_handler(
                spec, state, reason
            ),
            analyzer=self._analyzer,
            auto_start=False,
        )

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
        self._coordinator = AppCoordinator(session_factory)
        self._store = LiveProjectionStore(
            self._coordinator,
            service_generation=service_generation,
        )
        session_factory.bind(
            candidate_handler=self._accept_candidate,
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
        assert live is not None
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
        assert replacement is not None
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

    def _on_state_change(
        self,
        spec: SessionSpec,
        state: str,
        reason: str,
    ) -> None:
        if state != "failed":
            return
        revision = 0
        if self._store.current_session == (spec.session_id, spec.generation):
            revision = (self._store.current_revision or 0) + 1
        operation_id = self._operation_id(spec.session_id)
        self._event_publisher.publish_envelope(
            {
                "schema_version": "t0_app_v1",
                "service_generation": self._service_generation,
                "session_id": spec.session_id,
                "revision": revision,
                "event_type": "operation_failed",
                "operation_id": operation_id,
                "payload": {
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
            }
        )

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
    api = LiveApplicationApi(
        service_generation=service_generation,
        session_factory=LiveSessionFactory(
            LiveDataPreparator(
                market_data,
                context,
                quote_reader=resolved_provider,
            )
        ),
        preference_service=preferences,
        event_publisher=event_publisher,
        restore_on_startup=True,
    )
    # Keep the shared SQLite connection alive for the service lifetime.
    api._app_database = database
    return api


__all__ = [
    "LiveApplicationApi",
    "LazyLiveApplicationApi",
    "LiveSessionFactory",
    "create_live_application_api",
]
