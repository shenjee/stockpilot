"""Backend command API for static historical workbench snapshots.

``HistoricalSnapshotApi`` is the transport-independent boundary for the
``get_historical_snapshot`` App v1 command. It validates the request, builds
(or reuses) the market-data services, runs the historical snapshot builder,
and returns a frozen ``command_response`` payload.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol

from packages.marketdata.calendar_query import build_market_context_from_trading_calendar
from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.repositories.securities_store import SecuritiesStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services import SecuritiesSearchService
from packages.marketdata.services.kline_data_service import KLineDataService
from packages.marketdata.services.market_context_service import MarketContextService
from packages.marketdata.trading_calendar import CalendarUnavailableError, TradingCalendar
from packages.t0assistant.runtime import (
    HistoricalDataUnavailableError,
    HistoricalSnapshotError,
    build_historical_snapshot,
)


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")
_TRADE_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _error_category(error_code: str) -> str:
    """Map historical snapshot error codes to contract categories."""
    if error_code == "invalid_request":
        return "validation"
    if error_code == "historical_data_unavailable":
        return "data"
    if error_code == "service_unavailable":
        return "service"
    # Unknown runtime failures should not be labelled as validation errors.
    return "service"


def _build_market_context(
    provider: TencentStockDataProvider,
    store: KLineStore,
    today: date,
) -> MarketContextService:
    """Build an authoritative calendar from bundled TradingCalendar JSON (#133).

    The calendar's holiday JSON is the single source of truth for which days
    are trading days.  No benchmark probe or cached-date scaffold is needed.
    """

    del provider, store, today  # no I/O; calendar JSON is bundled
    return build_market_context_from_trading_calendar(TradingCalendar(), "sh")


def _build_live_market_context(
    provider: TencentStockDataProvider,
    store: KLineStore,
    today: date,
) -> MarketContextService:
    """Build the Live calendar from bundled TradingCalendar JSON (#133).

    Same authoritative source as :func:`_build_market_context`; Live no longer
    needs a separate evidence-based scaffold.
    """

    del provider, store, today  # no I/O; calendar JSON is bundled
    return build_market_context_from_trading_calendar(TradingCalendar(), "sh")


def _ensure_context_covers(
    context: MarketContextService,
    trade_date: date,
) -> MarketContextService:
    """Return a context whose coverage window includes ``trade_date``.

    The calendar's coverage window is materialised from the bundled yearly
    holiday JSON.  A date outside that window means the corresponding year's
    JSON is missing: the calendar must **not** fabricate weekday trading days
    for an unknown year (a holiday would be misreported as a trading day).
    Instead this raises :class:`CalendarUnavailableError` so the caller can
    surface a clear ``calendar year missing`` failure rather than silently
    trusting made-up data.
    """

    start = context.coverage_start
    end = context.coverage_end
    if start <= trade_date <= end:
        return context
    raise CalendarUnavailableError(
        f"trade_date {trade_date.isoformat()} is outside calendar coverage "
        f"({start.isoformat()}..{end.isoformat()}); the year's holiday JSON "
        f"is missing and the calendar will not fabricate trading days"
    )


class HistoricalSnapshotApiPort(Protocol):
    """Transport-independent historical snapshot command boundary."""

    def get_historical_snapshot(
        self,
        *,
        request_id: str,
        symbol: str,
        trade_date: str,
    ) -> dict[str, Any]:
        """Return a ``command_response`` payload for ``get_historical_snapshot``."""


class HistoricalSnapshotApi:
    """Concrete historical snapshot API backed by the local kline store."""

    def __init__(
        self,
        *,
        service_generation: int,
        store: KLineStore,
        provider: TencentStockDataProvider,
        market_context: MarketContextService,
        resolve_security: Callable[[str], Any] | None = None,
    ) -> None:
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        if not isinstance(market_context, MarketContextService):
            raise TypeError("market_context must be a MarketContextService")
        self._service_generation = service_generation
        self._store = store
        self._provider = provider
        self._market_context = market_context
        self._resolve_security = resolve_security

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def _market_data(
        self,
        market_context: MarketContextService | None = None,
    ) -> KLineDataService:
        """Build a ``KLineDataService`` wired to the local store and provider."""

        return KLineDataService(
            provider=self._provider,
            store=self._store,
            market_context=market_context or self._market_context,
        )

    def get_historical_snapshot(
        self,
        *,
        request_id: str,
        symbol: str,
        trade_date: str,
    ) -> dict[str, Any]:
        """Dispatch ``get_historical_snapshot`` and return a command response."""

        if not _SYMBOL_PATTERN.fullmatch(symbol):
            return self._reject(
                request_id,
                "invalid_request",
                "symbol must use canonical sh.###### or sz.######",
            )
        if not _TRADE_DATE_PATTERN.fullmatch(trade_date):
            return self._reject(
                request_id,
                "invalid_request",
                "trade_date must use YYYY-MM-DD",
            )

        try:
            resolved_trade_date = date.fromisoformat(trade_date)
        except ValueError:
            return self._reject(
                request_id,
                "invalid_request",
                "trade_date must be a valid calendar date",
            )

        try:
            effective_context = _ensure_context_covers(
                self._market_context,
                resolved_trade_date,
            )
            instrument_type: str | None = None
            if self._resolve_security is not None:
                identity = self._resolve_security(symbol)
                if identity is not None:
                    instrument_type = str(identity.instrument_type)
            snapshot = build_historical_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                market_data=self._market_data(effective_context),
                market_context=effective_context,
                instrument_type=instrument_type,
            )
        except CalendarUnavailableError as exc:
            return self._reject(
                request_id,
                "historical_data_unavailable",
                str(exc),
                retryable=True,
            )
        except HistoricalDataUnavailableError as exc:
            return self._reject(
                request_id,
                "historical_data_unavailable",
                str(exc),
                retryable=True,
            )
        except HistoricalSnapshotError as exc:
            return self._reject(
                request_id,
                "service_unavailable",
                str(exc),
                retryable=True,
            )
        except Exception as exc:
            return self._reject(
                request_id,
                "service_unavailable",
                f"historical snapshot failed: {exc}",
                retryable=True,
            )

        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": snapshot,
            "error": None,
        }

    def _reject(
        self,
        request_id: str,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": False,
            "operation_id": None,
            "data": None,
            "error": {
                "error_code": error_code,
                "category": _error_category(error_code),
                "severity": "error",
                "retryable": retryable,
                "affected_capability": "historical_chart",
                "message": message,
                "request_id": request_id,
                "details": {},
            },
        }


def create_historical_snapshot_api(
    service_generation: int,
    *,
    db_path: Path | None = None,
    provider: TencentStockDataProvider | None = None,
    clock: Callable[[], date] | None = None,
) -> HistoricalSnapshotApi:
    """Factory for the real historical snapshot API used by ``service.py``."""

    paths = RuntimePaths()
    paths.ensure_dirs()
    db_path = db_path or paths.db_dir / "market_data.sqlite"
    store = KLineStore(db_path)
    resolved_provider = provider or TencentStockDataProvider()
    today = (clock or date.today)()
    market_context = _build_market_context(resolved_provider, store, today)
    securities = SecuritiesSearchService(SecuritiesStore(db_path))
    return HistoricalSnapshotApi(
        service_generation=service_generation,
        store=store,
        provider=resolved_provider,
        market_context=market_context,
        resolve_security=securities.resolve,
    )


__all__ = [
    "HistoricalSnapshotApi",
    "HistoricalSnapshotApiPort",
    "create_historical_snapshot_api",
]
