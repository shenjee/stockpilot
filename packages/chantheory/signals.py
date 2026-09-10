"""Signal replay subsystem for chantheory.

This module handles signal configuration normalization, bar-by-bar replay,
and construction of signal series, events, and snapshots.

czsc 1.0.1 replaced the pure-Python ``czsc.signals.cxt`` module with the
Rust-native ``czsc._native.call_signal`` dispatcher. Signal evaluation now
goes through :func:`czsc._native.call_signal(name, czsc, params)`, which
returns a ``list[Signal]``. Each ``Signal`` exposes ``.key`` and ``.value``
attributes; ``.value`` uses the same ``v1_v2_v3_score`` format that the old
OrderedDict values used, so the downstream series/events/snapshots contracts
are unchanged.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Dict, List, Mapping, Protocol, Sequence, Tuple, runtime_checkable

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
# Signal dispatcher abstraction
# ---------------------------------------------------------------------------

@runtime_checkable
class SignalDispatcher(Protocol):
    """Callable contract for evaluating a named signal against a CZSC analyzer.

    A dispatcher takes ``(signal_name, analyzer, params)`` and returns a list
    of signal-result objects. Each result must expose a ``value`` attribute
    (string in ``v1_v2_v3_score`` format). The default implementation wraps
    :func:`czsc._native.call_signal`; tests may substitute a mock dispatcher.
    """

    def __call__(self, signal_name: str, analyzer: object, params: Mapping[str, Any] | None) -> List[Any]:
        ...


def _get_default_dispatcher() -> SignalDispatcher:
    """Return the default signal dispatcher backed by ``czsc._native.call_signal``.

    Imported lazily so that unit tests can patch ``chantheory.signals`` without
    importing czsc. Raises a clear diagnostic if the native extension is
    unavailable (e.g. czsc < 1.0 installed).
    """
    try:
        czsc = import_module("czsc")
    except ImportError as exc:
        raise EngineImportError(f"czsc could not be imported: {exc}") from exc
    native = getattr(czsc, "_native", None)
    call_signal = getattr(native, "call_signal", None) if native is not None else None
    if call_signal is None:
        raise EngineImportError(
            "czsc._native.call_signal is unavailable; czsc >= 1.0 is required "
            "for signal evaluation. The old czsc.signals.cxt module was removed."
        )

    def _dispatch(signal_name: str, analyzer: object, params: Mapping[str, Any] | None) -> List[Any]:
        return call_signal(signal_name, analyzer, dict(params) if params else None)

    return _dispatch


# Re-exported so callers (and tests) can catch the same error type used by
# the engine module without importing it directly.
from .engine import EngineImportError  # noqa: E402  (circular-safe: engine has no runtime dep on signals)


# ---------------------------------------------------------------------------
# Signal configuration normalization
# ---------------------------------------------------------------------------

def normalize_signals_config(
    signals_config: Sequence[object] | Mapping[str, object] | None,
) -> List[Dict[str, Any]]:
    """Normalise *signals_config* into a list of signal definition dicts.

    Each definition dict has the keys:
    - ``module``: lineage/dispatch source (``czsc._native`` for the default
      dispatcher; may be a custom module name for test mocks).
    - ``name``: the registered signal name (e.g. ``cxt_first_buy_V221126``).
    - ``key``: the project-facing signal key (e.g. ``first_buy``).
    - ``di``: the lookback depth (defaults to 1).
    - ``kwargs``: extra params forwarded to the dispatcher (e.g. ``freq``,
      ``ma_type``, ``timeperiod``).
    """
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
                    "module": "czsc._native",
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
                "module": str(item.get("module") or "czsc._native"),
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

def _evaluate_signal_via_dispatcher(
    dispatcher: SignalDispatcher,
    signal_name: str,
    analyzer: object,
    di: int,
    kwargs: Mapping[str, Any],
) -> str:
    """Evaluate *signal_name* via *dispatcher* and extract the value string.

    The dispatcher returns a ``list[Signal]``; we take the first result's
    ``value`` attribute, which uses the same ``v1_v2_v3_score`` format as the
    old OrderedDict values. An empty string is returned when the result list
    is empty or the value is missing.
    """
    params: Dict[str, Any] = {"di": di}
    params.update(dict(kwargs))
    result = dispatcher(signal_name, analyzer, params)
    return _extract_signal_value(result)


def _extract_signal_value(result: Any) -> str:
    """Extract the signal value string from a dispatcher result.

    Supports both the new ``Signal`` objects (with ``.value`` attribute) and
    legacy OrderedDict results (for backward compatibility with custom
    dispatchers that return mappings).
    """
    if isinstance(result, Mapping) and result:
        return str(next(iter(result.values()), "")).strip()
    if isinstance(result, Sequence) and result:
        first = result[0]
        value = getattr(first, "value", None)
        if value is not None:
            return str(value).strip()
    return ""


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
    dispatcher: SignalDispatcher | None = None,
) -> Tuple[List[Dict[str, Any]], List[SignalSeries], List[SignalEvent], List[SignalSnapshot], List[AnalysisWarning], List[Dict[str, Any]]]:
    """Run bar-by-bar signal replay and return evaluations, series, events, snapshots, warnings, and resolved config.

    *dispatcher* defaults to the czsc 1.0.1 native ``call_signal`` dispatcher.
    Tests may pass a mock dispatcher to avoid importing czsc.
    """
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

    # Resolve the signal dispatcher. The default wraps
    # ``czsc._native.call_signal``; tests inject a mock. A dispatcher failure
    # (e.g. czsc < 1.0 without ``_native``) produces a single diagnostic
    # warning and skips all signal evaluation.
    if dispatcher is None:
        try:
            dispatcher = _get_default_dispatcher()
        except EngineImportError as exc:
            warnings.append(
                _warning(
                    warning_id="warning_signal_dispatcher_unavailable",
                    code="SIGNAL_DISPATCHER_UNAVAILABLE",
                    message=str(exc),
                    field="signals",
                )
            )
            return [], [], [], [], warnings, signal_definitions

    strokes_by_end_ts = {stroke.end_timestamp: stroke for stroke in strokes}
    unknown_signals: set[str] = set()
    failed_signals: set[tuple[str, str]] = set()
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

            if signal_name in unknown_signals:
                continue

            try:
                signal_value = _evaluate_signal_via_dispatcher(
                    dispatcher=dispatcher,
                    signal_name=signal_name,
                    analyzer=replay_analyzer,
                    di=di,
                    kwargs=kwargs,
                )
                status = "active" if _signal_value_is_active(signal_value) else "inactive"
            except KeyError as exc:
                # czsc._native.call_signal raises KeyError for unknown signal
                # names. Record once and skip for the rest of the replay.
                signal_value = ""
                status = "not_ready"
                if signal_name not in unknown_signals:
                    unknown_signals.add(signal_name)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_function_missing_{signal_name}",
                            code="SIGNAL_FUNCTION_UNAVAILABLE",
                            message=f"Signal `{signal_name}` is not registered in the czsc signal dispatcher: {exc}",
                            field="signals",
                        )
                    )
                continue
            except (IndexError, AttributeError) as exc:
                signal_value = ""
                status = "not_ready"
                failed_key = (module_name, signal_name)
                if failed_key not in failed_signals:
                    failed_signals.add(failed_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_eval_failed_{signal_name}",
                            code="SIGNAL_EVALUATION_FAILED",
                            message=f"Signal `{module_name}.{signal_name}` not_ready: {exc}",
                            field="signals",
                        )
                    )
            except Exception as exc:
                signal_value = ""
                status = "error"
                failed_key = (module_name, signal_name)
                if failed_key not in failed_signals:
                    failed_signals.add(failed_key)
                    warnings.append(
                        _warning(
                            warning_id=f"warning_signal_eval_failed_{signal_name}",
                            code="SIGNAL_EVALUATION_FAILED",
                            message=f"Signal `{module_name}.{signal_name}` error: {exc}",
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
