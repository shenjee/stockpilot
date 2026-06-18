"""Signal replay subsystem for chantheory.

This module handles signal configuration normalization, bar-by-bar replay,
and construction of signal series, events, and snapshots.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .config import get_default_signals_config
from .schema import (
    AnalysisWarning,
    SignalEvent,
    SignalSeries,
    SignalSeriesPoint,
    SignalSnapshot,
    Stroke,
)
from .structure_mapping import (
    normalize_direction,
    safe_get,
    to_float,
    to_timestamp,
)


# ---------------------------------------------------------------------------
# Warning helper (re-exported from adapters for now; will be moved later)
# ---------------------------------------------------------------------------

def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )


# ---------------------------------------------------------------------------
# Signal configuration normalization
# ---------------------------------------------------------------------------

def normalize_signals_config(
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> List[Dict[str, Any]]:
    """Normalise *signals_config* into a list of signal definition dicts."""
    raw_items: Sequence[object] | None
    if signals_config is None:
        raw_items = get_default_signals_config()
    elif isinstance(signals_config, Mapping):
        raw_value = signals_config.get("signals", [])
        if not isinstance(raw_value, Sequence) or isinstance(raw_value, (str, bytes)):
            raise ValueError("`signals` must be a list when signals_config is a mapping.")
        raw_items = list(raw_value)
    elif isinstance(signals_config, Sequence) and not isinstance(signals_config, (str, bytes)):
        raw_items = list(signals_config)
    else:
        raise TypeError("signals_config must be a list or a mapping with a `signals` list.")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if isinstance(item, str):
            name = item.strip()
            if not name:
                raise ValueError(f"signals_config[{index}] is empty.")
            normalized.append(
                {
                    "module": "czsc.signals",
                    "name": name,
                    "key": name,
                    "di": 1,
                    "kwargs": {},
                }
            )
            continue

        if not isinstance(item, Mapping):
            raise TypeError(f"signals_config[{index}] must be a string or mapping.")
        if item.get("enabled", True) is False:
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"signals_config[{index}] is missing `name`.")
        kwargs = dict(item.get("kwargs", {}))
        if not isinstance(kwargs, Mapping):
            raise ValueError(f"signals_config[{index}].kwargs must be a mapping.")

        for k, v in item.items():
            if k not in ("module", "name", "key", "alias", "enabled", "kwargs"):
                kwargs[k] = v
        di = int(kwargs.pop("di", 1))

        normalized.append(
            {
                "module": str(item.get("module") or "czsc.signals"),
                "name": name,
                "key": str(item.get("key") or item.get("alias") or _default_signal_key(name=name, kwargs=kwargs)),
                "di": di,
                "kwargs": kwargs,
            }
        )

    return normalized


def _default_signal_key(name: str, kwargs: Mapping[str, Any]) -> str:
    key_parts = [name]
    freq = kwargs.get("freq")
    if freq not in (None, ""):
        key_parts.insert(0, str(freq))
    for key in sorted(k for k in kwargs.keys() if k != "freq"):
        value = kwargs[key]
        if value in (None, ""):
            continue
        key_parts.append(f"{key}={value}")
    return "_".join(key_parts)


# ---------------------------------------------------------------------------
# Signal evaluation helpers
# ---------------------------------------------------------------------------

def _evaluate_signal_function(func: Any, analyzer: object, di: int, kwargs: Mapping[str, Any]) -> str:
    result = func(analyzer, di=di, **dict(kwargs))
    return _extract_signal_value(result)


def _extract_signal_value(result: Any) -> str:
    if not isinstance(result, Mapping) or not result:
        return ""
    return str(next(iter(result.values()), "")).strip()


def _signal_value_is_active(value: str) -> bool:
    return bool(value) and not value.startswith("其他")


# ---------------------------------------------------------------------------
# Signal payload builder (main entry point for signal replay)
# ---------------------------------------------------------------------------

def build_signal_payloads(
    strokes: Sequence[Stroke],
    analyzer: object,
    index_by_timestamp: Mapping[str, int],
    signals_config: Sequence[object] | Mapping[str, object] | None,
    raw_bars: Sequence[object] | None = None,
) -> Tuple[List[Dict[str, Any]], List[SignalSeries], List[SignalEvent], List[SignalSnapshot], List[AnalysisWarning], List[Dict[str, Any]]]:
    """Run bar-by-bar signal replay and return evaluations, series, events, snapshots, warnings, and resolved config."""
    warnings: List[AnalysisWarning] = []
    try:
        signal_definitions = normalize_signals_config(signals_config)
    except (TypeError, ValueError) as exc:
        warnings.append(
            _warning(
                warning_id="warning_invalid_signals_config",
                code="INVALID_SIGNALS_CONFIG",
                message=f"signals_config is invalid and has been ignored: {exc}",
                field="signals_config",
            )
        )
        return [], [], [], [], warnings, []

    if not signal_definitions:
        return [], [], [], [], warnings, []

    bars_raw = list(raw_bars) if raw_bars is not None else list(getattr(analyzer, "bars_raw", []) or [])
    if not bars_raw:
        return [], [], [], [], warnings, signal_definitions

    strokes_by_end_ts = {stroke.end_timestamp: stroke for stroke in strokes}
    module_cache: Dict[str, object] = {}
    missing_modules: set[str] = set()
    missing_functions: set[tuple[str, str]] = set()
    failed_functions: set[tuple[str, str]] = set()
    evaluations: List[Dict[str, Any]] = []

    try:
        CZSC_cls = type(analyzer)
        replay_analyzer = CZSC_cls(bars_raw[:1], max_bi_num=getattr(analyzer, "max_bi_num", 50))
    except Exception:
        replay_analyzer = analyzer

    for i, bar in enumerate(bars_raw):
        if i > 0:
            replay_analyzer.update(bar)

        end_ts = to_timestamp(bar)
        stroke = strokes_by_end_ts.get(end_ts)

        if stroke:
            direction = stroke.direction
            reference_id = stroke.id
            price = stroke.end_price
        else:
            bi_list = list(getattr(replay_analyzer, "bi_list", []) or [])
            bi = bi_list[-1] if bi_list else None
            if bi:
                direction = normalize_direction(safe_get(bi, "direction", default=""))
                fx_a = safe_get(bi, "fx_a")
                start_ts = to_timestamp(fx_a) if fx_a else end_ts
            else:
                direction = ""
                start_ts = end_ts

            price = to_float(getattr(bar, "close", 0.0))
            reference_id = f"stroke_pending_{start_ts}_{end_ts}"

        bar_index = int(index_by_timestamp.get(end_ts, -1))
        for definition in signal_definitions:
            module_name = str(definition["module"])
            signal_name = str(definition["name"])
            signal_key = str(definition["key"])
            kwargs = dict(definition.get("kwargs", {}))
            di = int(definition.get("di", 1))

            if module_name in missing_modules:
                continue
            module = module_cache.get(module_name)
            if module is None:
                try:
                    module = import_module(module_name)
                    module_cache[module_name] = module
                except Exception as exc:
                    if module_name not in missing_modules:
                        missing_modules.add(module_name)
                        warnings.append(
                            _warning(
                                warning_id=f"warning_signal_module_missing_{module_name}",
                                code="SIGNAL_MODULE_UNAVAILABLE",
                                message=f"Signal module `{module_name}` could not be imported: {exc}",
                                field="signals",
                            )
                        )
                    continue

            func = getattr(module, signal_name, None)
            if func is None:
                missing_key = (module_name, signal_name)
                if missing_key not in missing_functions:
                    missing_functions.add(missing_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_function_missing_{signal_name}",
                            code="SIGNAL_FUNCTION_UNAVAILABLE",
                            message=f"Signal function `{module_name}.{signal_name}` is not available in the installed czsc package.",
                            field="signals",
                        )
                    )
                continue

            try:
                signal_value = _evaluate_signal_function(func=func, analyzer=replay_analyzer, di=di, kwargs=kwargs)
                status = "active" if _signal_value_is_active(signal_value) else "inactive"
            except Exception as exc:
                signal_value = ""
                status = "not_ready" if isinstance(exc, (IndexError, KeyError, AttributeError)) else "error"
                failed_key = (module_name, signal_name)
                if failed_key not in failed_functions:
                    failed_functions.add(failed_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_eval_failed_{signal_name}",
                            code="SIGNAL_EVALUATION_FAILED",
                            message=f"Signal function `{module_name}.{signal_name}` {status}: {exc}",
                            field="signals",
                        )
                    )

            evaluations.append(
                {
                    "signal_key": signal_key,
                    "signal_name": signal_name,
                    "module": module_name,
                    "timestamp": end_ts,
                    "bar_index": bar_index,
                    "reference_id": reference_id,
                    "price": price,
                    "direction": direction,
                    "value": signal_value,
                    "active": status == "active",
                    "status": status,
                    "di": di,
                    "meta": {
                        "kwargs": kwargs,
                    },
                }
            )

    return (
        evaluations,
        build_signal_series(evaluations=evaluations, signal_definitions=signal_definitions),
        build_signal_events(evaluations=evaluations),
        build_signal_snapshots(evaluations=evaluations),
        warnings,
        signal_definitions,
    )


# ---------------------------------------------------------------------------
# Signal series / events / snapshots builders
# ---------------------------------------------------------------------------

def _evaluation_status(evaluation: Mapping[str, Any]) -> str:
    """Resolve the status of an evaluation, deriving from the active flag when absent.

    Legacy or hand-built evaluations may omit the ``status`` field; fall back to
    ``active``/``inactive`` based on the ``active`` flag instead of assuming
    ``active`` (which would mislabel inactive evaluations).
    """
    return str(evaluation.get("status") or ("active" if evaluation.get("active") else "inactive"))


def build_signal_series(
    evaluations: Sequence[Mapping[str, Any]],
    signal_definitions: Sequence[Mapping[str, Any]],
) -> List[SignalSeries]:
    by_key: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in evaluations:
        by_key.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    items: List[SignalSeries] = []
    for definition in signal_definitions:
        signal_key = str(definition["key"])
        points = [
            SignalSeriesPoint(
                timestamp=str(evaluation["timestamp"]),
                bar_index=int(evaluation["bar_index"]),
                value=str(evaluation["value"]),
                active=bool(evaluation["active"]),
                status=_evaluation_status(evaluation),
                price=float(evaluation["price"]) if evaluation.get("price") is not None else None,
                reference_id=str(evaluation["reference_id"]),
                meta={
                    "direction": evaluation.get("direction", ""),
                },
            )
            for evaluation in by_key.get(signal_key, [])
        ]
        latest = points[-1] if points else None
        items.append(
            SignalSeries(
                signal_key=signal_key,
                signal_name=str(definition["name"]),
                module=str(definition["module"]),
                latest_value=latest.value if latest else "",
                latest_timestamp=latest.timestamp if latest else "",
                points=points,
                meta={
                    "active_point_count": sum(1 for point in points if point.active),
                },
            )
        )
    return items


def build_signal_events(evaluations: Sequence[Mapping[str, Any]]) -> List[SignalEvent]:
    by_key: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in evaluations:
        by_key.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    events: List[SignalEvent] = []
    for signal_key, items in by_key.items():
        previous: Mapping[str, Any] | None = None
        for item in items:
            current_active = bool(item["active"])
            previous_active = bool(previous["active"]) if previous is not None else False
            current_value = str(item["value"])
            previous_value = str(previous["value"]) if previous is not None else ""
            event_type = ""
            if previous is None and current_active:
                event_type = "triggered"
            elif not previous_active and current_active:
                event_type = "triggered"
            elif previous_active and current_active and current_value != previous_value:
                event_type = "switched"
            elif previous_active and not current_active:
                event_type = "invalidated"

            if event_type:
                events.append(
                    SignalEvent(
                        id=f"signal_event_{signal_key}_{item['bar_index']}_{event_type}",
                        signal_key=signal_key,
                        signal_name=str(item["signal_name"]),
                        module=str(item["module"]),
                        event_type=event_type,
                        timestamp=str(item["timestamp"]),
                        bar_index=int(item["bar_index"]),
                        value=current_value,
                        active=current_active,
                        status=_evaluation_status(item),
                        reference_id=str(item["reference_id"]),
                        price=float(item["price"]) if item.get("price") is not None else None,
                        meta={
                            "previous_value": previous_value,
                            "direction": item.get("direction", ""),
                        },
                    )
                )
            previous = item

    events.sort(key=lambda item: (item.bar_index, item.signal_key, item.event_type))
    return events


def build_signal_snapshots(evaluations: Sequence[Mapping[str, Any]]) -> List[SignalSnapshot]:
    snapshots_by_key: Dict[tuple[str, int, str], Dict[str, Any]] = {}
    for evaluation in evaluations:
        snapshot_key = (
            str(evaluation["timestamp"]),
            int(evaluation["bar_index"]),
            str(evaluation["reference_id"]),
        )
        snapshot = snapshots_by_key.setdefault(
            snapshot_key,
            {
                "timestamp": str(evaluation["timestamp"]),
                "bar_index": int(evaluation["bar_index"]),
                "reference_id": str(evaluation["reference_id"]),
                "price": float(evaluation["price"]) if evaluation.get("price") is not None else None,
                "values": {},
                "active_signals": {},
                "statuses": {},
                "signal_names": {},
            },
        )
        signal_key = str(evaluation["signal_key"])
        signal_value = str(evaluation["value"])
        snapshot["values"][signal_key] = signal_value
        snapshot["signal_names"][signal_key] = str(evaluation["signal_name"])
        snapshot["statuses"][signal_key] = _evaluation_status(evaluation)
        if bool(evaluation["active"]):
            snapshot["active_signals"][signal_key] = signal_value

    snapshots = [
        SignalSnapshot(
            id=f"signal_snapshot_{item['bar_index']}_{item['reference_id']}",
            timestamp=str(item["timestamp"]),
            bar_index=int(item["bar_index"]),
            values=dict(item["values"]),
            active_signals=dict(item["active_signals"]),
            statuses=dict(item["statuses"]),
            reference_id=str(item["reference_id"]),
            price=float(item["price"]) if item.get("price") is not None else None,
            meta={
                "signal_names": dict(item["signal_names"]),
            },
        )
        for item in sorted(snapshots_by_key.values(), key=lambda current: (current["bar_index"], current["reference_id"]))
    ]
    return snapshots
