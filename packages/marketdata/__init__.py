"""Shared market-data infrastructure for StockPilot."""

# Direct sys.path users may expose only packages/, without the repository root.
try:
    from packages._import_aliases import initialize_package as _initialize_package
except ModuleNotFoundError as _exc:
    if _exc.name != "packages":
        raise
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.append(str(_Path(__file__).resolve().parents[2]))
    from packages._import_aliases import initialize_package as _initialize_package

if _initialize_package(__name__):
    from .market_data import (
        INDICES,
        MarketDataProvider,
        TencentStockDataProvider,
        create_market_data_provider,
        get_market_prefix,
    )
    from .provider_result import MarketDataResult, ProviderIssue
    from .provider_request_queue import (
        ProviderQueueClosedError,
        ProviderQueueError,
        ProviderQueueFullError,
        ProviderQueueOutcome,
        ProviderRequestPriority,
        ProviderRequestQueue,
        get_shared_provider_request_queue,
    )
    from .runtime_paths import LOCAL_CONFIG_NAMES, RuntimePaths
    from .t0_schema import (
        T0_MARKET_SCHEMA_VERSION,
        T0_TIMEZONE,
        InstrumentIdentity,
        InstrumentType,
        MarketDataSchemaError,
        standardize_bar,
        standardize_kline_series,
        standardize_quote,
        standardize_quote_snapshot,
        standardize_security_identity,
    )
    from .trading_calendar import CalendarUnavailableError, TradingCalendar

    __all__ = [
        "INDICES",
        "LOCAL_CONFIG_NAMES",
        "InstrumentIdentity",
        "InstrumentType",
        "MarketDataProvider",
        "MarketDataResult",
        "MarketDataSchemaError",
        "ProviderIssue",
        "ProviderQueueClosedError",
        "ProviderQueueError",
        "ProviderQueueFullError",
        "ProviderQueueOutcome",
        "ProviderRequestPriority",
        "ProviderRequestQueue",
        "RuntimePaths",
        "T0_MARKET_SCHEMA_VERSION",
        "T0_TIMEZONE",
        "TencentStockDataProvider",
        "TradingCalendar",
        "CalendarUnavailableError",
        "create_market_data_provider",
        "get_market_prefix",
        "get_shared_provider_request_queue",
        "standardize_bar",
        "standardize_kline_series",
        "standardize_quote",
        "standardize_quote_snapshot",
        "standardize_security_identity",
    ]
