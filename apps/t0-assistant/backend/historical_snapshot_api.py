"""Backend command API for static historical workbench snapshots.

``HistoricalSnapshotApi`` is the transport-independent boundary for the
``get_historical_snapshot`` App v1 command. It validates the request, builds
(or reuses) the market-data services, runs the historical snapshot builder,
and returns a frozen ``command_response`` payload.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from packages.marketdata.market_data import TencentStockDataProvider
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.runtime_paths import RuntimePaths
from packages.marketdata.services.kline_data_service import KLineDataService
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import (
    HistoricalDataUnavailableError,
    HistoricalSnapshotError,
    build_historical_snapshot,
)


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")
_TRADE_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_BENCHMARK_CODE = "000001"
_BENCHMARK_MARKET = "sh"
_BENCHMARK_LOOKBACK_DAYS = 730


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


def _weekday_dates(start: date, end: date) -> list[date]:
    """Return every weekday in the inclusive date range."""

    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


def _benchmark_cached_dates(store: KLineStore) -> list[str]:
    """Return cached benchmark (or any) trade dates without network I/O."""

    cached_dates = store.trade_dates(_BENCHMARK_CODE, market=_BENCHMARK_MARKET)
    if not cached_dates:
        cached_dates = store.all_trade_dates()
    return list(cached_dates)


def _build_market_context(
    provider: TencentStockDataProvider,
    store: KLineStore,
    today: date,
) -> MarketContextService:
    """Build a market-wide calendar that does not depend on any single security.

    Cached benchmark index (``sh.000001``) bars are the preferred evidence of
    which days the exchange was open, but a stale cache must not make a newer
    weekday appear to be a holiday.  Therefore the generated weekday calendar
    for the coverage window is always the base set, and cached dates are merged
    into it as known-good trading days.  Holidays then degrade cleanly to
    ``historical_data_unavailable`` when the provider cannot supply bars.

    This synchronous path intentionally avoids network I/O so service startup
    stays fast and reliable.  Live must not use this builder — see
    :func:`_build_live_market_context`.
    """

    coverage_end = today
    coverage_start = today - timedelta(days=_BENCHMARK_LOOKBACK_DAYS)

    cached_dates = _benchmark_cached_dates(store)

    if cached_dates:
        first_cached = date.fromisoformat(cached_dates[0])
        coverage_start = min(coverage_start, first_cached)
        cached_day_set = {date.fromisoformat(value) for value in cached_dates}
    else:
        cached_day_set = set()

    weekday_day_set = set(_weekday_dates(coverage_start, coverage_end))
    trading_days = sorted(cached_day_set | weekday_day_set)

    return MarketContextService(
        trading_days=[value.isoformat() for value in trading_days],
        coverage_start=trading_days[0].isoformat(),
        coverage_end=coverage_end.isoformat(),
    )


def _build_live_market_context(
    provider: TencentStockDataProvider,
    store: KLineStore,
    today: date,
) -> tuple[MarketContextService, date]:
    """Build Live calendar evidence without synthesizing weekday opens.

    Unlike :func:`_build_market_context`, this does **not** union every weekday
    into ``trading_days``.  Working-day holidays (National Day, Spring Festival,
    etc.) therefore stay closed when absent from the benchmark cache, so Live
    Market View can fall back to the previous trading day (#130).

    Returns ``(context, authoritative_through)``.  ``authoritative_through`` is
    the last cached open day when evidence exists; weekdays after that bound
    are ``day_status=unknown`` for the Calendar adapter.
    """

    del provider  # reserved for future live calendar enrichment; no I/O here
    coverage_end = today
    coverage_start = today - timedelta(days=_BENCHMARK_LOOKBACK_DAYS)
    cached_dates = _benchmark_cached_dates(store)

    if cached_dates:
        cached_day_set = {date.fromisoformat(value) for value in cached_dates}
        coverage_start = min(coverage_start, min(cached_day_set))
        trading_days = sorted(cached_day_set)
        authoritative_through = trading_days[-1]
    else:
        # Cold start with no benchmark evidence: weekday best-effort only.
        trading_days = _weekday_dates(coverage_start, coverage_end)
        authoritative_through = coverage_end

    return (
        MarketContextService(
            trading_days=[value.isoformat() for value in trading_days],
            coverage_start=trading_days[0].isoformat(),
            coverage_end=coverage_end.isoformat(),
        ),
        authoritative_through,
    )


def _ensure_context_covers(
    context: MarketContextService,
    trade_date: date,
) -> MarketContextService:
    """Return a context whose coverage window includes ``trade_date``.

    The initial coverage window is intentionally bounded so service startup
    stays fast.  When a user requests a historical date outside that window,
    the calendar is extended on demand using generated weekdays.  This keeps
    the fixed 730-day lookback from becoming a hard limit on historical chart
    range, while still avoiding network I/O during startup.
    """

    start = context.coverage_start
    end = context.coverage_end
    if start <= trade_date <= end:
        return context
    new_start = min(start, trade_date)
    new_end = max(end, trade_date)
    trading_days = sorted(
        set(context.trading_days) | set(_weekday_dates(new_start, new_end))
    )
    return MarketContextService(
        trading_days=[value.isoformat() for value in trading_days],
        coverage_start=new_start.isoformat(),
        coverage_end=new_end.isoformat(),
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

        effective_context = _ensure_context_covers(
            self._market_context,
            resolved_trade_date,
        )

        try:
            snapshot = build_historical_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                market_data=self._market_data(effective_context),
                market_context=effective_context,
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
            "schema_version": "t0_app_v1",
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
            "schema_version": "t0_app_v1",
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
    store = KLineStore(db_path or paths.db_dir / "market_data.sqlite")
    resolved_provider = provider or TencentStockDataProvider()
    today = (clock or date.today)()
    market_context = _build_market_context(resolved_provider, store, today)
    return HistoricalSnapshotApi(
        service_generation=service_generation,
        store=store,
        provider=resolved_provider,
        market_context=market_context,
    )


__all__ = [
    "HistoricalSnapshotApi",
    "HistoricalSnapshotApiPort",
    "create_historical_snapshot_api",
]
