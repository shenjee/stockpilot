"""Shared trade value objects, fee rules, real-trade service and markers."""

from .api import TradeCommandApi, TradeEventPublisher
from .fee_policy import (
    AutomaticFeeNotSupportedError,
    FeeCalculation,
    FeePolicyValidationError,
    FeeSecurityType,
    calculate_fee,
)
from .markers import (
    SHARES_PER_LOT,
    TradeMarker,
    TradeMarkerProjection,
    TradeMarkerProjector,
    TradeMarkerValidationError,
    format_lot_label,
    lot_count,
    project_trade_marker,
    project_trade_markers,
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
from .service import (
    AllowAllEligibility,
    InstrumentEligibilityPort,
    TradeEligibilityError,
    TradeService,
)

__all__ = [
    "SHARES_PER_LOT",
    "AutomaticFeeNotSupportedError",
    "calculate_fee",
    "bucket_start_for",
    "FeeCalculation",
    "FeePolicyValidationError",
    "format_lot_label",
    "lot_count",
    "normalize_executed_at",
    "project_trade_marker",
    "project_trade_markers",
    "FeeSecurityType",
    "InstrumentEligibilityPort",
    "AllowAllEligibility",
    "TradeCommandApi",
    "TradeDraft",
    "TradeEligibilityError",
    "TradeEventPublisher",
    "TradeMarker",
    "TradeMarkerProjection",
    "TradeMarkerProjector",
    "TradeMarkerValidationError",
    "TradeRecord",
    "TradeScope",
    "TradeService",
    "TradeSide",
    "TradeValidationError",
]
