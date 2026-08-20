"""Domain fee-calculation rules for A-share and ETF trades.

Fee Policy is stateless and side-effect free. It accepts a validated trade
and a structured fee plan and returns the default fee breakdown. The caller
remains responsible for persisting the final fee value chosen by the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

from .models import TradeSide


class FeeSecurityType(StrEnum):
    """Fee-relevant security classification.

    This is intentionally separate from :class:`InstrumentType`
    (stock|etf|index) in ``packages.marketdata``.  The fee layer only cares
    about instruments that incur trading fees; indices are rejected with
    ``automatic_fee_not_supported`` (issue #151 decision #8).
    """

    A_SHARE = "a_share"
    ETF = "etf"


class FeePolicyValidationError(ValueError):
    """A stable field-level validation failure for fee-calculation input."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class _FeePlanLike(Protocol):
    """Minimal shape of a structured fee plan accepted by the policy."""

    a_share_commission_rate: Decimal
    a_share_min_commission: Decimal
    etf_commission_rate: Decimal
    etf_min_commission: Decimal
    stamp_duty_rate: Decimal
    stamp_duty_sell_only: bool
    transfer_fee_rate: Decimal
    transfer_fee_side: str
    transfer_fee_enabled: bool


@dataclass(frozen=True, slots=True)
class FeeCalculation:
    """Structured result of a fee-policy calculation."""

    trade_amount: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    total_fee: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_amount": float(self.trade_amount),
            "commission": float(self.commission),
            "stamp_duty": float(self.stamp_duty),
            "transfer_fee": float(self.transfer_fee),
            "total_fee": float(self.total_fee),
        }


def _positive_decimal(value: Any, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise FeePolicyValidationError(field, "must be a finite positive number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FeePolicyValidationError(field, "must be a finite positive number") from exc
    if not result.is_finite() or result <= 0:
        raise FeePolicyValidationError(field, "must be a finite positive number")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FeePolicyValidationError(field, "must be a positive integer")
    if value < 1:
        raise FeePolicyValidationError(field, "must be a positive integer")
    return value


def calculate_fee(
    *,
    security_type: FeeSecurityType | str,
    side: TradeSide | str,
    price: Decimal | float | str,
    quantity: int,
    plan: _FeePlanLike,
) -> FeeCalculation:
    """Return the default fee breakdown for a trade.

    All monetary calculations use ``Decimal`` to avoid binary-float rounding.
    The caller is responsible for any user override of the returned total.

    Indices (``instrument_type == "index"``) are rejected with
    ``automatic_fee_not_supported`` because they are not tradable and have
    no fee schedule (issue #151 decision #8).
    """

    if isinstance(security_type, str):
        if security_type == "index":
            raise FeePolicyValidationError(
                "security_type",
                "automatic_fee_not_supported: index is not tradable",
            )
        try:
            security_type = FeeSecurityType(security_type)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in FeeSecurityType)
            raise FeePolicyValidationError(
                "security_type", f"must be one of: {allowed}"
            ) from exc
    if not isinstance(security_type, FeeSecurityType):
        raise FeePolicyValidationError("security_type", "must be 'a_share' or 'etf'")

    if isinstance(side, str):
        try:
            side = TradeSide(side)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in TradeSide)
            raise FeePolicyValidationError("side", f"must be one of: {allowed}") from exc
    if not isinstance(side, TradeSide):
        raise FeePolicyValidationError("side", "must be 'buy' or 'sell'")

    price_value = _positive_decimal(price, "price")
    quantity_value = _positive_int(quantity, "quantity")

    amount = price_value * Decimal(quantity_value)

    if security_type is FeeSecurityType.ETF:
        commission_rate = plan.etf_commission_rate
        min_commission = plan.etf_min_commission
    else:
        commission_rate = plan.a_share_commission_rate
        min_commission = plan.a_share_min_commission

    commission = max(amount * commission_rate, min_commission)

    stamp_duty: Decimal
    if side is TradeSide.SELL:
        stamp_duty = amount * plan.stamp_duty_rate
    elif plan.stamp_duty_sell_only:
        stamp_duty = Decimal("0")
    else:
        stamp_duty = amount * plan.stamp_duty_rate

    transfer_fee: Decimal
    if not plan.transfer_fee_enabled:
        transfer_fee = Decimal("0")
    else:
        side_key = side.value
        side_flag = plan.transfer_fee_side
        if side_flag == "both" or side_flag == side_key:
            transfer_fee = amount * plan.transfer_fee_rate
        else:
            transfer_fee = Decimal("0")

    total = commission + stamp_duty + transfer_fee

    return FeeCalculation(
        trade_amount=amount,
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        total_fee=total,
    )
