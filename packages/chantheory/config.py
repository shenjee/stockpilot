from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


ENGINE_NAME = "czsc"
PINNED_ENGINE_VERSION = "0.9.63"
PINNED_ENGINE_REASON = (
    "Pinned to a Python 3.9-compatible release. Newer 0.10.x releases require "
    "Python 3.10+ and do not match the current project runtime."
)

DEFAULT_PARAMETERS = {
    "max_bi_num": 50,
    "min_bars": 60,
    "strict_validation": True,
    "derive_amount_from_close_volume": True,
}

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
    "Current repo flow only fetches day bars; minute and higher-period aggregation are future work.",
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
        supported_python="3.8-3.12 classifiers on 0.9.63",
        validated_python="3.9.6",
        import_shim="Import numpy.typing before czsc to avoid rs_czsc import failure on Python 3.9.",
    )


def get_default_parameters() -> Dict[str, object]:
    return dict(DEFAULT_PARAMETERS)
