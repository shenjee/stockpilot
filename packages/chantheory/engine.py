from __future__ import annotations

import warnings
from datetime import datetime
from importlib import import_module
from typing import Any, Dict, List, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_freq_name
from .schema import NormalizationResult


class EngineImportError(RuntimeError):
    pass


class EngineVersionMismatchError(RuntimeError):
    """Raised when the installed czsc version differs from the pinned version."""


def _get_installed_version() -> str | None:
    """Return the installed czsc ``__version__`` string, or ``None``."""
    try:
        czsc = import_module("czsc")
    except ImportError:
        return None
    return getattr(czsc, "__version__", None)


def assert_engine_version() -> str:
    """Verify the installed czsc version matches the project pin.

    Returns the installed version string. Raises
    :class:`EngineVersionMismatchError` when the installed version differs
    from :data:`~chantheory.config.PINNED_ENGINE_VERSION`.
    """
    installed = _get_installed_version()
    if installed is None:
        raise EngineImportError("czsc is not installed or has no __version__ attribute")
    if installed != PINNED_ENGINE_VERSION:
        raise EngineVersionMismatchError(
            f"czsc version mismatch: installed {installed!r} != pinned {PINNED_ENGINE_VERSION!r}. "
            f"Install czsc=={PINNED_ENGINE_VERSION} or update the pin."
        )
    return installed


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

    # czsc 1.0+ exports RawBar/Freq/CZSC from the package root via the
    # built-in Rust extension (czsc._native). The old pure-Python czsc.py
    # path was removed in 1.0; the top-level exports are now the only entry.
    RawBar = getattr(czsc, "RawBar", None)
    Freq = getattr(czsc, "Freq", None)
    CZSC = getattr(czsc, "CZSC", None)

    # czsc 0.10.x did not export from the package root on all paths; fall
    # back to the sub-module locations that existed in that series.
    if RawBar is None:
        RawBar = _import_attr("czsc.py.objects", "RawBar") or _import_attr("czsc.core", "RawBar")
    if Freq is None:
        Freq = _import_attr("czsc.py.objects", "Freq") or _import_attr("czsc.core", "Freq")
    if CZSC is None:
        CZSC = _import_attr("czsc.py.analyze", "CZSC") or _import_attr("czsc.core", "CZSC")

    if RawBar is None:
        try:
            RawBar = getattr(import_module("czsc.objects"), "RawBar")
        except ImportError as exc:
            raise EngineImportError("Unable to resolve czsc.RawBar from the installed czsc package") from exc
    if Freq is None:
        Freq = getattr(import_module("czsc.objects"), "Freq")
    if CZSC is None:
        CZSC = getattr(import_module("czsc.analyze"), "CZSC")

    return RawBar, Freq, CZSC


def load_czsc_utils() -> object:
    """Return a namespace exposing ``get_zs_seq``.

    czsc 0.10.x exposed ``get_zs_seq`` via ``czsc.utils.sig``; 1.0+ moved it
    to the top-level ``czsc.get_zs_seq``. This loader returns a module-like
    object with a ``get_zs_seq`` attribute in both cases. Callers that need
    the pivot-zone sequence should prefer ``analyzer.zs_list`` (available on
    CZSC instances in 1.0+) and only fall back to ``get_zs_seq`` when the
    instance attribute is unavailable.
    """
    try:
        return import_module("czsc.utils.sig")
    except ImportError:
        pass
    czsc = import_module("czsc")
    if hasattr(czsc, "get_zs_seq"):
        return czsc
    raise EngineImportError(
        "Unable to resolve czsc.get_zs_seq: czsc.utils.sig was removed in 1.0+ "
        "and the top-level get_zs_seq is unavailable."
    )


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
