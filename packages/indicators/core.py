"""Technical indicators for standard StockPilot K-line dictionaries.

Every public calculation returns complete, timestamp-aligned point sequences.
Values that do not yet have enough warm-up observations are represented by
``None`` so the result can cross the logical JSON boundary as ``null``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


MA_PERIODS = (5, 10, 20, 30, 60)
VOLUME_MA_PERIODS = (5, 10)
BOLL_PERIOD = 20
BOLL_STDDEV = 2.0
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9

IndicatorPoint = dict[str, str | float | None]
Bar = Mapping[str, Any]


class IndicatorInputError(ValueError):
    """Raised when bars cannot produce an aligned, deterministic series."""


def calculate_moving_average(
    bars: Sequence[Bar],
    period: int,
    *,
    field: str = "close",
) -> list[IndicatorPoint]:
    """Return a simple moving average with a full-window warm-up."""

    timestamps, values = _extract_series(bars, field)
    return _points(timestamps, _simple_moving_average(values, period))


def calculate_boll(
    bars: Sequence[Bar],
    *,
    period: int = BOLL_PERIOD,
    stddev: float = BOLL_STDDEV,
) -> dict[str, Any]:
    """Return BOLL middle and population-standard-deviation bands."""

    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise IndicatorInputError("BOLL period must be a positive integer")
    if (
        isinstance(stddev, bool)
        or not isinstance(stddev, (int, float))
        or not math.isfinite(stddev)
        or stddev < 0
    ):
        raise IndicatorInputError("BOLL stddev must be a finite non-negative number")

    timestamps, closes = _extract_series(bars, "close")
    middle = _simple_moving_average(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for index, mean in enumerate(middle):
        if mean is None:
            upper.append(None)
            lower.append(None)
            continue
        window = closes[index - period + 1 : index + 1]
        variance = math.fsum((value - mean) ** 2 for value in window) / period
        deviation = math.sqrt(max(variance, 0.0)) * float(stddev)
        upper.append(mean + deviation)
        lower.append(mean - deviation)

    return {
        "period": period,
        "stddev": float(stddev),
        "upper": _points(timestamps, upper),
        "middle": _points(timestamps, middle),
        "lower": _points(timestamps, lower),
    }


def calculate_macd(
    bars: Sequence[Bar],
    *,
    fast_period: int = MACD_FAST_PERIOD,
    slow_period: int = MACD_SLOW_PERIOD,
    signal_period: int = MACD_SIGNAL_PERIOD,
) -> dict[str, Any]:
    """Return MACD using recursive EMAs and the China-market 2x histogram.

    EMAs start recursively from the first close, while values remain hidden
    until their configured period is observed. DIF therefore starts after the
    slow-period warm-up. DEA and the histogram start after a further
    ``signal_period`` DIF observations. The histogram is exactly
    ``2 * (DIF - DEA)``.
    """

    _validate_macd_periods(fast_period, slow_period, signal_period)
    timestamps, closes = _extract_series(bars, "close")
    fast_ema = _recursive_ema(closes, fast_period)
    slow_ema = _recursive_ema(closes, slow_period)

    dif: list[float | None] = [
        fast - slow
        if index >= slow_period - 1
        else None
        for index, (fast, slow) in enumerate(zip(fast_ema, slow_ema, strict=True))
    ]
    available_dif = [value for value in dif if value is not None]
    available_dea = _recursive_ema(available_dif, signal_period)
    dea: list[float | None] = [None] * len(dif)
    first_dif_index = slow_period - 1
    for offset, value in enumerate(available_dea):
        if offset >= signal_period - 1:
            dea[first_dif_index + offset] = value

    histogram = [
        2.0 * (dif_value - dea_value)
        if dif_value is not None and dea_value is not None
        else None
        for dif_value, dea_value in zip(dif, dea, strict=True)
    ]
    return {
        "fast_period": fast_period,
        "slow_period": slow_period,
        "signal_period": signal_period,
        "dif": _points(timestamps, dif),
        "dea": _points(timestamps, dea),
        "histogram": _points(timestamps, histogram),
    }


def calculate_volume_indicators(
    bars: Sequence[Bar],
    *,
    ma_periods: Sequence[int] = VOLUME_MA_PERIODS,
) -> dict[str, list[IndicatorPoint]]:
    """Return raw volume points and requested simple volume averages."""

    timestamps, volumes = _extract_series(bars, "volume", non_negative=True)
    result = {"values": _points(timestamps, volumes)}
    for period in ma_periods:
        result[f"ma{period}"] = _points(
            timestamps,
            _simple_moving_average(volumes, period),
        )
    return result


def calculate_intraday_vwap(bars: Sequence[Bar]) -> list[IndicatorPoint]:
    """Return cumulative amount/volume VWAP, resetting at each trade date.

    A trade date with zero cumulative volume yields ``None``. Once cumulative
    volume is positive, a zero-volume and zero-amount bar retains the current
    cumulative VWAP. A zero-volume bar with positive amount is rejected as
    inconsistent market data. Reported bar ``amount`` is used directly;
    close-price approximation is intentionally not supported.
    """

    timestamps, volumes = _extract_series(bars, "volume", non_negative=True)
    _, amounts = _extract_series(bars, "amount", non_negative=True)
    values: list[float | None] = []
    current_date: str | None = None
    cumulative_volume = 0.0
    cumulative_amount = 0.0

    for timestamp, volume, amount in zip(
        timestamps, volumes, amounts, strict=True
    ):
        if volume == 0 and amount != 0:
            raise IndicatorInputError(
                f"bar {timestamp} amount must be zero when volume is zero"
            )
        trade_date = timestamp[:10]
        if trade_date != current_date:
            current_date = trade_date
            cumulative_volume = 0.0
            cumulative_amount = 0.0
        cumulative_volume += volume
        cumulative_amount += amount
        values.append(
            cumulative_amount / cumulative_volume
            if cumulative_volume > 0
            else None
        )
    return _points(timestamps, values)


def calculate_five_minute_indicators(bars: Sequence[Bar]) -> dict[str, Any]:
    """Build the frozen logical-schema five-minute indicator object."""

    _validate_closed_bars(bars)
    return {
        "ma": {
            f"ma{period}": calculate_moving_average(bars, period)
            for period in MA_PERIODS
        },
        "boll": calculate_boll(bars),
        "volume": calculate_volume_indicators(bars),
        "macd": calculate_macd(bars),
    }


def calculate_one_minute_indicators(bars: Sequence[Bar]) -> dict[str, Any]:
    """Build the frozen logical-schema one-minute indicator object."""

    _validate_timestamps(bars)
    timestamps, volumes = _extract_series(bars, "volume", non_negative=True)
    return {
        "vwap": calculate_intraday_vwap(bars),
        "volume": {"values": _points(timestamps, volumes)},
        "macd": calculate_macd(bars),
    }


def _extract_series(
    bars: Sequence[Bar],
    field: str,
    *,
    non_negative: bool = False,
) -> tuple[list[str], list[float]]:
    timestamps = _validate_timestamps(bars)
    values: list[float] = []
    for index, bar in enumerate(bars):
        if field not in bar:
            raise IndicatorInputError(f"bar {index} is missing {field}")
        value = bar[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise IndicatorInputError(f"bar {index} {field} must be a finite number")
        if non_negative and value < 0:
            raise IndicatorInputError(f"bar {index} {field} must be non-negative")
        values.append(float(value))
    return timestamps, values


def _validate_timestamps(bars: Sequence[Bar]) -> list[str]:
    timestamps: list[str] = []
    previous: str | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, Mapping):
            raise IndicatorInputError(f"bar {index} must be a mapping")
        timestamp = bar.get("timestamp")
        if not isinstance(timestamp, str) or len(timestamp) < 10:
            raise IndicatorInputError(
                f"bar {index} timestamp must be a non-empty market timestamp"
            )
        if previous is not None and timestamp <= previous:
            raise IndicatorInputError(
                "bar timestamps must be unique and strictly increasing"
            )
        timestamps.append(timestamp)
        previous = timestamp
    return timestamps


def _validate_closed_bars(bars: Sequence[Bar]) -> None:
    """Reject dynamic bars at the formal five-minute indicator boundary."""

    _validate_timestamps(bars)
    for index, bar in enumerate(bars):
        closed = bar.get("closed")
        if not isinstance(closed, bool):
            raise IndicatorInputError(
                f"bar {index} closed must be supplied explicitly"
            )
        if not closed:
            raise IndicatorInputError(
                f"bar {index} must be formally closed before indicator calculation"
            )


def _simple_moving_average(
    values: Sequence[float],
    period: int,
) -> list[float | None]:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise IndicatorInputError("moving-average period must be a positive integer")
    return [
        math.fsum(values[index - period + 1 : index + 1]) / period
        if index >= period - 1
        else None
        for index in range(len(values))
    ]


def _recursive_ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    ema = float(values[0])
    result = [ema]
    for value in values[1:]:
        ema = alpha * value + (1.0 - alpha) * ema
        result.append(ema)
    return result


def _validate_macd_periods(
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> None:
    periods = (fast_period, slow_period, signal_period)
    if any(
        isinstance(period, bool) or not isinstance(period, int) or period <= 0
        for period in periods
    ):
        raise IndicatorInputError("MACD periods must be positive integers")
    if fast_period >= slow_period:
        raise IndicatorInputError("MACD fast period must be less than slow period")


def _points(
    timestamps: Sequence[str],
    values: Sequence[float | None],
) -> list[IndicatorPoint]:
    if len(timestamps) != len(values):
        raise AssertionError("indicator values lost timestamp alignment")
    return [
        {"timestamp": timestamp, "value": value}
        for timestamp, value in zip(timestamps, values, strict=True)
    ]
