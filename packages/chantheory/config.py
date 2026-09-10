from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


ENGINE_NAME = "czsc"
PINNED_ENGINE_VERSION = "1.0.1"
PINNED_ENGINE_REASON = (
    "Pinned to czsc 1.0.1; the Rust-native engine replaces the old pure-Python "
    "czsc.py path. Timestamps, finished_bis tail-exclusion, and zs_list have "
    "been verified against the 0.10.12 baseline."
)

DEFAULT_PARAMETERS = {
    "max_bi_num": 50,
    "min_bi_len": 6,
    "min_bars": 60,
    "strict_validation": True,
    "derive_amount_from_close_volume": True,
}

# czsc 1.0.1 exposes ``min_bi_len`` as a CZSC constructor parameter (default
# 6) and as an instance attribute. czsc 0.10.12 hardcodes the same default
# internally and does NOT accept the kwarg. The baseline fixtures were frozen
# under 0.10.12 with the implicit default of 6, so the explicit value keeps
# 1.0.1 aligned with the baseline and prevents env-var (CZSC_MIN_BI_LEN)
# drift. ``run_engine`` only forwards the kwarg on 1.0+.
MIN_BI_LEN_DEFAULT = 6

MINUTE_TIMEFRAMES_FOR_MAX_BI = {"1m", "5m", "15m", "30m", "60m"}
DEFAULT_MAX_BI_NUM_MINUTE = 500

DEFAULT_SIGNALS_CONFIG = (
    {
        "module": "czsc.signals.cxt",
        "name": "cxt_first_buy_V221126",
        "key": "first_buy",
    },
    {
        "module": "czsc.signals.cxt",
        "name": "cxt_first_sell_V221126",
        "key": "first_sell",
    },
    {
        "module": "czsc.signals.cxt",
        "name": "cxt_second_bs_V240524",
        "key": "second_bs",
    },
    {
        "module": "czsc.signals.cxt",
        "name": "cxt_third_bs_V230319",
        "key": "third_bs",
    },
)

TIMEFRAME_TO_CZSC_FREQ = {
    "1m": "F1",
    "5m": "F5",
    "15m": "F15",
    "30m": "F30",
    "60m": "F60",
    "day": "D",
    "week": "W",
    "month": "M",
}

TIMEFRAME_ORDER = {
    "1m": 0,
    "5m": 1,
    "15m": 2,
    "30m": 3,
    "60m": 4,
    "day": 5,
    "week": 6,
    "month": 7,
}


def get_freq_name(timeframe: str) -> str:
    """Return the czsc ``Freq`` attribute name for a given *timeframe* string."""
    return TIMEFRAME_TO_CZSC_FREQ[timeframe]


MARKET_SUFFIX = {
    "sh": "SH",
    "sz": "SZ",
    "bj": "BJ",
}

TRACKER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
)

TRACKER_GAPS: Tuple[str, ...] = (
    "Current tracker K-lines do not persist amount/turnover directly.",
    "Current Tencent minute K-lines are fetched on demand and are not persisted in the local daily K-line cache.",
    "Adjustment mode is implicit in provider usage and not yet carried in the normalized schema.",
)

CONTAINMENT_STRATEGY = (
    "Normalization keeps raw source bars intact. Inclusion handling stays inside czsc "
    "analysis in Phase 2, rather than mutating project-level normalized input."
)

DEGRADATION_RULES: Tuple[str, ...] = (
    "Normalization failures raise in strict mode and become warnings in non-strict mode.",
    "Engine import or runtime failures return the frozen schema with empty structure lists.",
    "Warnings must explain whether failure came from input validation, engine import, or analysis execution.",
)


@dataclass(frozen=True)
class EngineCompatibility:
    engine: str
    version: str
    supported_python: str
    validated_python: str
    import_shim: str


def get_engine_compatibility() -> EngineCompatibility:
    return EngineCompatibility(
        engine=ENGINE_NAME,
        version=PINNED_ENGINE_VERSION,
        supported_python="Requires Python >=3.10; czsc 1.0.1 uses built-in Rust extension (czsc._native)",
        validated_python="3.14.5",
        import_shim="Import numpy.typing before czsc so the Rust extension initializes consistently.",
    )


def get_default_parameters() -> Dict[str, object]:
    return dict(DEFAULT_PARAMETERS)


def get_default_max_bi_num(timeframe: str | None = None) -> int:
    """Return the default max_bi_num for a given timeframe.

    Minute timeframes produce far more strokes than daily bars over the same
    calendar range, so they need a larger limit to avoid discarding early
    structure (strokes, segments, pivot zones) that falls outside the engine's
    retention window.
    """
    if timeframe in MINUTE_TIMEFRAMES_FOR_MAX_BI:
        return DEFAULT_MAX_BI_NUM_MINUTE
    return DEFAULT_PARAMETERS["max_bi_num"]


def get_default_signals_config() -> List[Dict[str, object]]:
    return [dict(item) for item in DEFAULT_SIGNALS_CONFIG]
