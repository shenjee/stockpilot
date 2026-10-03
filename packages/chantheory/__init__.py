
# Direct sys.path users may expose only packages/, without the repository root.
try:
    from packages._import_aliases import initialize_package as _initialize_package
except ModuleNotFoundError as _exc:
    if _exc.name != "packages":
        raise
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.append(str(_Path(__file__).resolve().parents[2]))
    from packages._import_aliases import initialize_package as _initialize_package

if _initialize_package(__name__):
    from .adapters import analyze, analyze_multi_timeframe, analyze_multi_timeframe_tracker_klines, analyze_normalized, analyze_tracker_klines
    from .config import (
        ENGINE_NAME,
        PINNED_ENGINE_VERSION,
        get_default_max_bi_num,
        get_default_parameters,
        get_default_signals_config,
        get_engine_compatibility,
    )
    from .normalize import NormalizationError, build_symbol, normalize_ohlcv_rows, normalize_tracker_klines
    from .schema import AnalysisResult, MultiTimeframeAnalysisResult, NormalizationResult, NormalizedBar

    __all__ = [
        "ENGINE_NAME",
        "PINNED_ENGINE_VERSION",
        "AnalysisResult",
        "MultiTimeframeAnalysisResult",
        "NormalizationError",
        "NormalizationResult",
        "NormalizedBar",
        "analyze",
        "analyze_multi_timeframe",
        "analyze_multi_timeframe_tracker_klines",
        "analyze_normalized",
        "analyze_tracker_klines",
        "build_symbol",
        "get_default_max_bi_num",
        "get_default_parameters",
        "get_default_signals_config",
        "get_engine_compatibility",
        "normalize_ohlcv_rows",
        "normalize_tracker_klines",
    ]
