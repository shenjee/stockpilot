from __future__ import annotations

from decimal import Decimal
import unittest

from packages.t0assistant.repositories import FeePlanRecord, TransferFeeSide
from packages.t0assistant.trading import (
    FeePolicyValidationError,
    FeeSecurityType,
    TradeSide,
    calculate_fee,
)


def _plan(
    *,
    a_share_commission_rate: Decimal = Decimal("0.0003"),
    a_share_min_commission: Decimal = Decimal("5"),
    etf_commission_rate: Decimal = Decimal("0.0002"),
    etf_min_commission: Decimal = Decimal("5"),
    stamp_duty_rate: Decimal = Decimal("0.0005"),
    stamp_duty_sell_only: bool = True,
    transfer_fee_rate: Decimal = Decimal("0.00001"),
    transfer_fee_side: TransferFeeSide = TransferFeeSide.BOTH,
    transfer_fee_enabled: bool = True,
) -> FeePlanRecord:
    return FeePlanRecord(
        fee_plan_id="plan-1",
        name="Test Plan",
        a_share_commission_rate=a_share_commission_rate,
        a_share_min_commission=a_share_min_commission,
        etf_commission_rate=etf_commission_rate,
        etf_min_commission=etf_min_commission,
        stamp_duty_rate=stamp_duty_rate,
        stamp_duty_sell_only=stamp_duty_sell_only,
        transfer_fee_rate=transfer_fee_rate,
        transfer_fee_side=transfer_fee_side.value,
        transfer_fee_enabled=transfer_fee_enabled,
    )


class FeePolicyCalculationTests(unittest.TestCase):
    def test_a_share_small_buy_hits_minimum_commission_and_no_stamp_duty(self) -> None:
        plan = _plan()
        result = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10.00"),
            quantity=100,
            plan=plan,
        )

        self.assertEqual(result.trade_amount, Decimal("1000.00"))
        self.assertEqual(result.commission, Decimal("5.00"))
        self.assertEqual(result.stamp_duty, Decimal("0"))
        self.assertEqual(result.transfer_fee, Decimal("0.01"))
        self.assertEqual(result.total_fee, Decimal("5.01"))

    def test_a_share_sell_includes_stamp_duty_and_transfer_fee(self) -> None:
        plan = _plan()
        result = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.SELL,
            price=Decimal("10.00"),
            quantity=1000,
            plan=plan,
        )

        self.assertEqual(result.trade_amount, Decimal("10000.00"))
        self.assertEqual(result.commission, Decimal("5.00"))
        self.assertEqual(result.stamp_duty, Decimal("5.00"))
        self.assertEqual(result.transfer_fee, Decimal("0.10"))
        self.assertEqual(result.total_fee, Decimal("10.10"))

    def test_etf_buy_and_sell_use_etf_commission_config(self) -> None:
        plan = _plan()
        buy = calculate_fee(
            security_type=FeeSecurityType.ETF,
            side=TradeSide.BUY,
            price=Decimal("2.50"),
            quantity=1000,
            plan=plan,
        )
        self.assertEqual(buy.trade_amount, Decimal("2500.00"))
        self.assertEqual(buy.commission, Decimal("5.00"))
        self.assertEqual(buy.stamp_duty, Decimal("0"))
        self.assertEqual(buy.transfer_fee, Decimal("0.025"))

        sell = calculate_fee(
            security_type=FeeSecurityType.ETF,
            side=TradeSide.SELL,
            price=Decimal("2.50"),
            quantity=1000,
            plan=plan,
        )
        self.assertEqual(sell.trade_amount, Decimal("2500.00"))
        self.assertEqual(sell.commission, Decimal("5.00"))
        self.assertEqual(sell.stamp_duty, Decimal("1.25"))
        self.assertEqual(sell.transfer_fee, Decimal("0.025"))

    def test_transfer_fee_disabled_is_zero(self) -> None:
        plan = _plan(transfer_fee_enabled=False)
        result = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        self.assertEqual(result.transfer_fee, Decimal("0"))

    def test_transfer_fee_buy_only_charges_buy(self) -> None:
        plan = _plan(transfer_fee_side=TransferFeeSide.BUY)
        buy = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        sell = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.SELL,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        self.assertEqual(buy.transfer_fee, Decimal("0.10"))
        self.assertEqual(sell.transfer_fee, Decimal("0"))

    def test_transfer_fee_sell_only_charges_sell(self) -> None:
        plan = _plan(transfer_fee_side=TransferFeeSide.SELL)
        buy = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        sell = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.SELL,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        self.assertEqual(buy.transfer_fee, Decimal("0"))
        self.assertEqual(sell.transfer_fee, Decimal("0.10"))

    def test_transfer_fee_both_charges_both_sides(self) -> None:
        plan = _plan(transfer_fee_side=TransferFeeSide.BOTH)
        for side in (TradeSide.BUY, TradeSide.SELL):
            result = calculate_fee(
                security_type=FeeSecurityType.A_SHARE,
                side=side,
                price=Decimal("10000.00"),
                quantity=1,
                plan=plan,
            )
            self.assertEqual(result.transfer_fee, Decimal("0.10"))

    def test_zero_minimum_commission_plan(self) -> None:
        plan = _plan(a_share_min_commission=Decimal("0"))
        result = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10.00"),
            quantity=100,
            plan=plan,
        )
        self.assertEqual(result.commission, Decimal("0.30"))

    def test_stamp_duty_bidirectional_when_sell_only_is_false(self) -> None:
        plan = _plan(stamp_duty_sell_only=False)
        buy = calculate_fee(
            security_type=FeeSecurityType.A_SHARE,
            side=TradeSide.BUY,
            price=Decimal("10000.00"),
            quantity=1,
            plan=plan,
        )
        self.assertEqual(buy.stamp_duty, Decimal("5.00"))


class FeePolicyValidationTests(unittest.TestCase):
    def test_invalid_security_type_is_rejected(self) -> None:
        with self.assertRaises(FeePolicyValidationError) as ctx:
            calculate_fee(
                security_type="future",
                side=TradeSide.BUY,
                price=Decimal("10.00"),
                quantity=100,
                plan=_plan(),
            )
        self.assertEqual(ctx.exception.field, "security_type")

    def test_invalid_side_is_rejected(self) -> None:
        with self.assertRaises(FeePolicyValidationError) as ctx:
            calculate_fee(
                security_type=FeeSecurityType.A_SHARE,
                side="hold",
                price=Decimal("10.00"),
                quantity=100,
                plan=_plan(),
            )
        self.assertEqual(ctx.exception.field, "side")

    def test_non_positive_price_is_rejected(self) -> None:
        for price in (0, -1, "abc", float("nan"), None, True):
            with self.subTest(price=price):
                with self.assertRaises(FeePolicyValidationError) as ctx:
                    calculate_fee(
                        security_type=FeeSecurityType.A_SHARE,
                        side=TradeSide.BUY,
                        price=price,
                        quantity=100,
                        plan=_plan(),
                    )
                self.assertEqual(ctx.exception.field, "price")

    def test_non_positive_quantity_is_rejected(self) -> None:
        for quantity in (0, -1, "abc", None, True, 1.5):
            with self.subTest(quantity=quantity):
                with self.assertRaises(FeePolicyValidationError) as ctx:
                    calculate_fee(
                        security_type=FeeSecurityType.A_SHARE,
                        side=TradeSide.BUY,
                        price=Decimal("10.00"),
                        quantity=quantity,
                        plan=_plan(),
                    )
                self.assertEqual(ctx.exception.field, "quantity")


if __name__ == "__main__":
    unittest.main()
