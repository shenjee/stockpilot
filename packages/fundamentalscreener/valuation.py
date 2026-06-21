"""估值评分与标签（Phase 4）。

输入：``ValuationData``（来自 ``FixtureRepository``，未来由 SQLite/数据源接口
提供）。

输出：``ValuationResult``，包含 ``ValuationEntry`` 列表与跨公司 warnings。

设计原则（沿用 Phase 3 风格）：

- 只做相对估值，不做 DCF、不写文字研报。
- ``label`` 按 docs §16 规则计算，优先级：
  ``not_applicable -> expensive_but_supported -> expensive ->
   low_need_quality_check -> fair``。
- ``score`` 使用阈值打分（独立于 cohort 大小），权重在
  ``config.VALUATION_SCORE_WEIGHTS``：
    history_pe 17.5 / history_pb 17.5 / industry 35 / growth_match 20 /
    dividend 10。
- ``pe``、``pb``、``pe_percentile``、``pb_percentile`` 任一关键字段缺失即
  退化为 ``not_applicable``，避免把数据不完整的公司当作"合理估值"误判。
- 缺失字段写入 entry ``warnings``，整命令不崩溃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    INDUSTRY_POSITION_SCORE,
    SUPPORTED_VALUATION_SORTS,
    VALUATION_LABEL_PRIORITY,
    VALUATION_LABEL_THRESHOLDS,
    VALUATION_SCORE_WEIGHTS,
)
from .repositories import CompanyData, MarketSnapshot, ValuationData
from .schema import ValuationEntry


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ValuationResult:
    companies: List[ValuationEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 分量分映射
# ---------------------------------------------------------------------------


def _ramp(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """value 在 [lo, hi] 内做线性映射到 0-100；``hi<lo`` 时单调递减。"""

    if value is None:
        return None
    if hi == lo:
        return 50.0
    raw = (value - lo) / (hi - lo) * 100.0
    if raw < 0.0:
        return 0.0
    if raw > 100.0:
        return 100.0
    return raw


def _history_percentile_score(percentile: Optional[float]) -> Optional[float]:
    """历史估值分位 → 分数。分位越低分数越高，越高分数越低。

    pct=0   → 100；pct=0.5 → 50；pct=1.0 → 0。
    """

    if percentile is None:
        return None
    return _ramp(percentile, 1.0, 0.0)


def _industry_score(position: Optional[str]) -> Optional[float]:
    if position is None:
        return None
    return INDUSTRY_POSITION_SCORE.get(position, INDUSTRY_POSITION_SCORE["unknown"])


def _growth_match_score(peg: Optional[float]) -> Optional[float]:
    """PEG → 分数。

    PEG <= 0.5 → 100（明显低估）；PEG=1.0 → 70；PEG=1.5 → 40；PEG >= 2.5 → 0。
    """

    if peg is None:
        return None
    return _ramp(peg, 2.5, 0.5)


def _dividend_score(yield_: Optional[float]) -> Optional[float]:
    """股息率 → 分数。0% → 0；5% → 100。"""

    if yield_ is None:
        return None
    return _ramp(yield_, 0.0, 0.05)


def _aggregate_score(components: Dict[str, Optional[float]]) -> Optional[float]:
    """按 ``VALUATION_SCORE_WEIGHTS`` 加权；缺失分量按可用权重重新归一。"""

    weights = dict(VALUATION_SCORE_WEIGHTS)
    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return None
    weight_sum = sum(weights[k] for k in valid)
    if weight_sum == 0:
        return None
    return sum(weights[k] * v for k, v in valid.items()) / weight_sum


# ---------------------------------------------------------------------------
# Label 规则
# ---------------------------------------------------------------------------


def _candidate_labels(v: ValuationData) -> List[str]:
    """根据 docs §16 规则列出当前数据命中的所有候选 label。

    规则采用"显式拆分"而非"互斥追加"，每条规则独立、可读：

    1. ``pe`` / ``pb`` / ``pe_percentile`` / ``pb_percentile`` 任一关键
       字段缺失或不适用 → ``not_applicable``。
    2. 高估值分位（``pe_percentile > 0.80`` 或 ``pb_percentile > 0.80``）
       且 ``peg`` 提供且 ``peg <= 1.5`` → ``expensive_but_supported``。
    3. 高估值分位且 ``peg`` 缺失或 ``peg > 1.5`` → ``expensive``。
    4. 低分位（``pe_percentile < 0.35`` 或 ``pb_percentile < 0.35``）
       → ``low_need_quality_check``。
    5. 合理区间（``pe_percentile`` 和 ``pb_percentile`` 都在
       ``[0.35, 0.70]``）→ ``fair``。

    其中 (2) 与 (3) 互斥；(4) 与 (5) 互斥。最终 label 由
    ``_resolve_label`` 按 ``VALUATION_LABEL_PRIORITY`` 选优先级最高者。
    """

    # 规则 1：任一关键字段缺失 → not_applicable（提前返回，避免后续误判）。
    if any(getattr(v, name) is None for name in _KEY_FIELDS):
        return ["not_applicable"]

    th = VALUATION_LABEL_THRESHOLDS
    pe = v.pe_percentile
    pb = v.pb_percentile
    assert pe is not None and pb is not None  # 已被规则 1 保证

    candidates: List[str] = []

    # 规则 2/3：高估值分位 → expensive_but_supported 或 expensive。
    high_pe = pe > th["expensive_pct_threshold"]
    high_pb = pb > th["expensive_pct_threshold"]
    if high_pe or high_pb:
        if v.peg is not None and v.peg <= th["supported_peg_threshold"]:
            candidates.append("expensive_but_supported")
        else:
            candidates.append("expensive")

    # 规则 4：低分位 → low_need_quality_check。
    low_pe = pe < th["low_pct_threshold"]
    low_pb = pb < th["low_pct_threshold"]
    if low_pe or low_pb:
        candidates.append("low_need_quality_check")

    # 规则 5：合理区间 → fair。
    in_fair_range = (
        th["fair_lower"] <= pe <= th["fair_upper"]
        and th["fair_lower"] <= pb <= th["fair_upper"]
    )
    if in_fair_range:
        candidates.append("fair")

    if not candidates:
        # 例如 pe_pct=0.75（不算 expensive，也不算 fair，也不算 low）。
        # 回退到 fair，保留"可比较"的语义。
        candidates.append("fair")
    return candidates


def _resolve_label(candidates: Sequence[str]) -> str:
    """按 ``VALUATION_LABEL_PRIORITY`` 选优先级最高的 label。"""

    for label in VALUATION_LABEL_PRIORITY:
        if label in candidates:
            return label
    return "not_applicable"


# ---------------------------------------------------------------------------
# 缺失字段警告
# ---------------------------------------------------------------------------


# 不强制要求字段（缺了不报 warning）：peg / dividend_yield / ps /
# industry_valuation_position。这与 docs §16 的"不适用或缺失指标 →
# not_applicable + warnings"保持一致：pe/pb 是关键字段，缺它们才算
# "关键缺失"。
_KEY_FIELDS: Tuple[str, ...] = (
    "pe",
    "pb",
    "pe_percentile",
    "pb_percentile",
)


def _missing_field_warnings(v: ValuationData) -> List[str]:
    return [name for name in _KEY_FIELDS if getattr(v, name) is None]


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_valuation(
    snapshot: MarketSnapshot, codes: Sequence[str]
) -> ValuationResult:
    """按给定 codes 顺序计算估值。

    缺失的 code 会在顶层 ``warnings`` 中以 ``code_not_found: <code>`` 形式
    报出，但不会阻断已有 code 的计算。
    """

    if not codes:
        return ValuationResult(companies=[], warnings=["no_codes_provided"])

    val_index: Dict[str, ValuationData] = {v.code: v for v in snapshot.valuations}
    company_index: Dict[str, CompanyData] = {c.code: c for c in snapshot.companies}

    warnings: List[str] = []
    entries: List[ValuationEntry] = []
    for code in codes:
        v = val_index.get(code)
        if v is None:
            warnings.append(f"code_not_found: {code}")
            continue

        components = {
            "history_pe": _history_percentile_score(v.pe_percentile),
            "history_pb": _history_percentile_score(v.pb_percentile),
            "industry": _industry_score(v.industry_valuation_position),
            "growth_match": _growth_match_score(v.peg),
            "dividend": _dividend_score(v.dividend_yield),
        }
        score = _aggregate_score(components)

        label = _resolve_label(_candidate_labels(v))
        missing = _missing_field_warnings(v)
        entry_warnings: List[str] = [f"missing_field: {name}" for name in missing]

        # not_applicable 时即便有部分字段，也提示"key fields missing"，
        # 与 docs §16 DoD"不适用或缺失指标输出 not_applicable 和 warnings"对齐。
        if label == "not_applicable" and not entry_warnings:
            entry_warnings.append("not_applicable: missing key valuation fields")

        display_name = v.name or (
            company_index[code].name if code in company_index else ""
        )

        entries.append(
            ValuationEntry(
                code=code,
                name=display_name,
                pe=v.pe,
                pb=v.pb,
                ps=v.ps,
                peg=v.peg,
                dividend_yield=v.dividend_yield,
                pe_percentile=v.pe_percentile,
                pb_percentile=v.pb_percentile,
                industry_valuation_position=v.industry_valuation_position,
                score=_round_or_none(score, 2),
                label=label,
                warnings=entry_warnings,
            )
        )

    return ValuationResult(companies=entries, warnings=warnings)


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------


_SORT_FIELD_TO_ATTR: Dict[str, str] = {
    "score": "score",
    "pe": "pe",
    "pb": "pb",
    "ps": "ps",
    "peg": "peg",
    "dividend_yield": "dividend_yield",
    "pe_percentile": "pe_percentile",
    "pb_percentile": "pb_percentile",
}

# 估值越低越好的字段按升序；score、dividend_yield 按降序。
_VALUATION_ASCENDING: Tuple[str, ...] = (
    "pe",
    "pb",
    "ps",
    "peg",
    "pe_percentile",
    "pb_percentile",
)


def sort_valuations(
    entries: Sequence[ValuationEntry], sort_field: str
) -> List[ValuationEntry]:
    if sort_field not in SUPPORTED_VALUATION_SORTS:
        return list(entries)
    attr = _SORT_FIELD_TO_ATTR[sort_field]
    ascending = sort_field in _VALUATION_ASCENDING

    def sort_key(e: ValuationEntry) -> Tuple[int, float]:
        value = getattr(e, attr)
        if value is None:
            return (1, 0.0)
        signed = float(value) if ascending else -float(value)
        return (0, signed)

    return sorted(entries, key=sort_key)


__all__ = [
    "ValuationResult",
    "compute_valuation",
    "sort_valuations",
]
