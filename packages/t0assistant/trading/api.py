"""Transport-agnostic command boundary for real trades (Issue #163).

``TradeCommandApi`` is the Python-side counterpart of the App trade commands
``list_trades`` / ``list_trade_history`` / ``create_trade`` / ``update_trade`` /
``delete_trade``. It sits between the formal HTTP transport
(``backend.service``) and the real-trade
:class:`~packages.t0assistant.trading.service.TradeService`, turning validated
command requests into either an accepted ``command_response`` plus (when
applicable) an authoritative scoped ``trades_changed`` event, or a structured
``application_error``.

Contract obligations enforced here (see ``contracts/README.md``):

* ``list_trades`` is a *fact-via-changed-event* command. Its accepted response
  carries ``operation_id: null`` and ``data: null``; the renderer must not read
  ``command_response.data``. After an accepted ``list_trades`` exactly one real
  ``trades_changed`` event (``session_id: null``) is published for the request
  ``symbol + trade_date`` scope, including when that scope is empty
  (``payload.trades: []``). Index symbols still accept and publish an empty
  scoped fact. The command never publishes a full-repository snapshot.
* ``list_trade_history`` is a synchronous full-repository read. Its accepted
  response carries ``operation_id: null`` and
  ``data: { trade_revision, trades }`` and publishes **no** event.
* ``create_trade`` / ``update_trade`` / ``delete_trade`` persist synchronously
  through the repository. A trade only becomes a fact once the repository
  confirms the write; on any persistence failure the repository exception is
  mapped to an ``application_error`` and **no** ``trades_changed`` is published
  and **no** revision is bumped.
* Mutations publish scoped facts only. An update that moves a trade between
  ``(symbol, trade_date)`` scopes bumps and publishes the old scope (without
  the moved trade) then the new scope, each with a newer ``trade_revision``.
* ``trade_scope: simulated`` is rejected with ``unsupported_trade_scope``.
* Create/update eligibility failures map to ``trade_not_allowed``.
* ``trade_revision`` is monotonic within one ``service_generation`` and starts
  at ``0`` for an empty repository.

The transport (envelope ``revision`` used for delivery ordering) is owned by
the injected :class:`TradeEventPublisher`; this layer owns only the
trade-scoped ``trade_revision`` and the snapshot contents. Fee calculation is
not performed here: the user-confirmed fee is persisted verbatim, so changing a
fee plan never retroactively alters a historical trade.
"""

from __future__ import annotations

from threading import Lock
from typing import Any, Protocol

from .models import TradeRecord, TradeValidationError
from .service import TradeEligibilityError, TradeService


class TradeEventPublisher(Protocol):
    """Transport port that publishes a real ``trades_changed`` event envelope.

    The concrete implementation (owned by the formal service transport) assigns
    the envelope ``revision`` and ``service_generation`` and delivers the
    envelope to connected renderers. This layer supplies the trade-scoped
    ``trade_revision``, the explicit ``symbol`` / ``trade_date`` scope, and the
    scoped snapshot contents.
    """

    def publish_trades_changed(
        self,
        *,
        service_generation: int,
        trade_revision: int,
        trades: list[dict[str, Any]],
        symbol: str,
        trade_date: str,
        operation_id: str | None = None,
    ) -> None:
        """Publish one authoritative scoped real ``trades_changed`` envelope."""


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


def _map_eligibility_error(_error: TradeEligibilityError) -> dict[str, Any]:
    """Map a :class:`TradeEligibilityError` to ``trade_not_allowed``.

    Issue #163: index, not-found, and eligibility that cannot be established
    (including service failures) all fail closed as
    ``trade_not_allowed`` / ``invalid_request`` / ``retryable: false``.
    """

    return _error_def(
        "trade_not_allowed", category="invalid_request", retryable=False
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
    reflects the persisted truth for one explicit scope. ``trade_revision`` is
    the only in-memory state, and it is monotonic within the
    ``service_generation``.
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
        authoritative scoped ``trades_changed`` event(s) have already been
        published.
        """
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = "missing-request-id"
        payload = request.get("payload")
        if not isinstance(payload, dict):
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交命令负载无效",
                retryable=False,
            )

        try:
            if command == "list_trades":
                return self._list_trades(request_id, payload)
            if command == "list_trade_history":
                return self._list_trade_history(request_id, payload)
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
            request_id,
            "invalid_trade_request",
            "validation",
            f"未知成交命令：{command}",
            retryable=False,
        )

    # -- Command handlers ------------------------------------------------

    def _list_trades(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        rejected = self._reject_if_not_real_scope(request_id, payload.get("trade_scope"))
        if rejected is not None:
            return rejected
        symbol = payload.get("symbol")
        trade_date = payload.get("trade_date")
        if not isinstance(symbol, str) or not symbol:
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "标的代码缺失",
                retryable=False,
            )
        if not isinstance(trade_date, str) or not trade_date:
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "交易日期缺失",
                retryable=False,
            )
        with self._lock:
            if self._eligibility_is_index(symbol):
                trades: list[dict[str, Any]] = []
            else:
                trades = self._scoped_snapshot(symbol, trade_date)
            revision = self._trade_revision
        self._publish(revision, trades, symbol=symbol, trade_date=trade_date)
        return self._accepted(request_id)

    def _list_trade_history(
        self, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        rejected = self._reject_if_not_real_scope(request_id, payload.get("trade_scope"))
        if rejected is not None:
            return rejected
        with self._lock:
            trades = [record.to_dict() for record in self._service.list_all_trades()]
            revision = self._trade_revision
        return self._accepted(
            request_id,
            data={"trade_revision": revision, "trades": trades},
        )

    def _create_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_payload = payload.get("trade")
        if not isinstance(trade_payload, dict):
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交记录缺失",
                retryable=False,
            )
        rejected = self._reject_if_not_real_scope(
            request_id, trade_payload.get("trade_scope")
        )
        if rejected is not None:
            return rejected
        with self._lock:
            record = self._service.create_trade(trade_payload)
            self._trade_revision += 1
            symbol = record.trade.symbol
            trade_date = self._trade_date_of(record)
            trades = self._scoped_snapshot(symbol, trade_date)
            revision = self._trade_revision
        self._publish(revision, trades, symbol=symbol, trade_date=trade_date)
        return self._accepted(request_id)

    def _update_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_id = payload.get("trade_id")
        trade_payload = payload.get("trade")
        if not isinstance(trade_id, str) or not trade_id:
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交记录标识缺失",
                retryable=False,
            )
        if not isinstance(trade_payload, dict):
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交记录缺失",
                retryable=False,
            )
        rejected = self._reject_if_not_real_scope(
            request_id, trade_payload.get("trade_scope")
        )
        if rejected is not None:
            return rejected
        publishes: list[tuple[int, list[dict[str, Any]], str, str]] = []
        with self._lock:
            old = self._service.get_trade(trade_id)
            if old is None:
                raise _TradeMissing(trade_id)
            old_symbol = old.trade.symbol
            old_date = self._trade_date_of(old)
            updated = self._service.update_trade(trade_id, trade_payload)
            new_symbol = updated.trade.symbol
            new_date = self._trade_date_of(updated)
            if (old_symbol, old_date) != (new_symbol, new_date):
                self._trade_revision += 1
                publishes.append(
                    (
                        self._trade_revision,
                        self._scoped_snapshot(old_symbol, old_date),
                        old_symbol,
                        old_date,
                    )
                )
                self._trade_revision += 1
                publishes.append(
                    (
                        self._trade_revision,
                        self._scoped_snapshot(new_symbol, new_date),
                        new_symbol,
                        new_date,
                    )
                )
            else:
                self._trade_revision += 1
                publishes.append(
                    (
                        self._trade_revision,
                        self._scoped_snapshot(new_symbol, new_date),
                        new_symbol,
                        new_date,
                    )
                )
        for revision, trades, symbol, trade_date in publishes:
            self._publish(revision, trades, symbol=symbol, trade_date=trade_date)
        return self._accepted(request_id)

    def _delete_trade(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        trade_id = payload.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交记录标识缺失",
                retryable=False,
            )
        rejected = self._reject_if_not_real_scope(request_id, payload.get("trade_scope"))
        if rejected is not None:
            return rejected
        with self._lock:
            existing = self._service.get_trade(trade_id)
            if existing is None:
                raise _TradeMissing(trade_id)
            symbol = existing.trade.symbol
            trade_date = self._trade_date_of(existing)
            deleted = self._service.delete_trade(trade_id)
            if not deleted:
                raise _TradeMissing(trade_id)
            self._trade_revision += 1
            trades = self._scoped_snapshot(symbol, trade_date)
            revision = self._trade_revision
        self._publish(revision, trades, symbol=symbol, trade_date=trade_date)
        return self._accepted(request_id)

    # -- Helpers ---------------------------------------------------------

    def _scoped_snapshot(self, symbol: str, trade_date: str) -> list[dict[str, Any]]:
        """Read the scoped real-trade snapshot for one symbol and trade date."""

        return [
            record.to_dict()
            for record in self._service.list_trades(symbol, trade_date)
        ]

    @staticmethod
    def _trade_date_of(record: TradeRecord) -> str:
        """Return the ISO trade date (YYYY-MM-DD) for a persisted record."""

        return record.trade.executed_at.date().isoformat()

    def _eligibility_is_index(self, symbol: str) -> bool:
        """Return True when eligibility resolves the symbol as an index."""

        try:
            status = self._service._eligibility.check_eligibility(symbol)
        except Exception:
            return False
        return status == "index"

    def _reject_if_not_real_scope(
        self, request_id: str, trade_scope: Any
    ) -> dict[str, Any] | None:
        if trade_scope == "simulated":
            return self._reject(
                request_id,
                "unsupported_trade_scope",
                "invalid_request",
                "模拟成交范围已停用",
                retryable=False,
            )
        if trade_scope != "real":
            return self._reject(
                request_id,
                "invalid_trade_request",
                "validation",
                "成交范围无效",
                retryable=False,
            )
        return None

    def _publish(
        self,
        trade_revision: int,
        trades: list[dict[str, Any]],
        *,
        symbol: str,
        trade_date: str,
    ) -> None:
        if self._publisher is None:
            return
        self._publisher.publish_trades_changed(
            service_generation=self._service_generation,
            trade_revision=trade_revision,
            trades=trades,
            symbol=symbol,
            trade_date=trade_date,
        )

    def _accepted(
        self, request_id: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": data,
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
        # Issue #163: all eligibility failures collapse to trade_not_allowed.
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
