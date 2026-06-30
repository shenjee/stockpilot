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

__all__ = [
    "INDICES",
    "LOCAL_CONFIG_NAMES",
    "MarketDataProvider",
    "MarketDataResult",
    "ProviderIssue",
    "RuntimePaths",
    "TencentStockDataProvider",
    "create_market_data_provider",
    "get_market_prefix",
]
