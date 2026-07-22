"""Transport-independent trade domain values.

The same values are used by persisted real trades and Replay-only simulated
trades.  Persistence and Session ownership are deliberately outside this
module; this layer only owns validation, timestamp normalization, and the
deterministic five-minute chart bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import math
import re
from typing import Any, Mapping


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_MINUTE_FORMAT = "%Y-%m-%d %H:%M"


class TradeScope(StrEnum):
    REAL = "real"
    SIMULATED = "simulated"


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TradeValidationError(ValueError):
    """A stable field-level validation failure for a trade value."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def normalize_executed_at(value: str | datetime) -> datetime:
    """Return a second-precision, naive Asia/Shanghai wall-clock value.

    Cross-process trade timestamps intentionally use the application's fixed
    local wall-clock representation.  A minute-only user input is accepted and
    normalized by appending ``:00``; sub-second and timezone-bearing values are
    rejected so no adapter can silently change the recorded execution time.
    """

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise TradeValidationError(
                "executed_at", "must be an Asia/Shanghai wall-clock value without an offset"
            )
        if value.microsecond:
            raise TradeValidationError("executed_at", "must not contain fractional seconds")
        return value

    if not isinstance(value, str):
        raise TradeValidationError("executed_at", "must be a timestamp string")

    candidate = value.strip()
    format_ = _MINUTE_FORMAT if len(candidate) == 16 else _TIMESTAMP_FORMAT
    try:
        parsed = datetime.strptime(candidate, format_)
    except ValueError as exc:
        raise TradeValidationError(
            "executed_at", "must use YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS"
        ) from exc
    return parsed


def bucket_start_for(executed_at: str | datetime) -> datetime:
    """Return the inclusive start of the five-minute bar containing a trade."""

    timestamp = normalize_executed_at(executed_at)
    return timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0)


def _enum_value(enum_type: type[TradeScope] | type[TradeSide], value: Any, field: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise TradeValidationError(field, f"must be one of: {allowed}") from exc


def _decimal_value(value: Any, field: str, *, allow_zero: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TradeValidationError(field, "must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise TradeValidationError(field, "must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradeValidationError(field, "must be a finite number") from exc
    if not result.is_finite():
        raise TradeValidationError(field, "must be a finite number")
    if result < 0 or (not allow_zero and result == 0):
        comparison = "zero or greater" if allow_zero else "greater than zero"
        raise TradeValidationError(field, f"must be {comparison}")
    return result


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TradeValidationError(field, "must be a string or null")
    normalized = value.strip()
    if not normalized:
        raise TradeValidationError(field, "must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class TradeDraft:
    """Validated input shared by real and Replay-simulated trades."""

    trade_scope: TradeScope
    symbol: str
    side: TradeSide
    executed_at: datetime
    price: Decimal
    quantity: int
    fee: Decimal | None = None
    note: str = ""
    fee_plan_id: str | None = None

    def __post_init__(self) -> None:
        """Keep the value valid even when domain code constructs it directly."""

        if not isinstance(self.symbol, str) or not _SYMBOL_PATTERN.fullmatch(self.symbol):
            raise TradeValidationError("symbol", "must use sh.###### or sz.######")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise TradeValidationError("quantity", "must be a positive integer")
        if not isinstance(self.note, str):
            raise TradeValidationError("note", "must be a string")

        object.__setattr__(
            self,
            "trade_scope",
            _enum_value(TradeScope, self.trade_scope, "trade_scope"),
        )
        object.__setattr__(self, "side", _enum_value(TradeSide, self.side, "side"))
        object.__setattr__(
            self, "executed_at", normalize_executed_at(self.executed_at)
        )
        object.__setattr__(
            self, "price", _decimal_value(self.price, "price", allow_zero=False)
        )
        if self.fee is not None:
            object.__setattr__(
                self, "fee", _decimal_value(self.fee, "fee", allow_zero=True)
            )
        object.__setattr__(
            self,
            "fee_plan_id",
            _optional_text(self.fee_plan_id, "fee_plan_id"),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TradeDraft:
        """Validate a logical ``trade_draft`` payload from the app contract."""

        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol):
            raise TradeValidationError("symbol", "must use sh.###### or sz.######")

        quantity = payload.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise TradeValidationError("quantity", "must be a positive integer")

        note = payload.get("note", "")
        if not isinstance(note, str):
            raise TradeValidationError("note", "must be a string")

        fee_raw = payload.get("fee")
        return cls(
            trade_scope=_enum_value(
                TradeScope, payload.get("trade_scope"), "trade_scope"
            ),
            symbol=symbol,
            side=_enum_value(TradeSide, payload.get("side"), "side"),
            executed_at=normalize_executed_at(payload.get("executed_at")),
            price=_decimal_value(payload.get("price"), "price", allow_zero=False),
            quantity=quantity,
            fee=(
                None
                if fee_raw is None
                else _decimal_value(fee_raw, "fee", allow_zero=True)
            ),
            note=note,
            fee_plan_id=_optional_text(payload.get("fee_plan_id"), "fee_plan_id"),
        )

    @property
    def bucket_start(self) -> datetime:
        return bucket_start_for(self.executed_at)

    def to_dict(self) -> dict[str, Any]:
        """Map the value back to the process-neutral app contract shape."""

        return {
            "trade_scope": self.trade_scope.value,
            "symbol": self.symbol,
            "side": self.side.value,
            "executed_at": self.executed_at.strftime(_TIMESTAMP_FORMAT),
            "price": float(self.price),
            "quantity": self.quantity,
            "fee": None if self.fee is None else float(self.fee),
            "note": self.note,
            "fee_plan_id": self.fee_plan_id,
        }


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A domain trade with identity and its derived chart bucket."""

    trade_id: str
    trade: TradeDraft

    def __post_init__(self) -> None:
        if not isinstance(self.trade_id, str) or not self.trade_id.strip():
            raise TradeValidationError("trade_id", "must not be blank")
        object.__setattr__(self, "trade_id", self.trade_id.strip())

    @property
    def bucket_start(self) -> datetime:
        return self.trade.bucket_start

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "bucket_start": self.bucket_start.strftime(_TIMESTAMP_FORMAT),
            **self.trade.to_dict(),
        }
