from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any, Dict, Iterable, Mapping, Tuple

from .config import ENGINE_NAME, PINNED_ENGINE_VERSION, get_default_parameters
from .describe import build_summary
from .normalize import normalize_ohlcv_rows, normalize_tracker_klines
from .plotting import build_plot_primitives
from .schema import AnalysisResult, AnalysisWarning, NormalizationResult


class EngineImportError(RuntimeError):
    pass


def analyze(
    rows: Iterable[Mapping[str, object]],
    symbol: str,
    timeframe: str = "day",
    source: str = "unknown",
    parameters: Dict[str, Any] | None = None,
    strict: bool = True,
) -> AnalysisResult:
    normalized = normalize_ohlcv_rows(
        rows=rows,
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        strict=strict,
    )
    return analyze_normalized(normalized=normalized, parameters=parameters)


def analyze_tracker_klines(
    rows: Iterable[Mapping[str, object]],
    code: str,
    market: str,
    timeframe: str = "day",
    source: str = "tencent",
    parameters: Dict[str, Any] | None = None,
    strict: bool = True,
) -> AnalysisResult:
    normalized = normalize_tracker_klines(
        rows=rows,
        code=code,
        market=market,
        timeframe=timeframe,
        source=source,
        strict=strict,
    )
    return analyze_normalized(normalized=normalized, parameters=parameters)


def analyze_normalized(
    normalized: NormalizationResult,
    parameters: Dict[str, Any] | None = None,
) -> AnalysisResult:
    merged_parameters = get_default_parameters()
    if parameters:
        merged_parameters.update(parameters)

    result = AnalysisResult(
        symbol=normalized.symbol,
        timeframe=normalized.timeframe,
        source=normalized.source,
        engine=ENGINE_NAME,
        engine_version=PINNED_ENGINE_VERSION,
        parameters=merged_parameters,
        warnings=list(normalized.warnings),
        meta={
            "bar_count": len(normalized.bars),
            "input_fields": list(normalized.input_fields),
            "gaps": list(normalized.gaps),
            "engine_probe": {},
        },
    )

    if not normalized.bars:
        result.warnings.append(
            _warning(
                warning_id="warning_no_bars",
                code="NO_INPUT_BARS",
                message="No bars were available after normalization.",
                field="bars",
            )
        )
        result.summary = build_summary(result)
        return result

    try:
        analyzer, raw_bars = _run_engine(normalized=normalized, parameters=merged_parameters)
        result.meta["engine_probe"] = {
            "status": "ok",
            "raw_bar_count": len(raw_bars),
            "fractal_count": len(getattr(analyzer, "fx_list", [])),
            "finished_bi_count": len(getattr(analyzer, "finished_bis", [])),
            "last_bi_extend": bool(getattr(analyzer, "last_bi_extend", False)),
        }
    except Exception as exc:
        result.meta["engine_probe"] = {"status": "failed", "error": str(exc)}
        result.warnings.append(
            _warning(
                warning_id="warning_engine_probe_failed",
                code="ENGINE_PROBE_FAILED",
                message=f"czsc probe failed during Phase 1: {exc}",
                field="engine",
            )
        )

    result.plot_primitives = build_plot_primitives(result)
    result.summary = build_summary(result)
    return result


def _run_engine(
    normalized: NormalizationResult,
    parameters: Dict[str, Any],
) -> Tuple[object, list]:
    RawBar, Freq, CZSC = load_czsc()
    freq = getattr(Freq, _get_freq_name(normalized.timeframe))
    raw_bars = []

    for bar in normalized.bars:
        raw_bars.append(
            RawBar(
                symbol=bar.symbol,
                id=bar.bar_index,
                dt=_parse_dt(bar.timestamp),
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


def load_czsc() -> Tuple[object, object, object]:
    # rs_czsc expects numpy.typing to be loaded before czsc import on Python 3.9.
    import_module("numpy.typing")
    try:
        czsc = import_module("czsc")
    except ImportError as exc:
        raise EngineImportError(str(exc)) from exc

    RawBar = getattr(import_module("czsc.objects"), "RawBar")
    Freq = getattr(czsc, "Freq")
    CZSC = getattr(czsc, "CZSC")
    return RawBar, Freq, CZSC


def _parse_dt(value: str) -> datetime:
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d")
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _get_freq_name(timeframe: str) -> str:
    mapping = {
        "1m": "F1",
        "5m": "F5",
        "15m": "F15",
        "30m": "F30",
        "60m": "F60",
        "day": "D",
        "week": "W",
        "month": "M",
    }
    return mapping[timeframe]


def _warning(warning_id: str, code: str, message: str, field: str) -> AnalysisWarning:
    return AnalysisWarning(
        id=warning_id,
        warning_code=code,
        severity="warning",
        message=message,
        field=field,
    )
