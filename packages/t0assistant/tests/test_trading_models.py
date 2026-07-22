from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest

from packages.t0assistant.trading import (
    TradeDraft,
    TradeRecord,
    TradeScope,
    TradeSide,
    TradeValidationError,
    bucket_start_for,
    normalize_executed_at,
)


class TradeTimeTests(unittest.TestCase):
    def test_minute_input_is_normalized_to_second_precision(self) -> None:
        self.assertEqual(
            normalize_executed_at("2026-07-22 10:03"),
            datetime(2026, 7, 22, 10, 3, 0),
        )

    def test_second_input_is_preserved(self) -> None:
        self.assertEqual(
            normalize_executed_at("2026-07-22 10:03:47"),
            datetime(2026, 7, 22, 10, 3, 47),
        )

    def test_fractional_or_offset_time_is_rejected(self) -> None:
        for value in (
            "2026-07-22 10:03:47.1",
            "2026-07-22T10:03:47+08:00",
        ):
            with self.subTest(value=value), self.assertRaises(TradeValidationError):
                normalize_executed_at(value)

    def test_five_minute_bucket_uses_inclusive_floor_boundaries(self) -> None:
        cases = {
            "2026-07-22 09:30:00": datetime(2026, 7, 22, 9, 30),
            "2026-07-22 09:34:59": datetime(2026, 7, 22, 9, 30),
            "2026-07-22 09:35:00": datetime(2026, 7, 22, 9, 35),
            "2026-07-22 14:59:59": datetime(2026, 7, 22, 14, 55),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(bucket_start_for(value), expected)


class TradeValueTests(unittest.TestCase):
    def _payload(self, *, scope: str = "real") -> dict[str, object]:
        return {
            "trade_scope": scope,
            "symbol": "sh.600584",
            "side": "buy",
            "executed_at": "2026-07-22 10:03",
            "price": 38.25,
            "quantity": 200,
            "fee": 5,
            "note": "first fill",
            "fee_plan_id": "fee-plan-1",
        }

    def test_real_and_simulated_trades_share_one_value_and_validation(self) -> None:
        real = TradeDraft.from_mapping(self._payload(scope="real"))
        simulated = TradeDraft.from_mapping(self._payload(scope="simulated"))

        self.assertIs(type(real), type(simulated))
        self.assertEqual(real.trade_scope, TradeScope.REAL)
        self.assertEqual(simulated.trade_scope, TradeScope.SIMULATED)
        self.assertEqual(real.side, TradeSide.BUY)
        self.assertEqual(real.price, Decimal("38.25"))
        self.assertEqual(real.executed_at.second, 0)

    def test_record_matches_frozen_transport_shape(self) -> None:
        record = TradeRecord("trade-1", TradeDraft.from_mapping(self._payload()))

        self.assertEqual(
            record.to_dict(),
            {
                "trade_id": "trade-1",
                "bucket_start": "2026-07-22 10:00:00",
                "trade_scope": "real",
                "symbol": "sh.600584",
                "side": "buy",
                "executed_at": "2026-07-22 10:03:00",
                "price": 38.25,
                "quantity": 200,
                "fee": 5.0,
                "note": "first fill",
                "fee_plan_id": "fee-plan-1",
            },
        )

    def test_record_rejects_blank_trade_id(self) -> None:
        trade = TradeDraft.from_mapping(self._payload())

        for trade_id in ("", "   "):
            with self.subTest(trade_id=trade_id), self.assertRaises(
                TradeValidationError
            ) as ctx:
                TradeRecord(trade_id, trade)
            self.assertEqual(ctx.exception.field, "trade_id")

    def test_optional_fee_and_fee_plan_remain_null(self) -> None:
        payload = self._payload()
        payload["fee"] = None
        payload["fee_plan_id"] = None
        trade = TradeDraft.from_mapping(payload)

        self.assertIsNone(trade.fee)
        self.assertIsNone(trade.fee_plan_id)

    def test_direct_construction_cannot_bypass_domain_validation(self) -> None:
        with self.assertRaises(TradeValidationError) as ctx:
            TradeDraft(
                trade_scope=TradeScope.REAL,
                symbol="sh.600584",
                side=TradeSide.BUY,
                executed_at=datetime(2026, 7, 22, 10, 3),
                price=Decimal("38.25"),
                quantity=0,
            )
        self.assertEqual(ctx.exception.field, "quantity")

    def test_mapping_rejects_invalid_executed_at(self) -> None:
        payload = self._payload()
        payload["executed_at"] = "2026-07-22 25:03"

        with self.assertRaises(TradeValidationError) as ctx:
            TradeDraft.from_mapping(payload)
        self.assertEqual(ctx.exception.field, "executed_at")

    def test_invalid_domain_fields_are_rejected(self) -> None:
        invalid_values = {
            "trade_scope": "paper",
            "symbol": "600584",
            "side": "hold",
            "price": 0,
            "quantity": True,
            "fee": -0.01,
            "note": None,
            "fee_plan_id": "   ",
        }
        for field, value in invalid_values.items():
            payload = self._payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(TradeValidationError) as ctx:
                TradeDraft.from_mapping(payload)
            self.assertEqual(ctx.exception.field, field)


if __name__ == "__main__":
    unittest.main()
