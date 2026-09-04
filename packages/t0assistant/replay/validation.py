"""Lightweight runtime validation for the frozen Replay v1.0 snapshot."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any


def validate_replay_snapshot(
    snapshot: object,
    *,
    expected_session_id: str,
) -> None:
    """Raise ``TypeError`` when a snapshot violates Replay v1.0."""

    root = _mapping(
        snapshot,
        "snapshot",
        {
            "timezone", "session", "replay", "market", "indicators",
            "chan_analysis", "chan_analysis_30m", "warnings",
        },
    )
    if root["timezone"] != "Asia/Shanghai":
        raise TypeError("snapshot.timezone must be Asia/Shanghai")
    _session(root["session"], expected_session_id)
    _replay(root["replay"])
    _market(root["market"])
    _indicators(root["indicators"])
    _chan_analysis(root["chan_analysis"])
    _chan_analysis_30m(root["chan_analysis_30m"])
    warnings = _array(root["warnings"], "snapshot.warnings")
    for index, warning in enumerate(warnings):
        _warning(warning, f"snapshot.warnings[{index}]")


def _session(value: object, expected_session_id: str) -> None:
    path = "snapshot.session"
    session = _mapping(
        value,
        path,
        {"session_id", "session_type", "symbol", "trade_date", "state", "revision"},
    )
    if session["session_id"] != expected_session_id:
        raise TypeError(f"{path}.session_id does not match the request")
    if session["session_type"] != "replay":
        raise TypeError(f"{path}.session_type must be replay")
    _symbol(session["symbol"], f"{path}.symbol")
    _datetime(session["trade_date"], f"{path}.trade_date", "%Y-%m-%d")
    if session["state"] not in {
        "loading", "ready", "playing", "paused", "failed", "retired"
    }:
        raise TypeError(f"{path}.state is invalid")
    _integer(session["revision"], f"{path}.revision", minimum=0)


def _replay(value: object) -> None:
    path = "snapshot.replay"
    replay = _mapping(
        value,
        path,
        {
            "granularity", "current_time", "next_bar_time", "start_time",
            "end_time", "playing", "playback_speed", "step_seconds",
        },
    )
    if replay["granularity"] not in {"one_minute", "five_minute"}:
        raise TypeError(f"{path}.granularity is invalid")
    for field in ("current_time", "start_time", "end_time"):
        _datetime(replay[field], f"{path}.{field}", "%Y-%m-%d %H:%M:%S")
    if replay["next_bar_time"] is not None:
        _datetime(
            replay["next_bar_time"],
            f"{path}.next_bar_time",
            "%Y-%m-%d %H:%M:%S",
        )
    if type(replay["playing"]) is not bool:
        raise TypeError(f"{path}.playing must be boolean")
    if (
        type(replay["playback_speed"]) is not int
        or replay["playback_speed"] not in {1, 2, 5, 10}
    ):
        raise TypeError(f"{path}.playback_speed is invalid")
    if (
        type(replay["step_seconds"]) is not int
        or replay["step_seconds"] not in {60, 300}
    ):
        raise TypeError(f"{path}.step_seconds is invalid")


def _market(value: object) -> None:
    path = "snapshot.market"
    market = _mapping(
        value, path, {"bars_1m", "bars_5m", "bars_30m", "daily_bars", "quote"}
    )
    for field in ("bars_1m", "bars_5m", "bars_30m", "daily_bars"):
        for index, bar in enumerate(_array(market[field], f"{path}.{field}")):
            _bar(bar, f"{path}.{field}[{index}]")
    if market["quote"] is not None:
        _quote(market["quote"], f"{path}.quote")


def _bar(value: object, path: str) -> None:
    bar = _mapping(
        value,
        path,
        {"timestamp", "open", "high", "low", "close", "volume", "amount", "closed"},
    )
    if not isinstance(bar["timestamp"], str) or len(bar["timestamp"]) < 10:
        raise TypeError(f"{path}.timestamp is invalid")
    for field in ("open", "high", "low", "close"):
        _number(bar[field], f"{path}.{field}", minimum=0)
    for field in ("volume", "amount"):
        if bar[field] is not None:
            _number(bar[field], f"{path}.{field}", minimum=0)
    if type(bar["closed"]) is not bool:
        raise TypeError(f"{path}.closed must be boolean")


def _quote(value: object, path: str) -> None:
    quote = _mapping(
        value,
        path,
        {
            "timestamp", "latest_price", "change_percent", "open", "high", "low",
            "previous_close", "volume", "amount", "volume_ratio",
            "order_imbalance", "turnover_rate",
        },
    )
    _datetime(quote["timestamp"], f"{path}.timestamp", "%Y-%m-%d %H:%M:%S")
    for field in ("latest_price", "open", "high", "low", "previous_close"):
        _number(quote[field], f"{path}.{field}", minimum=0)
    for field in ("volume", "amount"):
        if quote[field] is not None:
            _number(quote[field], f"{path}.{field}", minimum=0)
    _number(quote["change_percent"], f"{path}.change_percent")
    for field in ("volume_ratio", "order_imbalance", "turnover_rate"):
        if quote[field] is not None:
            _number(quote[field], f"{path}.{field}")


def _indicators(value: object) -> None:
    path = "snapshot.indicators"
    indicators = _mapping(
        value, path, {"five_minute", "thirty_minute", "one_minute"}
    )
    five = _mapping(
        indicators["five_minute"],
        f"{path}.five_minute",
        {"ma", "boll", "volume", "macd"},
    )
    _indicator_block(five, f"{path}.five_minute")
    thirty = _mapping(
        indicators["thirty_minute"],
        f"{path}.thirty_minute",
        {"ma", "boll", "volume", "macd"},
    )
    _indicator_block(thirty, f"{path}.thirty_minute")

    one = _mapping(
        indicators["one_minute"],
        f"{path}.one_minute",
        {"vwap", "volume", "macd"},
    )
    _points(one["vwap"], f"{path}.one_minute.vwap")
    one_volume = _mapping(
        one["volume"], f"{path}.one_minute.volume", {"values"}
    )
    _points(one_volume["values"], f"{path}.one_minute.volume.values")
    _macd(one["macd"], f"{path}.one_minute.macd")


def _macd(value: object, path: str) -> None:
    macd = _mapping(
        value,
        path,
        {"fast_period", "slow_period", "signal_period", "dif", "dea", "histogram"},
    )
    if (
        macd["fast_period"] != 12
        or macd["slow_period"] != 26
        or macd["signal_period"] != 9
    ):
        raise TypeError(f"{path} parameters are invalid")
    for field in ("dif", "dea", "histogram"):
        _points(macd[field], f"{path}.{field}")


def _indicator_block(block: Mapping[str, Any], path: str) -> None:
    ma = _mapping(
        block["ma"],
        f"{path}.ma",
        {"ma5", "ma10", "ma20", "ma30", "ma60"},
    )
    for field, points in ma.items():
        _points(points, f"{path}.ma.{field}")
    boll = _mapping(
        block["boll"],
        f"{path}.boll",
        {"period", "stddev", "upper", "middle", "lower"},
    )
    if boll["period"] != 20 or boll["stddev"] != 2.0:
        raise TypeError(f"{path}.boll parameters are invalid")
    for field in ("upper", "middle", "lower"):
        _points(boll[field], f"{path}.boll.{field}")
    volume = _mapping(
        block["volume"],
        f"{path}.volume",
        {"values", "ma5", "ma10"},
    )
    for field in ("values", "ma5", "ma10"):
        _points(volume[field], f"{path}.volume.{field}")
    macd = _mapping(
        block["macd"],
        f"{path}.macd",
        {"fast_period", "slow_period", "signal_period", "dif", "dea", "histogram"},
    )
    if macd["fast_period"] != 12 or macd["slow_period"] != 26 or macd["signal_period"] != 9:
        raise TypeError(f"{path}.macd parameters are invalid")
    for field in ("dif", "dea", "histogram"):
        _points(macd[field], f"{path}.macd.{field}")


def _points(value: object, path: str) -> None:
    for index, point in enumerate(_array(value, path)):
        item_path = f"{path}[{index}]"
        item = _mapping(point, item_path, {"timestamp", "value"})
        if not isinstance(item["timestamp"], str) or len(item["timestamp"]) < 10:
            raise TypeError(f"{item_path}.timestamp is invalid")
        if item["value"] is not None:
            _number(item["value"], f"{item_path}.value")


def _chan_analysis(value: object) -> None:
    _chan_analysis_at(value, "snapshot.chan_analysis")


def _chan_analysis_30m(value: object) -> None:
    _chan_analysis_at(value, "snapshot.chan_analysis_30m")


def _chan_analysis_at(value: object, path: str) -> None:
    fields = {
        "symbol", "timeframe", "source", "engine", "engine_version", "parameters",
        "fractals", "strokes", "segments", "pivot_zones", "divergences",
        "structure_alerts", "signal_series", "signal_events", "signal_snapshots",
        "candidate_point_events", "candidate_buy_points", "candidate_sell_points",
        "plot_primitives", "summary", "warnings", "meta",
    }
    chan = _mapping(value, path, fields)
    for field in ("symbol", "timeframe", "source", "engine_version"):
        if not isinstance(chan[field], str) or not chan[field]:
            raise TypeError(f"{path}.{field} must be a non-empty string")
    if chan["engine"] != "czsc":
        raise TypeError(f"{path}.engine must be czsc")
    for field in ("parameters", "meta"):
        if not isinstance(chan[field], Mapping):
            raise TypeError(f"{path}.{field} must be an object")
    object_arrays = fields - {
        "symbol", "timeframe", "source", "engine", "engine_version",
        "parameters", "summary", "meta",
    }
    for field in object_arrays:
        for index, item in enumerate(_array(chan[field], f"{path}.{field}")):
            if not isinstance(item, Mapping):
                raise TypeError(f"{path}.{field}[{index}] must be an object")
    for index, item in enumerate(_array(chan["summary"], f"{path}.summary")):
        if not isinstance(item, str):
            raise TypeError(f"{path}.summary[{index}] must be a string")


def _warning(value: object, path: str) -> None:
    warning = _mapping(
        value,
        path,
        {
            "warning_code", "severity", "message", "affected_capability",
            "affected_field", "details",
        },
    )
    for field in ("warning_code", "message"):
        if not isinstance(warning[field], str) or not warning[field]:
            raise TypeError(f"{path}.{field} must be a non-empty string")
    if warning["severity"] not in {"info", "warning"}:
        raise TypeError(f"{path}.severity is invalid")
    if warning["affected_capability"] not in {
        "replay",
        "intraday_chart",
        "five_minute_chart",
        "thirty_minute_chart",
        "chan_analysis",
    }:
        raise TypeError(f"{path}.affected_capability is invalid")
    if not isinstance(warning["affected_field"], str):
        raise TypeError(f"{path}.affected_field must be a string")
    if not isinstance(warning["details"], Mapping):
        raise TypeError(f"{path}.details must be an object")


def _mapping(
    value: object,
    path: str,
    fields: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be an object")
    if set(value) != fields:
        raise TypeError(f"{path} fields do not match Replay v1.0")
    return value


def _array(value: object, path: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{path} must be an array")
    return value


def _integer(value: object, path: str, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise TypeError(f"{path} must be an integer >= {minimum}")


def _number(value: object, path: str, *, minimum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be numeric")
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise TypeError(f"{path} is outside the allowed range")


def _datetime(value: object, path: str, pattern: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    try:
        parsed = datetime.strptime(value, pattern)
    except ValueError as error:
        raise TypeError(f"{path} has an invalid format") from error
    if parsed.strftime(pattern) != value:
        raise TypeError(f"{path} has an invalid format")


def _symbol(value: object, path: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 9
        or value[:3] not in {"sh.", "sz."}
        or not value[3:].isdigit()
    ):
        raise TypeError(f"{path} is not a standard security symbol")
