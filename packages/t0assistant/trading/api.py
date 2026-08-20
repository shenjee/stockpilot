"""Transport-agnostic command boundary for real trades (T0-043).

``TradeCommandApi`` is the Python-side counterpart of the frozen App v1 trade
commands ``list_trades`` / ``create_trade`` / ``update_trade`` / ``delete_trade``.
It sits between the formal HTTP transport (``backend.service``) and the
real-trade :class:`~packages.t0assistant.trading.service.TradeService`, turning
validated command requests into either an accepted ``command_response`` plus an
authoritative ``trades_changed`` event, or a structured ``application_error``.

Contract obligations enforced here (see ``contracts/README.md`` and
``contracts/fixtures/list-trades-flow-v1.json``):

* ``list_trades`` is a *fact-via-changed-event* command. Its accepted response
  carries ``operation_id: null`` and ``data: null``; the renderer must not read
  ``command_response.data``. After an accepted ``list_trades`` exactly one real
  ``trades_changed`` event (``session_id: null``) is published, including when
  the repository is empty (``payload.trades: []``).
* ``create_trade`` / ``update_trade`` / ``delete_trade`` persist synchronously
  through the repository. A trade only becomes a fact once the repository
  confirms the write; on any persistence failure the repository exception is
  mapped to an ``application_error`` and **no** ``trades_changed`` is published
  and **no** revision is bumped, so a failed write can never leave the renderer
  with a "front-end success, database failure" state.
* ``payload.trades`` is a *complete repository snapshot* (every persisted real
  trade for every symbol and trading date). The wire payload deliberately
  carries no scope fields; consumers filter and sort themselves.
* ``trade_revision`` is monotonic within one ``service_generation`` and starts
  at ``0`` for an empty repository. A Python restart produces a new
  ``service_generation`` and resets the gate (the renderer compares the
  ``(service_generation, trade_revision)`` pair).

The transport (envelope ``revision`` used for delivery ordering) is owned by
the injected :class:`TradeEventPublisher`; this layer owns only the
trade-scoped ``trade_revision`` and the snapshot contents. Fee calculation is
not performed here: the user-confirmed fee is persisted verbatim, so changing a
fee plan never retroactively alters a historical trade.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Protocol

from .models import TradeValidationError
from .service import TradeEligibilityError, TradeService


class TradeEventPublisher(Protocol):
    """Transport port that publishes a real ``trades_changed`` event envelope.

    The concrete implementation (owned by the formal service transport) assigns
    the envelope ``revision`` and ``service_generation`` and delivers the
    envelope to connected renderers. This layer supplies only the trade-scoped
    ``trade_revision`` and the full snapshot contents.
    """

    def publish_trades_changed(
        self,
        *,
        service_generation: int,
        trade_revision: int,
        trades: list[dict[str, Any]],
        operation_id: str | None = None,
    ) -> None:
        """Publish one authoritative real ``trades_changed`` envelope."""


# Map repository / domain exceptions to a stable application_error. The frozen
# contract leaves ``error_code`` as a free-form non-empty string, so these
# stable snake_case codes are part of this layer's public surface.
_ERROR_DEFINITIONS: dict[type[BaseException], dict[str, Any]] = {
    TradeValidationError: {
        "error_code": "invalid_trade_request",
        "category": "validation",
        "retryable": False,
    },
}


def _error_def(error_code: str, *, category: str, retryable: bool) -> dict[str, Any]:
    return {
        "error_code": error_code,
        "category": category,
        "retryable": retryable,
    }


def _map_eligibility_error(error: TradeEligibilityError) -> dict[str, Any]:
    """Map a :class:`TradeEligibilityError` to a stable application_error.

    The error message carries the specific stable code set by
    :class:`TradeService._require_eligible`:
    ``"security_not_found"``, ``"security_not_tradable"``, or
    ``"service_unavailable"``.  Each maps to a distinct error_code so the
    renderer can distinguish "not found" from "not tradable" from
    "eligibility service down" instead of receiving a generic
    ``trade_service_unavailable`` for all three.
    """
    # TradeValidationError stores (field, message); str() yields "field message".
    parts = str(error).strip().split(maxsplit=1)
    code = parts[1].strip() if len(parts) == 2 else ""
    if code == "security_not_found":
        return _error_def(
            "security_not_found", category="validation", retryable=False
        )
    if code == "security_not_tradable":
        return _error_def(
            "security_not_tradable", category="validation", retryable=False
        )
    if code == "service_unavailable":
        return _error_def(
            "eligibility_service_unavailable",
            category="service",
            retryable=True,
        )
    # Unknown eligibility code: surface as a retryable service error.
    return _error_def(
        "trade_service_unavailable", category="service", retryable=True
    )


# Repository exceptions are imported lazily to keep this module importable
# without the repositories package (e.g. for pure unit tests with a fake
# repository). They are resolved to definitions on first use.
def _resolve_repository_errors() -> dict[type[BaseException], dict[str, Any]]:
    try:
        from packages.t0assistant.repositories.trading import (
            RepositoryConflictError,
            RepositoryNotFoundError,
            RepositoryPersistenceError,
            RepositoryReadOnlyError,
        )
    except ImportError:  # pragma: no cover - repositories always present in app
        return {}
    return {
        RepositoryNotFoundError: _error_def(
            "trade_not_found", category="data", retryable=False
        ),
        RepositoryReadOnlyError: _error_def(
            "repository_read_only", category="persistence", retryable=False
        ),
        RepositoryConflictError: _error_def(
            "trade_conflict", category="data", retryable=False
        ),
        RepositoryPersistenceError: _error_def(
            "trade_persist_failed", category="persistence", retryable=True
        ),
    }


class TradeCommandApi:
    """Dispatch the frozen trade commands over a :class:`TradeService`.

    The API keeps no trade cache: every snapshot published to the renderer is
    read fresh from the repository, so a published ``trades_changed`` always
    reflects the persisted truth. ``trade_revision`` is the only in-memory
    state, and it is monotonic within the ``service_generation``.
    """

    def __init__(
        self,
        service: TradeService,
        *,
        service_generation: int,
        publisher: TradeEventPublisher | None = None,
    ) -> None:
        if not isinstance(service, TradeService):
            raise TypeError("service must be a TradeService")
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        self._service = service
        self._service_generation = service_generation
        self._publisher = publisher
        self._trade_revision = 0
        self._lock = Lock()

    @property
    def service_generation(self) -> int:
        return self._service_generation

    @property
    def trade_revision(self) -> int:
        """Current monotonic trade revision (0 for an empty repository)."""
        with self._lock:
            return self._trade_revision

    def dispatch(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a schema-valid trade ``command_request``.

        Returns a ``command_response`` payload. On an accepted mutation the
        authoritative ``trades_changed`` event has already been published.
        """
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = "missing-request-id"
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return self._reject(request_id, "invalid_trade_request", "validation",
                                "成交命令负载无效", retryable=False)

        try:
            if command == "list_trades":
                return self._list_trades(request_id, payload)
            if command == "create_trade":
                return self._create_trade(request_id, payload)
            if command == "update_trade":
                return self._update_trade(request_id, payload)
            if command == "delete_trade":
                return self._delete_trade(request_id, payload)
        except Exception as error:  # mapped to a structured application_error
            mapped = self._map_error(error)
            return self._reject(
                request_id,
                mapped["error_code"],
                mapped["category"],
                self._message_for(error),
                retryable=mapped["retryable"],
            )
        # The transport validates the command name against the frozen schema
        # before dispatch, so an unknown command is a transport-layer concern.
        return self._reject(
            request_id, "invalid_trade_request", "validation",
            f"未知成交命令：{command}", retryable=False,
        )

    # -- Command handlers ------------------------------------------------

    def _list_trades(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        # list_trades is real-only: simulated trades belong to the Replay
        # Session and never reach this service or the real repository.
        if payload.get("trade_scope") != "real":
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "list_trades 仅支持真实成交", retryable=False,
            )
        # Publish the current full snapshot at the current revision. A read
        # failure does NOT publish an empty fact: the exception propagates to
        # _map_error and no event is published.
        with self._lock:
            trades = self._snapshot()
            revision = self._trade_revision
        self._publish(revision, trades)
        return self._accepted(request_id)

    def _create_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_payload = payload.get("trade")
        if not isinstance(trade_payload, dict):
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "成交记录缺失", retryable=False,
            )
        with self._lock:
            self._service.create_trade(trade_payload)  # validates + persists
            self._trade_revision += 1
            trades = self._snapshot()
            revision = self._trade_revision
        self._publish(revision, trades)
        return self._accepted(request_id)

    def _update_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_id = payload.get("trade_id")
        trade_payload = payload.get("trade")
        if not isinstance(trade_id, str) or not trade_id:
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "成交记录标识缺失", retryable=False,
            )
        if not isinstance(trade_payload, dict):
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "成交记录缺失", retryable=False,
            )
        with self._lock:
            # update_trade preserves trade_id and persists the user-confirmed
            # fee verbatim; raises RepositoryNotFoundError if the id is gone.
            self._service.update_trade(trade_id, trade_payload)
            self._trade_revision += 1
            trades = self._snapshot()
            revision = self._trade_revision
        self._publish(revision, trades)
        return self._accepted(request_id)

    def _delete_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_id = payload.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "成交记录标识缺失", retryable=False,
            )
        if payload.get("trade_scope") != "real":
            return self._reject(
                request_id, "invalid_trade_request", "validation",
                "delete_trade 仅支持真实成交", retryable=False,
            )
        with self._lock:
            # Hard delete; returns False when no trade had the id. A missing
            # trade is reported so the renderer can reconcile, but it is not a
            # persistence failure (no revision bump, no publish on the False
            # branch handled below).
            deleted = self._service.delete_trade(trade_id)
            if not deleted:
                raise _TradeMissing(trade_id)
            self._trade_revision += 1
            trades = self._snapshot()
            revision = self._trade_revision
        self._publish(revision, trades)
        return self._accepted(request_id)

    # -- Helpers ---------------------------------------------------------

    def _snapshot(self) -> list[dict[str, Any]]:
        """Read the complete real-trade repository snapshot, freshest first.

        The wire payload is unordered by contract; the repository returns a
        deterministic ``executed_at, trade_id`` ordering which is a stable base
        for consumers. Renderers sort for display themselves.
        """
        return [record.to_dict() for record in self._service.list_all_trades()]

    def _publish(self, trade_revision: int, trades: list[dict[str, Any]]) -> None:
        if self._publisher is None:
            return
        self._publisher.publish_trades_changed(
            service_generation=self._service_generation,
            trade_revision=trade_revision,
            trades=trades,
        )

    def _accepted(self, request_id: str) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": None,
            "error": None,
        }

    def _reject(
        self,
        request_id: str,
        error_code: str,
        category: str,
        message: str,
        *,
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
                "affected_capability": "trades",
                "message": message,
                "request_id": request_id,
                "details": {},
            },
        }

    @staticmethod
    def _message_for(error: BaseException) -> str:
        message = str(error).strip()
        return message or "成交操作未完成"

    @staticmethod
    def _map_error(error: BaseException) -> dict[str, Any]:
        if isinstance(error, _TradeMissing):
            return _error_def("trade_not_found", category="data", retryable=False)
        # Issue #151: TradeEligibilityError is a TradeValidationError subclass.
        # Its message carries the specific stable code ("security_not_found",
        # "security_not_tradable", or "service_unavailable"), so check it
        # before the generic parent mapping to avoid falling through to
        # trade_service_unavailable.
        if isinstance(error, TradeEligibilityError):
            return _map_eligibility_error(error)
        definition = _ERROR_DEFINITIONS.get(type(error))
        if definition is not None:
            return definition
        for error_type, definition in _resolve_repository_errors().items():
            if isinstance(error, error_type):
                return definition
        # Unknown failure: surface as a retryable service error rather than a
        # silent success. No snapshot is published for this path.
        return {
            "error_code": "trade_service_unavailable",
            "category": "service",
            "retryable": True,
        }


class _TradeMissing(Exception):
    """Internal sentinel: delete_trade found no persisted trade for the id."""

    def __init__(self, trade_id: str) -> None:
        self.trade_id = trade_id
        super().__init__(f"成交记录不存在：{trade_id}")


__all__ = ["TradeCommandApi", "TradeEventPublisher"]
