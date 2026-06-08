from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Dict, List, Optional


@dataclass
class AnalysisWarning:
    id: str
    warning_code: str
    severity: str
    message: str
    field: str = ""
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class NormalizedBar:
    symbol: str
    timeframe: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str
    bar_index: int
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class Fractal:
    id: str
    fractal_type: str
    bar_index: int
    timestamp: str
    price: float
    confirmed: bool
    source: str = "czsc"
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class Stroke:
    id: str
    direction: str
    start_fractal_id: str
    end_fractal_id: str
    start_timestamp: str
    end_timestamp: str
    start_price: float
    end_price: float
    confirmed: bool
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class Segment:
    id: str
    direction: str
    stroke_ids: List[str]
    start_timestamp: str
    end_timestamp: str
    start_price: float
    end_price: float
    confirmed: bool
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class PivotZone:
    id: str
    start_timestamp: str
    end_timestamp: str
    high: float
    low: float
    segment_ids: List[str]
    level: str
    active: bool
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class Divergence:
    id: str
    divergence_type: str
    reference_type: str
    reference_id: str
    timestamp: str
    strength: str
    confirmed: bool
    description: str
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class StructureAlert:
    id: str
    alert_type: str
    severity: str
    timestamp: str
    related_ids: List[str]
    message: str
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class CandidatePoint:
    id: str
    point_type: str
    timestamp: str
    price: float
    reference_id: str
    confirmed: bool
    reason: str
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class PlotPrimitive:
    id: str
    type: str
    layer: str
    x: str = ""
    y: Optional[float] = None
    x1: str = ""
    y1: Optional[float] = None
    x2: str = ""
    y2: Optional[float] = None
    style: str = "solid"
    color: str = "#2563EB"
    text: str = ""
    meta: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class NormalizationResult:
    symbol: str
    timeframe: str
    source: str
    bars: List[NormalizedBar]
    warnings: List[AnalysisWarning] = dc_field(default_factory=list)
    input_fields: List[str] = dc_field(default_factory=list)
    gaps: List[str] = dc_field(default_factory=list)


@dataclass
class AnalysisResult:
    symbol: str
    timeframe: str
    source: str
    engine: str
    engine_version: str
    parameters: Dict[str, Any]
    fractals: List[Fractal] = dc_field(default_factory=list)
    strokes: List[Stroke] = dc_field(default_factory=list)
    segments: List[Segment] = dc_field(default_factory=list)
    pivot_zones: List[PivotZone] = dc_field(default_factory=list)
    divergences: List[Divergence] = dc_field(default_factory=list)
    structure_alerts: List[StructureAlert] = dc_field(default_factory=list)
    candidate_buy_points: List[CandidatePoint] = dc_field(default_factory=list)
    candidate_sell_points: List[CandidatePoint] = dc_field(default_factory=list)
    plot_primitives: List[PlotPrimitive] = dc_field(default_factory=list)
    summary: List[str] = dc_field(default_factory=list)
    warnings: List[AnalysisWarning] = dc_field(default_factory=list)
    meta: Dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
