"""Application service for real trades: validation, CRUD and marker projection.

The service is the single Python entry point for real-trade facts. It keeps no
in-memory trade cache: every read and write goes through the repository, so a
trade only becomes a fact once the repository confirms persistence. A failed
create/update/delete can therefore never leave the caller with a "memory
success" that was never written to disk - the repository exception propagates
and nothing is cached.

Scope boundary: only real trades are persisted here. Replay-simulated trades
are owned by the Replay Session (see ``module_design.md`` §5.6) and must never
reach this service or the real-trade repository. Fee calculation belongs to
``fee_policy`` and the caller; this service persists the fee the user confirmed
and never recomputes it, so changing a fee plan never retroactively alters a
historical trade.
"""

from __future__ import annotations

from datetime import date
import uuid
from typing import Any, Callable, Mapping, Protocol

from packages.t0assistant.preferences import PreferenceCapability

from .markers import TradeMarker, TradeMarkerProjection, TradeMarkerProjector
from .models import TradeDraft, TradeRecord, TradeScope, TradeValidationError


class _TradeRepository(Protocol):
    """Narrow persistence port used by ``TradeService``."""

    @property
    def capability(self) -> PreferenceCapability: ...

    def create(self, record: TradeRecord) -> TradeRecord: ...

    def get(self, trade_id: str) -> TradeRecord | None: ...

    def list_all(self) -> tuple[TradeRecord, ...]: ...

    def list_for_symbol_and_date(
        self, symbol: str, trade_date: date | str
    ) -> tuple[TradeRecord, ...]: ...

    def update(self, record: TradeRecord) -> TradeRecord: ...

    def delete(self, trade_id: str) -> bool: ...


class InstrumentEligibilityPort(Protocol):
    """Check whether a symbol is eligible for real trading.

    Decouples :class:`TradeService` from the securities-search service
    (issue #151 decision #5).  The App layer injects an adapter that wraps
    :class:`SecuritiesSearchService`; the service itself never imports the
    search service.

    Implementations return one of:

    * ``"tradable"`` – the symbol resolves to a stock or ETF and may be traded.
    * ``"index"`` – the symbol resolves to an index, which is not tradable.
    * ``None`` – the symbol was not found in the securities master.

    A ``service_unavailable`` result indicates the eligibility check itself
    failed (e.g. the securities store is unreachable); callers may treat this
    as a soft failure depending on context.
    """

    def check_eligibility(self, symbol: str) -> str | None: ...
    """Return ``"tradable"``, ``"index"`` or ``None`` for *symbol*."""


class TradeEligibilityError(TradeValidationError):
    """Raised when a trade fails instrument-eligibility validation."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field, message)


class AllowAllEligibility(InstrumentEligibilityPort):
    """Test/replay helper: every symbol is tradable.

    Issue #151 P2 #6: ``TradeService`` now requires an
    :class:`InstrumentEligibilityPort`; tests and replay-simulated flows that
    never touch the real-trade repository can inject this port to preserve the
    old "no eligibility check" behaviour without bypassing the contract.
    """

    def check_eligibility(self, symbol: str) -> str | None:
        return "tradable"


def _default_trade_id() -> str:
    return uuid.uuid4().hex


class TradeService:
    """Real-trade CRUD and marker projection over a persistence port.

    Persistence failures surface as repository exceptions; the service never
    fabricates a successful trade. ``update`` raises the repository's
    ``RepositoryNotFoundError`` when no persisted trade has the id, and
    ``delete`` is permanent - the repository hard deletes (no soft delete, no
    recycle bin, no resurrection).
    """

    def __init__(
        self,
        repository: _TradeRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        marker_projection: TradeMarkerProjection | None = None,
        eligibility: InstrumentEligibilityPort,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory or _default_trade_id
        self._markers = marker_projection or TradeMarkerProjector()
        # Issue #151 P2 #6: eligibility is required and must implement the
        # InstrumentEligibilityPort contract (check_eligibility).  We use a
        # structural hasattr check instead of isinstance because the Protocol
        # is not @runtime_checkable.
        if not callable(getattr(eligibility, "check_eligibility", None)):
            raise TypeError(
                "eligibility must implement InstrumentEligibilityPort "
                "(check_eligibility)"
            )
        self._eligibility = eligibility

    @property
    def capability(self) -> PreferenceCapability:
        return self._repository.capability

    # -- CRUD -----------------------------------------------------------

    def create_trade(self, draft: TradeDraft | Mapping[str, Any]) -> TradeRecord:
        """Validate, assign an identity and persist a new real trade.

        The returned record is a fact only because the repository confirmed
        the write; on any persistence failure the repository exception
        propagates and no record is returned or cached.
        """

        validated = self._coerce_draft(draft)
        self._require_real(validated)
        self._require_eligible(validated)
        record = TradeRecord(self._id_factory(), validated)
        return self._repository.create(record)

    def update_trade(
        self, trade_id: str, draft: TradeDraft | Mapping[str, Any]
    ) -> TradeRecord:
        """Replace a persisted real trade's values, preserving its identity.

        The fee is persisted exactly as supplied and never recomputed from a
        fee plan, so historical trades are not retroactively recalculated.
        Raises ``RepositoryNotFoundError`` if no persisted trade has the id.
        """

        normalized_id = self._require_trade_id(trade_id)
        validated = self._coerce_draft(draft)
        self._require_real(validated)
        self._require_eligible(validated)
        record = TradeRecord(normalized_id, validated)
        return self._repository.update(record)

    def delete_trade(self, trade_id: str) -> bool:
        """Permanently delete a persisted real trade.

        Returns ``True`` when a trade was deleted and ``False`` when no
        persisted trade had ``trade_id``. Deletion is hard and irreversible.
        """

        normalized_id = self._require_trade_id(trade_id)
        return self._repository.delete(normalized_id)

    def get_trade(self, trade_id: str) -> TradeRecord | None:
        """Return the persisted real trade, or ``None`` if it does not exist."""

        normalized_id = self._require_trade_id(trade_id)
        return self._repository.get(normalized_id)

    def list_trades(
        self, symbol: str, trade_date: date | str
    ) -> tuple[TradeRecord, ...]:
        """Return persisted real trades for one symbol on one trading date."""

        return self._repository.list_for_symbol_and_date(symbol, trade_date)

    def list_all_trades(self) -> tuple[TradeRecord, ...]:
        """Return every persisted real trade, ordered by execution time."""

        return self._repository.list_all()

    # -- Marker projection ---------------------------------------------

    def project_markers(
        self, trades: TradeRecord | Mapping[str, Any] | Any
    ) -> tuple[TradeMarker, ...]:
        """Project trade records into renderer-agnostic chart markers.

        Accepts a single ``TradeRecord``, a sequence of records, or a sequence
        of process-neutral ``trade_record`` mappings (as emitted by
        ``TradeRecord.to_dict``) so the backend can project whatever shape it
        currently holds without an extra conversion pass.
        """

        records = _coerce_records(trades)
        return self._markers.project(records)

    def markers_for(
        self, symbol: str, trade_date: date | str
    ) -> tuple[TradeMarker, ...]:
        """Project all persisted real trades for one symbol/date to markers."""

        return self.project_markers(self.list_trades(symbol, trade_date))

    # -- Helpers --------------------------------------------------------

    @staticmethod
    def _coerce_draft(draft: TradeDraft | Mapping[str, Any]) -> TradeDraft:
        if isinstance(draft, TradeDraft):
            return draft
        if isinstance(draft, Mapping):
            return TradeDraft.from_mapping(draft)
        raise TypeError("draft must be a TradeDraft or a mapping")

    @staticmethod
    def _require_real(draft: TradeDraft) -> None:
        if draft.trade_scope is not TradeScope.REAL:
            raise TradeValidationError(
                "trade_scope", "only real trades can be persisted"
            )

    def _require_eligible(self, draft: TradeDraft) -> None:
        """Enforce instrument eligibility before persisting a real trade.

        Issue #151 P2 #6: the eligibility port is required, so the check is
        never bypassed.  Callers that do not need real eligibility (e.g.
        unit tests with a fake repository) must inject an allow-all port.
        """

        try:
            status = self._eligibility.check_eligibility(draft.symbol)
        except Exception as exc:  # noqa: BLE001 - surface as stable error
            raise TradeEligibilityError(
                "symbol", "service_unavailable"
            ) from exc
        if status is None:
            raise TradeEligibilityError("symbol", "security_not_found")
        if status == "index":
            raise TradeEligibilityError("symbol", "security_not_tradable")
        if status != "tradable":
            raise TradeEligibilityError("symbol", "security_not_tradable")

    @staticmethod
    def _require_trade_id(trade_id: str) -> str:
        if not isinstance(trade_id, str) or not trade_id.strip():
            raise TradeValidationError("trade_id", "must not be blank")
        return trade_id.strip()


def _coerce_records(
    trades: TradeRecord | Mapping[str, Any] | Any,
) -> tuple[TradeRecord, ...]:
    """Normalize mixed trade input into a tuple of ``TradeRecord`` values."""

    if isinstance(trades, TradeRecord):
        return (trades,)
    if isinstance(trades, Mapping):
        return (_record_from_mapping(trades),)

    records: list[TradeRecord] = []
    for item in trades:  # type: ignore[arg-type]
        if item is None:
            continue
        if isinstance(item, TradeRecord):
            records.append(item)
        elif isinstance(item, Mapping):
            records.append(_record_from_mapping(item))
        else:
            raise TradeValidationError(
                "trades", "must be TradeRecord or trade_record mapping"
            )
    return tuple(records)


def _record_from_mapping(payload: Mapping[str, Any]) -> TradeRecord:
    """Rebuild a ``TradeRecord`` from a process-neutral ``trade_record`` dict."""

    trade_id = payload.get("trade_id")
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise TradeValidationError("trade_id", "must not be blank")
    return TradeRecord(trade_id.strip(), TradeDraft.from_mapping(payload))
