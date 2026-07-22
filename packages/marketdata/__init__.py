"""Shared market-data infrastructure for StockPilot."""

from .market_data import (
    INDICES,
    MarketDataProvider,
    TencentStockDataProvider,
    create_market_data_provider,
    get_market_prefix,
)
from .provider_result import MarketDataResult, ProviderIssue
from .runtime_paths import LOCAL_CONFIG_NAMES, RuntimePaths
from .t0_schema import (
    T0_MARKET_SCHEMA_VERSION,
    T0_TIMEZONE,
    MarketDataSchemaError,
    standardize_bar,
    standardize_kline_series,
    standardize_quote,
    standardize_quote_snapshot,
    standardize_security_identity,
)

__all__ = [
    "INDICES",
    "LOCAL_CONFIG_NAMES",
    "MarketDataProvider",
    "MarketDataResult",
    "MarketDataSchemaError",
    "ProviderIssue",
    "RuntimePaths",
    "T0_MARKET_SCHEMA_VERSION",
    "T0_TIMEZONE",
    "TencentStockDataProvider",
    "create_market_data_provider",
    "get_market_prefix",
    "standardize_bar",
    "standardize_kline_series",
    "standardize_quote",
    "standardize_quote_snapshot",
    "standardize_security_identity",
]
