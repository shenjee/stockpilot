from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest

from packages.t0assistant.trading import (
    SHARES_PER_LOT,
    TradeDraft,
    TradeMarker,
    TradeMarkerProjector,
    TradeMarkerValidationError,
    TradeRecord,
    TradeScope,
    format_lot_label,
    lot_count,
    project_trade_marker,
    project_trade_markers,
)


def _record(
    trade_id: str = "trade-1",
    *,
    trade_scope: str = "real",
    symbol: str = "sh.600584",
    side: str = "buy",
    executed_at: str = "2026-07-24 10:03:47",
    price: Decimal | float = Decimal("38.25"),
    quantity: int = 200,
    fee: Decimal | float | None = Decimal("5.01"),
    note: str = "manual fill",
    fee_plan_id: str | None = "shenwan-hongyuan",
) -> TradeRecord:
    return TradeRecord(
        trade_id,
        TradeDraft.from_mapping(
            {
                "trade_scope": trade_scope,
                "symbol": symbol,
                "side": side,
                "executed_at": executed_at,
                "price": price,
                "quantity": quantity,
                "fee": fee,
                "note": note,
                "fee_plan_id": fee_plan_id,
            }
        ),
    )


class LotLabelTests(unittest.TestCase):
    def test_shares_per_lot_is_one_hundred(self) -> None:
        self.assertEqual(SHARES_PER_LOT, 100)

    def test_whole_lots_render_without_decimals(self) -> None:
        self.assertEqual(format_lot_label(100), "1")
        self.assertEqual(format_lot_label(200), "2")
        self.assertEqual(format_lot_label(1000), "10")

    def test_fractional_lots_strip_trailing_zeros(self) -> None:
        self.assertEqual(format_lot_label(50), "0.5")
        self.assertEqual(format_lot_label(125), "1.25")
        self.assertEqual(format_lot_label(5), "0.05")
        self.assertEqual(format_lot_label(10), "0.1")

    def test_lot_count_is_exact_decimal(self) -> None:
        self.assertEqual(lot_count(200), Decimal("2"))
        self.assertEqual(lot_count(50), Decimal("0.5"))
        self.assertEqual(lot_count(125), Decimal("1.25"))

    def test_lot_helpers_reject_non_positive(self) -> None:
        for bad in (0, -100, True):
            with self.assertRaises(TradeMarkerValidationError):
                lot_count(bad)
            with self.assertRaises(TradeMarkerValidationError):
                format_lot_label(bad)


class ProjectTradeMarkerTests(unittest.TestCase):
    def test_projects_bucket_price_side_quantity_and_lots(self) -> None:
        marker = project_trade_marker(_record(quantity=200, side="buy"))

        self.assertEqual(marker.trade_id, "trade-1")
        self.assertEqual(marker.trade_scope, TradeScope.REAL)
        self.assertEqual(marker.bucket_start, datetime(2026, 7, 24, 10, 0, 0))
        self.assertEqual(marker.price, Decimal("38.25"))
        self.assertEqual(marker.side.value, "buy")
        self.assertEqual(marker.quantity, 200)
        self.assertEqual(marker.lots, Decimal("2"))
        self.assertEqual(marker.label, "B2")

    def test_bucket_floors_to_five_minute_inclusive_start(self) -> None:
        for executed_at, expected in (
            ("2026-07-24 09:31:12", "2026-07-24 09:30:00"),
            ("2026-07-24 10:04:59", "2026-07-24 10:00:00"),
            ("2026-07-24 10:05:00", "2026-07-24 10:05:00"),
            ("2026-07-24 14:57:30", "2026-07-24 14:55:00"),
        ):
            marker = project_trade_marker(_record(executed_at=executed_at))
            self.assertEqual(marker.bucket_start, datetime.fromisoformat(expected))

    def test_sell_label_uses_s_prefix(self) -> None:
        marker = project_trade_marker(_record(side="sell", quantity=300))
        self.assertEqual(marker.label, "S3")
        self.assertEqual(marker.side.value, "sell")

    def test_simulated_scope_passes_through(self) -> None:
        marker = project_trade_marker(_record(trade_scope="simulated"))
        self.assertEqual(marker.trade_scope, TradeScope.SIMULATED)

    def test_to_dict_is_renderer_agnostic(self) -> None:
        """The marker payload carries no color, shape, or Unix timestamp."""

        payload = project_trade_marker(_record()).to_dict()
        self.assertEqual(
            set(payload),
            {
                "trade_id",
                "trade_scope",
                "bucket_start",
                "price",
                "side",
                "quantity",
                "lots",
                "label",
            },
        )
        self.assertEqual(payload["bucket_start"], "2026-07-24 10:00:00")
        self.assertEqual(payload["lots"], 2.0)
        self.assertEqual(payload["label"], "B2")
        # No rendering concerns leak into the payload.
        for renderer_field in ("color", "shape", "time"):
            self.assertNotIn(renderer_field, payload)

    def test_non_record_input_raises(self) -> None:
        with self.assertRaises(TradeMarkerValidationError):
            project_trade_marker({"trade_id": "x"})  # type: ignore[arg-type]


class TradeMarkerProjectorTests(unittest.TestCase):
    def test_empty_input_returns_empty_tuple(self) -> None:
        self.assertEqual(TradeMarkerProjector().project(()), ())

    def test_single_record_wrapped_into_tuple(self) -> None:
        record = _record()
        markers = TradeMarkerProjector().project(record)
        self.assertEqual(len(markers), 1)
        self.assertIsInstance(markers[0], TradeMarker)
        self.assertEqual(markers[0].trade_id, "trade-1")

    def test_stable_order_within_and_across_buckets(self) -> None:
        # Same 10:00 bucket: buy@38.25 (A), buy@38.30 (C), sell@38.25 (B);
        # then a later 10:05 bucket (D).
        a = _record("A", side="buy", executed_at="2026-07-24 10:03:00", price=38.25)
        b = _record("B", side="sell", executed_at="2026-07-24 10:03:00", price=38.25)
        c = _record("C", side="buy", executed_at="2026-07-24 10:03:00", price=38.30)
        d = _record("D", side="buy", executed_at="2026-07-24 10:08:00", price=38.00)

        markers = TradeMarkerProjector().project((d, c, b, a))
        self.assertEqual([m.trade_id for m in markers], ["A", "C", "B", "D"])

    def test_tiebreak_on_trade_id_when_bucket_side_price_match(self) -> None:
        first = _record("zeta", executed_at="2026-07-24 10:03:00")
        second = _record("alpha", executed_at="2026-07-24 10:03:00")
        markers = TradeMarkerProjector().project((first, second))
        self.assertEqual([m.trade_id for m in markers], ["alpha", "zeta"])

    def test_project_trade_markers_convenience_uses_default_projector(self) -> None:
        markers = project_trade_markers([_record("t1"), _record("t2")])
        self.assertEqual(len(markers), 2)

    def test_project_trade_markers_accepts_custom_projector(self) -> None:
        class _Stub:
            def __init__(self) -> None:
                self.received: list[TradeRecord] = []

            def project(self, trades):
                self.received = list(trades)
                return ()

        stub = _Stub()
        project_trade_markers([_record()], projector=stub)
        self.assertEqual(len(stub.received), 1)


if __name__ == "__main__":
    unittest.main()
