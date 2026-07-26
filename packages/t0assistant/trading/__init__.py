"""Shared trade value objects and deterministic time-bucketing rules."""

from .fee_policy import (
    FeeCalculation,
    FeePolicyValidationError,
    SecurityType,
    calculate_fee,
)
from .models import (
    TradeDraft,
    TradeRecord,
    TradeScope,
    TradeSide,
    TradeValidationError,
    bucket_start_for,
    normalize_executed_at,
)

__all__ = [
    "calculate_fee",
    "bucket_start_for",
    "FeeCalculation",
    "FeePolicyValidationError",
    "normalize_executed_at",
    "SecurityType",
    "TradeDraft",
    "TradeRecord",
    "TradeScope",
    "TradeSide",
    "TradeValidationError",
]
