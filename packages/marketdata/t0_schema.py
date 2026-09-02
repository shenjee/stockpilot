"""Stable T+0 market-data dictionaries built from existing provider output.

The cross-process shape is owned by
``apps/t0-assistant/contracts/logical-v2.schema.json``.  This module deliberately
does not introduce a second hierarchy of bar or quote classes: it validates
and maps provider dictionaries into that frozen logical shape, with security
identity and timezone carried by a small series/snapshot envelope. Tencent
provider rows include market timestamps, reported amounts and closed state;
KLineStore persists reported amounts while retaining legacy rows with an
unknown amount as ``NULL`` until a provider refresh replaces them.

Issue #151 separates four independent capability boundaries:

* :class:`InstrumentIdentity` / :class:`InstrumentType` — *what an instrument
  is* (stock, etf, index).  This is objective securities-master identity and
  lives here, in the market-data package.  It is deliberately distinct from
  the trading/fee layer's ``FeeSecurityType`` (a_share | etf), which expresses
  *can automatic fees be computed*.
* MarketDataCapability — *can quote/kline/replay be shown* — is implicit:
  every resolved :class:`InstrumentIdentity` can be viewed.
* TradeCapability and FeeCapability are owned by the trading package and are
  enforced at the trade/fee boundary, not in search or market-data code.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .market_data import get_market_prefix


T0_MARKET_SCHEMA_VERSION = "t0_market_v1"
T0_TIMEZONE = "Asia/Shanghai"
T0_MARKETS = frozenset({"sh", "sz"})
T0_TIMEFRAMES = frozenset({"1m", "5m", "day"})


class InstrumentType(StrEnum):
    """Objective securities-master instrument identity.

    This is *what an instrument is*, independent of whether it can be traded
    or have fees auto-computed.  The trading/fee layer maps these to its own
    ``FeeSecurityType`` (a_share | etf) at its boundary; ``index`` has no
    fee-layer counterpart and is rejected there.
    """

    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"


# Mapping from SecuritiesStore ``type`` values to :class:`InstrumentType`.
_INSTRUMENT_TYPE_MAP: dict[str, InstrumentType] = {
    "stock": InstrumentType.STOCK,
    "etf": InstrumentType.ETF,
    "index": InstrumentType.INDEX,
}


@dataclass(frozen=True, slots=True)
class InstrumentIdentity:
    """Authoritative, immutable securities-master identity.

    A resolved :class:`InstrumentIdentity` is the single source of truth for
    *what an instrument is*.  It is resolved once at the App/API orchestration
    entry (via :class:`SecuritiesSearchService` or an equivalent catalog port)
    and then passed — as an immutable value — through Session, Live, Replay,
    and Historical paths.  Downstream code never re-resolves identity from a
    bare symbol, and never fabricates identity when resolution fails.

    The ``instrument_type`` field carries objective identity (stock | etf |
    index).  The trading/fee layer's ``FeeSecurityType`` is a *separate*
    enum owned by ``packages.t0assistant.trading``; the mapping
    (stock→a_share, etf→etf, index→unsupported) is an explicit trade-strategy
    decision enforced at the trade/fee boundary, not a property of identity.
    """

    symbol: str
    code: str
    market: str
    name: str
    instrument_type: InstrumentType

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "code": self.code,
            "market": self.market,
            "name": self.name,
            "instrument_type": self.instrument_type.value,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> InstrumentIdentity:
        """Build an :class:`InstrumentIdentity` from a validated mapping.

        Raises :class:`MarketDataSchemaError` if any required field is missing
        or ``instrument_type`` is not a known :class:`InstrumentType`.
        """

        try:
            instrument_type = InstrumentType(str(data["instrument_type"]))
        except (KeyError, ValueError) as exc:
            raise MarketDataSchemaError(
                "instrument_type must be one of: "
                + ", ".join(t.value for t in InstrumentType)
            ) from exc
        symbol = str(data.get("symbol", ""))
        code = str(data.get("code", ""))
        market = str(data.get("market", "")).lower()
        name = str(data.get("name", "")).strip()
        if not symbol or not code or market not in T0_MARKETS or not name:
            raise MarketDataSchemaError(
                "InstrumentIdentity requires non-empty symbol, code, market (sh|sz) and name"
            )
        return cls(
            symbol=symbol,
            code=code,
            market=market,
            name=name,
            instrument_type=instrument_type,
        )


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
    """Map one provider/store row to the T0 ``bar`` shape.

    Price availability requires ``timestamp + OHLC``. ``volume`` and ``amount``
    are nullable observations: a missing key and an explicit ``null`` both
    become ``null`` in the output, and the fields are always present. Unknown
    quantity must never be fabricated as ``0`` or derived from OHLC.
    ``closed`` may come from the row or an explicit caller decision; it is
    never inferred from the current wall clock.
    """

    timestamp = row.get("timestamp", row.get("date"))
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        raise MarketDataSchemaError("bar timestamp/date must be a non-empty market timestamp")

    resolved_closed = row.get("closed", closed)
    if not isinstance(resolved_closed, bool):
        raise MarketDataSchemaError("bar closed must be supplied explicitly")

    bar = {
        "timestamp": timestamp,
        "open": round(_non_negative_number(row, "open"), 2),
        "high": round(_non_negative_number(row, "high"), 2),
        "low": round(_non_negative_number(row, "low"), 2),
        "close": round(_non_negative_number(row, "close"), 2),
        "volume": _optional_non_negative_number(row, "volume"),
        "amount": _round_optional(
            _optional_non_negative_number(row, "amount"), 2
        ),
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
        "latest_price": round(
            _non_negative_number(row, "latest_price", fallback="price"), 2
        ),
        "change_percent": round(
            _number(row, "change_percent", fallback="change_pct"), 2
        ),
        "open": round(_non_negative_number(row, "open"), 2),
        "high": round(_non_negative_number(row, "high"), 2),
        "low": round(_non_negative_number(row, "low"), 2),
        "previous_close": round(_non_negative_number(
            row, "previous_close", fallback="pre_close"
        ), 2),
        "volume": _optional_non_negative_number(row, "volume"),
        "amount": _round_optional(
            _optional_non_negative_number(row, "amount"), 2
        ),
        "volume_ratio": _round_optional(
            _optional_number(row, "volume_ratio"), 2
        ),
        "order_imbalance": _round_optional(
            _optional_number(row, "order_imbalance"), 2
        ),
        "turnover_rate": _round_optional(
            _optional_number(row, "turnover_rate"), 2
        ),
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


def _optional_non_negative_number(
    row: Mapping[str, Any],
    key: str,
) -> float | int | None:
    """Return a non-negative number, or ``null`` when the key is absent/null.

    Missing keys and explicit ``null`` are equivalent unknown values. The
    output always retains the field so consumers never treat absence as zero.
    """

    if key not in row or row[key] is None:
        return None
    value = _number(row, key)
    if value < 0:
        raise MarketDataSchemaError(f"{key} must be non-negative")
    return value


def _round_optional(value: float | int | None, ndigits: int) -> float | int | None:
    """Round an optional numeric value to *ndigits* decimal places."""
    if value is None:
        return None
    return round(value, ndigits)
