"""Static historical workbench snapshot builder.

Provides a transport-free way to build a complete ``workbench_snapshot`` for a
past trading day. Unlike Replay, the result is read-only: it has no playback
cursor, no session lifecycle, and no incremental updates.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

from packages.marketdata.services.market_context_service import (
    MarketContextService,
)

from .pipeline import CzscAnalyzerPort, WorkbenchPipeline
from .replay_data import (
    ReplayDataPreparator,
    ReplayDataUnavailableError,
    ReplayPreparationConfig,
)
from .workbench_projection import SessionProjectionInput, build_workbench_projection


class HistoricalSnapshotError(RuntimeError):
    """Raised when a static historical snapshot cannot be built."""


class HistoricalDataUnavailableError(HistoricalSnapshotError):
    """Raised when market data for the requested historical day is missing."""


def build_historical_snapshot(
    symbol: str,
    trade_date: date | str,
    market_data: Any,
    market_context: MarketContextService,
    *,
    analyzer: CzscAnalyzerPort | None = None,
    deadline_seconds: float = 8.0,
) -> dict[str, Any]:
    """Build a complete, static ``workbench_snapshot`` for a historical day.

    Args:
        symbol: canonical symbol, e.g. ``sh.600000``.
        trade_date: trading date as ``date`` or ``YYYY-MM-DD`` string.
        market_data: a port satisfying ``ReplayMarketDataPort`` (typically a
            ``KLineDataService`` instance).
        market_context: authoritative trading calendar for the market.
        analyzer: optional CZSC analyzer; when ``None`` the pipeline default
            (closed-5m ``packages.chantheory.analyze`` wrapper) is used.
        deadline_seconds: absolute monotonic deadline for data preparation.

    Returns:
        A validated ``workbench_snapshot`` dictionary with
        ``session.session_type == "historical"``.

    Raises:
        HistoricalDataUnavailableError: when neither 1m nor official 5m data is
            reliable for the requested date.
        HistoricalSnapshotError: for validation or pipeline failures.
    """

    preparator = ReplayDataPreparator(
        market_data=market_data,
        market_context=market_context,
        clock=time.monotonic,
    )
    config = ReplayPreparationConfig(
        deadline_monotonic=time.monotonic() + deadline_seconds,
    )

    try:
        prepared = preparator.prepare(
            symbol,
            trade_date,
            config=config,
        )
    except ReplayDataUnavailableError as exc:
        raise HistoricalDataUnavailableError(str(exc)) from exc
    except Exception as exc:
        raise HistoricalSnapshotError(
            f"failed to prepare historical snapshot for {symbol} {trade_date}"
        ) from exc

    pipeline = WorkbenchPipeline(
        session=prepared.market_session,
        market_input_port=prepared.market_input_port,
        analyzer=analyzer,
    )
    try:
        result = pipeline.preview(prepared.market_session.end)
    except Exception as exc:
        raise HistoricalSnapshotError(
            f"pipeline computation failed for {symbol} {trade_date}"
        ) from exc

    session = SessionProjectionInput(
        session_id=f"historical:{prepared.symbol}:{prepared.trade_date}",
        session_type="historical",
        symbol=prepared.symbol,
        trade_date=prepared.trade_date,
        state="ready",
        revision=0,
    )

    projection = build_workbench_projection(result, session)
    return projection.to_dict()
