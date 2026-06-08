from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Mapping, MutableMapping, Optional, Sequence

from .config import MARKET_SUFFIX, TIMEFRAME_TO_CZSC_FREQ, TRACKER_GAPS, TRACKER_REQUIRED_FIELDS
from .schema import AnalysisWarning, NormalizationResult, NormalizedBar


TIMESTAMP_KEYS: Sequence[str] = ("timestamp", "datetime", "dt", "date", "trade_date")
VOLUME_KEYS: Sequence[str] = ("volume", "vol")
AMOUNT_KEYS: Sequence[str] = ("amount", "turnover")


class NormalizationError(ValueError):
    pass


def build_symbol(code: str, market: str) -> str:
    market_key = (market or "").lower()
    suffix = MARKET_SUFFIX.get(market_key)
    if not suffix:
        raise NormalizationError(f"Unsupported market prefix: {market}")
    return f"{code}.{suffix}"


def normalize_tracker_klines(
    rows: Iterable[Mapping[str, object]],
    code: str,
    market: str,
    timeframe: str = "day",
    source: str = "tencent",
    strict: bool = True,
) -> NormalizationResult:
    symbol = build_symbol(code=code, market=market)
    return normalize_ohlcv_rows(
        rows=rows,
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        strict=strict,
        input_fields=list(TRACKER_REQUIRED_FIELDS),
        gaps=list(TRACKER_GAPS),
    )


def normalize_ohlcv_rows(
    rows: Iterable[Mapping[str, object]],
    symbol: str,
    timeframe: str,
    source: str,
    strict: bool = True,
    input_fields: Optional[List[str]] = None,
    gaps: Optional[List[str]] = None,
) -> NormalizationResult:
    if timeframe not in TIMEFRAME_TO_CZSC_FREQ:
        raise NormalizationError(f"Unsupported timeframe: {timeframe}")

    rows_list = [dict(raw) for raw in rows]
    warnings: List[AnalysisWarning] = []
    deduped: MutableMapping[str, Mapping[str, object]] = {}

    for row in rows_list:
        timestamp = _extract_timestamp(row)
        if timestamp in deduped:
            warnings.append(
                _warning(
                    warning_id="warning_duplicate_timestamp",
                    code="DUPLICATE_TIMESTAMP",
                    message="Duplicate timestamps detected; the last row for each timestamp is kept.",
                    field="timestamp",
                )
            )
        deduped[timestamp] = row

    normalized_rows = sorted(deduped.values(), key=_extract_timestamp)
    normalized_bars: List[NormalizedBar] = []
    derived_amount_warning_added = False

    for index, row in enumerate(normalized_rows):
        try:
            bar = _normalize_row(
                row=row,
                symbol=symbol,
                timeframe=timeframe,
                source=source,
                bar_index=index,
            )
        except NormalizationError as exc:
            if strict:
                raise
            warnings.append(
                _warning(
                    warning_id=f"warning_invalid_bar_{index}",
                    code="INVALID_BAR",
                    message=str(exc),
                    field="bars",
                )
            )
            continue
        if bar.meta.get("amount_derived") and not derived_amount_warning_added:
            warnings.append(
                _warning(
                    warning_id="warning_amount_derived",
                    code="AMOUNT_DERIVED",
                    message="Missing amount values are derived with close * volume during Phase 1.",
                    field="amount",
                )
            )
            derived_amount_warning_added = True
        normalized_bars.append(bar)

    if not normalized_bars and strict:
        raise NormalizationError("No valid bars were provided.")

    return NormalizationResult(
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        bars=normalized_bars,
        warnings=warnings,
        input_fields=input_fields or [],
        gaps=gaps or [],
    )


def _normalize_row(
    row: Mapping[str, object],
    symbol: str,
    timeframe: str,
    source: str,
    bar_index: int,
) -> NormalizedBar:
    timestamp = _extract_timestamp(row)
    open_price = _extract_float(row, "open")
    close_price = _extract_float(row, "close")
    high_price = _extract_float(row, "high")
    low_price = _extract_float(row, "low")
    volume = _extract_float(row, *VOLUME_KEYS)

    amount = _extract_optional_float(row, *AMOUNT_KEYS)
    amount_derived = False
    if amount is None:
        amount = close_price * volume
        amount_derived = True

    if high_price < max(open_price, close_price, low_price):
        raise NormalizationError(f"High price is inconsistent at {timestamp}.")
    if low_price > min(open_price, close_price, high_price):
        raise NormalizationError(f"Low price is inconsistent at {timestamp}.")
    if volume < 0:
        raise NormalizationError(f"Volume must be non-negative at {timestamp}.")

    return NormalizedBar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        amount=amount,
        source=source,
        bar_index=bar_index,
        meta={"amount_derived": amount_derived},
    )


def _extract_timestamp(row: Mapping[str, object]) -> str:
    for key in TIMESTAMP_KEYS:
        value = row.get(key)
        if value in (None, ""):
            continue
        return _normalize_timestamp(value)
    raise NormalizationError("A timestamp field is required.")


def _normalize_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if not isinstance(value, str):
        raise NormalizationError(f"Unsupported timestamp value: {value!r}")

    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise NormalizationError(f"Unsupported timestamp format: {value}")


def _extract_float(row: Mapping[str, object], *keys: str) -> float:
    value = _extract_optional_float(row, *keys)
    if value is None:
        raise NormalizationError(f"Missing required numeric field from {keys}.")
    return value


def _extract_optional_float(row: Mapping[str, object], *keys: str) -> Optional[float]:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise NormalizationError(f"Invalid numeric value for {key}: {value!r}") from exc
    return None


def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )
