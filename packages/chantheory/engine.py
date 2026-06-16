from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any, Dict, List, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_freq_name
from .schema import NormalizationResult


class EngineImportError(RuntimeError):
    pass


def load_czsc() -> Tuple[object, object, object]:
    # Load numpy.typing first so rs_czsc-backed imports initialize consistently.
    import_module("numpy.typing")
    try:
        czsc = import_module("czsc")
    except ImportError as exc:
        raise EngineImportError(str(exc)) from exc

    def _import_attr(module_name: str, attr_name: str) -> object | None:
        try:
            module = import_module(module_name)
        except ImportError:
            return None
        return getattr(module, attr_name, None)

    # Prefer the pure-Python CZSC path. The top-level rs_czsc-backed RawBar
    # converts date-only daily bars to pandas timestamps such as the previous
    # day 16:00, which makes Chan structures fall off the K-line trading dates.
    py_raw_bar = _import_attr("czsc.py.objects", "RawBar")
    py_freq = _import_attr("czsc.py.objects", "Freq")
    py_czsc = _import_attr("czsc.py.analyze", "CZSC")
    if py_raw_bar is not None and py_freq is not None and py_czsc is not None:
        return py_raw_bar, py_freq, py_czsc

    # czsc 0.10.x exports these symbols from the package root / core module,
    # while older releases exposed RawBar from czsc.objects.
    raw_bar_candidates = (
        getattr(czsc, "RawBar", None),
        _import_attr("czsc.core", "RawBar"),
        _import_attr("czsc.py.objects", "RawBar"),
    )
    RawBar = next((candidate for candidate in raw_bar_candidates if candidate is not None), None)
    if RawBar is None:
        try:
            RawBar = getattr(import_module("czsc.objects"), "RawBar")
        except ImportError as exc:
            raise EngineImportError("Unable to resolve czsc.RawBar from the installed czsc package") from exc

    Freq = getattr(czsc, "Freq", None) or _import_attr("czsc.core", "Freq")
    CZSC = getattr(czsc, "CZSC", None) or _import_attr("czsc.core", "CZSC")
    return RawBar, Freq, CZSC


def load_czsc_utils() -> object:
    return import_module("czsc.utils.sig")


def run_engine(
    normalized: NormalizationResult,
    parameters: Dict[str, Any],
) -> Tuple[object, list]:
    RawBar, Freq, CZSC = load_czsc()
    freq = getattr(Freq, get_freq_name(normalized.timeframe))
    raw_bars = []

    for bar in normalized.bars:
        raw_bars.append(
            RawBar(
                symbol=bar.symbol,
                id=bar.bar_index,
                dt=parse_dt(bar.timestamp),
                freq=freq,
                open=bar.open,
                close=bar.close,
                high=bar.high,
                low=bar.low,
                vol=bar.volume,
                amount=bar.amount,
            )
        )

    analyzer = CZSC(raw_bars, max_bi_num=int(parameters["max_bi_num"]))
    return analyzer, raw_bars


def parse_dt(value: str) -> datetime:
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
