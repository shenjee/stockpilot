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
from typing import Any, Protocol

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
    ) -> None:
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        self._service_generation = service_generation
        self._store = store
        self._provider = provider
        self._market_context: MarketContextService | None = None

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def _market_context_for(self, symbol: str) -> MarketContextService:
        """Build (and cache) a trading calendar from the local kline store."""

        if self._market_context is not None:
            return self._market_context

        market = symbol[:2]
        code = symbol[3:]
        dates = self._store.trade_dates(code, market)
        if not dates:
            raise HistoricalDataUnavailableError(
                f"no market data available for {symbol}"
            )
        self._market_context = MarketContextService(
            trading_days=dates,
            coverage_start=dates[0],
            coverage_end=dates[-1],
        )
        return self._market_context

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
            market_context = self._market_context_for(symbol)
            snapshot = build_historical_snapshot(
                symbol=symbol,
                trade_date=trade_date,
                market_data=self._market_data(),
                market_context=market_context,
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
                "category": (
                    "data" if error_code == "historical_data_unavailable" else "validation"
                ),
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
) -> HistoricalSnapshotApi:
    """Factory for the real historical snapshot API used by ``service.py``."""

    paths = RuntimePaths()
    paths.ensure_dirs()
    store = KLineStore(db_path or paths.db_dir / "market_data.sqlite")
    provider = TencentStockDataProvider()
    return HistoricalSnapshotApi(
        service_generation=service_generation,
        store=store,
        provider=provider,
    )


__all__ = [
    "HistoricalSnapshotApi",
    "HistoricalSnapshotApiPort",
    "create_historical_snapshot_api",
]
