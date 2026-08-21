"""Renderer-agnostic projection of trade records onto 5-minute chart markers.

The projection is the authoritative Python-side source of the data a chart
needs to draw a trade: the inclusive five-minute bucket that owns the trade
(``bucket_start``), the actual execution price used as the y-coordinate, the
direction, and the lot count. It deliberately carries **no** rendering
concerns - no color, no marker shape, no Unix timestamp - so any renderer
(React/Lightweight Charts today, another surface later) can map the values to
its own visual style. This is the ``Trade Service`` projection port referenced
by ``module_design.md`` §5.6.

``TradeScope.SIMULATED`` remains only for recognizing legacy payloads; runtime
trade commands reject simulated scope (Issue #163). Sorting is stable and
mirrors the renderer-side projection in
``apps/t0-assistant/renderer/.../trade-markers.mjs`` so the two layers never
disagree about marker order within a 5m bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Sequence

from .models import TradeRecord, TradeScope, TradeSide

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: A-share and ETF board lot size. A trade's quantity is recorded in shares;
#: one lot is 100 shares. The PRD label convention is "direction + lots"
#: (e.g. ``B2`` = buy 2 lots = 200 shares).
SHARES_PER_LOT = 100


class TradeMarkerValidationError(ValueError):
    """A stable failure raised when a trade record cannot be projected."""


def lot_count(quantity: int) -> Decimal:
    """Return the number of lots for a share quantity.

    ``Decimal`` is used so fractional lots (1-99 shares) stay exact instead of
    inheriting binary-float error.
    """

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TradeMarkerValidationError("quantity", "must be a positive integer")
    if quantity < 1:
        raise TradeMarkerValidationError("quantity", "must be a positive integer")
    return Decimal(quantity) / Decimal(SHARES_PER_LOT)


def format_lot_label(quantity: int) -> str:
    """Format a share quantity as a lot label without trailing zeros.

    Whole lots render as an integer (``2``); fractional lots keep up to two
    decimals with trailing zeros stripped (``0.5``, ``1.25``). This matches the
    renderer-side ``formatLotLabel`` so the two layers produce identical text.
    """

    lots = lot_count(quantity)
    if lots == lots.to_integral_value():
        return str(int(lots))
    return str(lots.quantize(Decimal("0.01")).normalize())


@dataclass(frozen=True, slots=True)
class TradeMarker:
    """Renderer-agnostic chart marker projected from a persisted trade.

    Carries only the data a chart needs: the owning 5m bucket, the execution
    price, the direction, the share quantity and the lot label. It never
    carries color, shape or a Unix timestamp - those belong to the renderer.
    """

    trade_id: str
    trade_scope: TradeScope
    bucket_start: datetime
    price: Decimal
    side: TradeSide
    quantity: int
    lots: Decimal
    label: str

    def to_dict(self) -> dict[str, object]:
        """Serialize to the process-neutral marker payload shape."""

        return {
            "trade_id": self.trade_id,
            "trade_scope": self.trade_scope.value,
            "bucket_start": self.bucket_start.strftime(_TIMESTAMP_FORMAT),
            "price": float(self.price),
            "side": self.side.value,
            "quantity": self.quantity,
            "lots": float(self.lots),
            "label": self.label,
        }


class TradeMarkerProjection(Protocol):
    """Port that projects trade records into renderer-agnostic markers."""

    def project(
        self, trades: Sequence[TradeRecord]
    ) -> tuple[TradeMarker, ...]: ...


def _side_order(side: TradeSide) -> int:
    """Buy sorts before sell within the same 5m bucket."""

    return 0 if side is TradeSide.BUY else 1


def project_trade_marker(trade: TradeRecord) -> TradeMarker:
    """Project a single validated trade record into a chart marker.

    A ``TradeRecord`` is already domain-validated, so this only derives the
    marker fields. Invalid input types raise rather than silently producing a
    marker at the wrong coordinates.
    """

    if not isinstance(trade, TradeRecord):
        raise TradeMarkerValidationError("trade", "must be a TradeRecord")

    quantity = trade.trade.quantity
    lots = lot_count(quantity)
    prefix = "B" if trade.trade.side is TradeSide.BUY else "S"

    return TradeMarker(
        trade_id=trade.trade_id,
        trade_scope=trade.trade.trade_scope,
        bucket_start=trade.bucket_start,
        price=trade.trade.price,
        side=trade.trade.side,
        quantity=quantity,
        lots=lots,
        label=f"{prefix}{format_lot_label(quantity)}",
    )


class TradeMarkerProjector:
    """Default ``TradeMarkerProjection`` implementation.

    Projects each trade (skipping ``None`` entries) and returns a tuple in a
    stable order: bucket time ascending, then buy before sell, then price
    ascending, then ``trade_id`` ascending. The order is identical to the
    renderer-side projection so multiple trades in one 5m bucket are drawn
    predictably and tests are deterministic.
    """

    def project(self, trades: Sequence[TradeRecord]) -> tuple[TradeMarker, ...]:
        if isinstance(trades, TradeRecord):
            records: tuple[TradeRecord, ...] = (trades,)
        else:
            records = tuple(trades)

        markers = [project_trade_marker(trade) for trade in records if trade is not None]

        markers.sort(
            key=lambda marker: (
                marker.bucket_start,
                _side_order(marker.side),
                marker.price,
                marker.trade_id,
            )
        )
        return tuple(markers)


def project_trade_markers(
    trades: Sequence[TradeRecord],
    *,
    projector: TradeMarkerProjection | None = None,
) -> tuple[TradeMarker, ...]:
    """Convenience entry point projecting trades through an optional port."""

    projection = projector or TradeMarkerProjector()
    return projection.project(trades)
