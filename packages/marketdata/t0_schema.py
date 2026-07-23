"""Stable T+0 market-data dictionaries built from existing provider output.

The cross-process shape is owned by
``apps/t0-assistant/contracts/logical-schema.json``.  This module deliberately
does not introduce a second hierarchy of bar or quote classes: it validates
and maps provider dictionaries into that frozen logical shape, with security
identity and timezone carried by a small series/snapshot envelope. Tencent
provider rows include market timestamps, reported amounts and closed state;
KLineStore persists reported amounts while retaining legacy rows with an
unknown amount as ``NULL`` until a provider refresh replaces them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .market_data import get_market_prefix


T0_MARKET_SCHEMA_VERSION = "t0_market_v1"
T0_TIMEZONE = "Asia/Shanghai"
T0_MARKETS = frozenset({"sh", "sz"})
T0_TIMEFRAMES = frozenset({"1m", "5m", "day"})


class MarketDataSchemaError(ValueError):
    """Raised when provider data cannot be represented without fabrication."""


def standardize_security_identity(
    code: str,
    market: str | None = None,
) -> dict[str, str]:
    """Return the frozen T+0 security identity for an A-share/ETF code."""

    normalized_code, normalized_market = _normalize_code_market(code, market)
    return {
        "symbol": f"{normalized_market}.{normalized_code}",
        "code": normalized_code,
        "market": normalized_market,
        "timezone": T0_TIMEZONE,
    }


def standardize_bar(
    row: Mapping[str, Any],
    *,
    closed: bool | None = None,
) -> dict[str, Any]:
    """Map one field-complete provider/store row to the T0-002 ``bar`` shape.

    ``amount`` is intentionally required because deriving it from close and
    volume would change the market-data meaning.  ``closed`` may come from the
    row or from an explicit caller decision; it is never inferred from the
    current wall clock.  T0-008/T0-009 are responsible for making current
    Provider/Store rows field-complete; this function does not mask that gap.
    """

    timestamp = row.get("timestamp", row.get("date"))
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        raise MarketDataSchemaError("bar timestamp/date must be a non-empty market timestamp")
    if "amount" not in row:
        raise MarketDataSchemaError("bar amount is required and must come from the provider")

    resolved_closed = row.get("closed", closed)
    if not isinstance(resolved_closed, bool):
        raise MarketDataSchemaError("bar closed must be supplied explicitly")

    bar = {
        "timestamp": timestamp,
        "open": _non_negative_number(row, "open"),
        "high": _non_negative_number(row, "high"),
        "low": _non_negative_number(row, "low"),
        "close": _non_negative_number(row, "close"),
        "volume": _non_negative_number(row, "volume"),
        "amount": _non_negative_number(row, "amount"),
        "closed": resolved_closed,
    }
    if bar["high"] < max(bar["open"], bar["low"], bar["close"]):
        raise MarketDataSchemaError("bar high is below an OHLC value")
    if bar["low"] > min(bar["open"], bar["high"], bar["close"]):
        raise MarketDataSchemaError("bar low is above an OHLC value")
    return bar


def standardize_kline_series(
    code: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str | None = None,
    timeframe: str,
    closed: bool | None = None,
) -> dict[str, Any]:
    """Wrap standardized bars with stable identity, timezone and timeframe."""

    if timeframe not in T0_TIMEFRAMES:
        raise MarketDataSchemaError(f"unsupported T+0 timeframe: {timeframe}")
    identity = standardize_security_identity(code, market)
    return {
        "schema_version": T0_MARKET_SCHEMA_VERSION,
        **identity,
        "timeframe": timeframe,
        "bars": [standardize_bar(row, closed=closed) for row in rows],
    }


def standardize_quote(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map an existing realtime dictionary to the T0-002 ``quote`` shape."""

    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str) or len(timestamp) < 19:
        raise MarketDataSchemaError(
            "quote timestamp must be the provider's market timestamp"
        )

    return {
        "timestamp": timestamp,
        "latest_price": _non_negative_number(row, "latest_price", fallback="price"),
        "change_percent": _number(row, "change_percent", fallback="change_pct"),
        "open": _non_negative_number(row, "open"),
        "high": _non_negative_number(row, "high"),
        "low": _non_negative_number(row, "low"),
        "previous_close": _non_negative_number(
            row, "previous_close", fallback="pre_close"
        ),
        "volume": _non_negative_number(row, "volume"),
        "amount": _non_negative_number(row, "amount"),
        "volume_ratio": _optional_number(row, "volume_ratio"),
        "order_imbalance": _optional_number(row, "order_imbalance"),
        "turnover_rate": _optional_number(row, "turnover_rate"),
    }


def standardize_quote_snapshot(
    code: str,
    row: Mapping[str, Any],
    *,
    market: str | None = None,
) -> dict[str, Any]:
    """Wrap a standardized quote with stable identity and timezone."""

    identity = standardize_security_identity(code, market)
    return {
        "schema_version": T0_MARKET_SCHEMA_VERSION,
        **identity,
        "quote": standardize_quote(row),
    }


def _normalize_code_market(code: str, market: str | None) -> tuple[str, str]:
    value = str(code).strip().lower()
    embedded_market = None
    if len(value) == 9 and value[2] == "." and value[:2] in T0_MARKETS:
        embedded_market, value = value[:2], value[3:]
    elif len(value) == 8 and value[:2] in T0_MARKETS:
        embedded_market, value = value[:2], value[2:]

    if not (len(value) == 6 and value.isdigit()):
        raise MarketDataSchemaError("T+0 code must contain exactly six digits")
    resolved_market = (market or embedded_market or get_market_prefix(value)).lower()
    if embedded_market and market and embedded_market != resolved_market:
        raise MarketDataSchemaError("code prefix and explicit market disagree")
    if resolved_market not in T0_MARKETS:
        raise MarketDataSchemaError("T+0 currently supports Shanghai and Shenzhen only")
    return value, resolved_market


def _raw_value(row: Mapping[str, Any], key: str, fallback: str | None = None) -> Any:
    if key in row:
        return row[key]
    if fallback and fallback in row:
        return row[fallback]
    raise MarketDataSchemaError(f"{key} is required")


def _number(row: Mapping[str, Any], key: str, fallback: str | None = None) -> float | int:
    value = _raw_value(row, key, fallback)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketDataSchemaError(f"{key} must be numeric")
    if not math.isfinite(value):
        raise MarketDataSchemaError(f"{key} must be finite")
    return value


def _non_negative_number(
    row: Mapping[str, Any],
    key: str,
    fallback: str | None = None,
) -> float | int:
    value = _number(row, key, fallback)
    if value < 0:
        raise MarketDataSchemaError(f"{key} must be non-negative")
    return value


def _optional_number(row: Mapping[str, Any], key: str) -> float | int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketDataSchemaError(f"{key} must be numeric or null")
    if not math.isfinite(value):
        raise MarketDataSchemaError(f"{key} must be finite or null")
    return value
