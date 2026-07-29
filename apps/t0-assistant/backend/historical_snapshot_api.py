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


def _build_market_context(
    provider: TencentStockDataProvider,
    store: KLineStore,
    today: date,
) -> MarketContextService:
    """Build a market-wide calendar that does not depend on any single security.

    The preferred source is cached benchmark index (``sh.000001``) bars, because
    the benchmark trades on nearly every open market day.  If the benchmark is
    not cached, fall back to the union of all cached bars.  When the store is
    completely empty, use a generated weekday calendar so the API remains
    usable; holidays then degrade cleanly to ``historical_data_unavailable``
    when the provider cannot supply bars.

    This synchronous path intentionally avoids network I/O so service startup
    stays fast and reliable.
    """

    coverage_end = today
    coverage_start = today - timedelta(days=_BENCHMARK_LOOKBACK_DAYS)

    dates = store.trade_dates(_BENCHMARK_CODE, market=_BENCHMARK_MARKET)
    if not dates:
        dates = store.all_trade_dates()
    if dates:
        return MarketContextService(
            trading_days=dates,
            coverage_start=dates[0],
            coverage_end=coverage_end.isoformat(),
        )

    weekday_dates = [
        (coverage_start + timedelta(days=offset)).isoformat()
        for offset in range((coverage_end - coverage_start).days + 1)
        if (coverage_start + timedelta(days=offset)).weekday() < 5
    ]
    return MarketContextService(
        trading_days=weekday_dates,
        coverage_start=weekday_dates[0],
        coverage_end=coverage_end.isoformat(),
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

    def _market_data(self) -> KLineDataService:
        """Build a ``KLineDataService`` wired to the local store and provider."""

        return KLineDataService(
            provider=self._provider,
            store=self._store,
            market_context=self._market_context,
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
            snapshot = build_historical_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                market_data=self._market_data(),
                market_context=self._market_context,
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
