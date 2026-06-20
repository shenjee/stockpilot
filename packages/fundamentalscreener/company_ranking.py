"""板块内公司排名（Phase 2）。

输入：``MarketSnapshot`` + 目标板块 ``sector_id``。

输出：``CompanyRankingResult``，包含 ``CompanyEntry`` 列表与跨公司 warnings。

Phase 2 第一版只依赖板块内行情可观测的量：

- ``market_cap``、``turnover_amount``、``turnover_rate`` 直接来自数据。
- ``sector_return_rank`` 按板块内 ``return_1d`` 降序排名（1 = 最强）。
- ``leader_score`` 由 ``market_cap`` 在板块内做 min-max 归一化（板块内龙头优先）。
- ``attention_score`` 由 ``turnover_amount`` 与 ``turnover_rate`` 各自归一化后等权平均
  （资金关注 = 绝对成交额 + 相对换手率）。
- ``financial_quality_score`` / ``valuation_score`` 在 Phase 3/4 接入前固定为
  ``None``，对应 schema 注释。
- ``combined_score`` 使用 Phase 2 第一版权重：
  ``leader_score * 0.4 + attention_score * 0.6``。
- ``group`` 由 ``combined_score`` 阈值决定（priority/watch/cautious），尚不引入
  财务、估值硬伤；Phase 5 编排会重新计算 group。
- ``flags`` Phase 2 留空，避免在没有财务/估值时编造硬伤。

数据缺失：单家公司缺关键列时对应字段为 None，并把可读 warning 写入 entry；
没有任何公司或板块本身不存在的情况由 CLI 层负责报错（``sector_not_found``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    COMBINED_SCORE_WEIGHTS_PHASE2,
    COMPANY_GROUP_PRIORITY_THRESHOLD,
    COMPANY_GROUP_WATCH_THRESHOLD,
    COMPANY_SORT_ASCENDING,
    SUPPORTED_COMPANY_SORTS,
)
from .repositories import CompanyData, MarketSnapshot, SectorData
from .schema import CompanyEntry


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class CompanyRankingResult:
    """板块内公司排名结果。"""

    sector_id: Optional[str] = None
    sector_name: Optional[str] = None
    companies: List[CompanyEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def _last_day_return(daily: Sequence) -> Optional[float]:
    """以最后两根 K 线的 close 计算 1 日涨跌幅。"""

    if len(daily) < 2:
        return None
    base = daily[-2].close
    if base == 0:
        return None
    return daily[-1].close / base - 1.0


def _min_max_normalize(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """min-max 归一化到 0-100；None 透传；全相等时给 50。"""

    valid = [v for v in values if v is not None]
    if not valid:
        return [None for _ in values]
    lo, hi = min(valid), max(valid)
    if hi == lo:
        return [50.0 if v is not None else None for v in values]
    span = hi - lo
    return [(v - lo) / span * 100.0 if v is not None else None for v in values]


def _rank_descending(values: Sequence[Optional[float]]) -> List[Optional[int]]:
    indexed: List[Tuple[int, float]] = [
        (i, v) for i, v in enumerate(values) if v is not None
    ]
    indexed.sort(key=lambda x: x[1], reverse=True)
    ranks: List[Optional[int]] = [None] * len(values)
    for rank, (idx, _) in enumerate(indexed, start=1):
        ranks[idx] = rank
    return ranks


def _attention_score(
    turnover_norm: Optional[float], turnover_rate_norm: Optional[float]
) -> Optional[float]:
    """绝对成交额与相对换手率等权平均；任一缺失时退化为另一个。"""

    components = [c for c in (turnover_norm, turnover_rate_norm) if c is not None]
    if not components:
        return None
    return sum(components) / len(components)


def _aggregate_combined(
    leader_score: Optional[float],
    attention_score: Optional[float],
) -> Optional[float]:
    """按 ``COMBINED_SCORE_WEIGHTS_PHASE2`` 加权；缺失分量按可用权重重新归一。"""

    components: Dict[str, Optional[float]] = {
        "leader_score": leader_score,
        "attention_score": attention_score,
    }
    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return None
    weights = dict(COMBINED_SCORE_WEIGHTS_PHASE2)
    weight_sum = sum(weights[k] for k in valid)
    if weight_sum == 0:
        return None
    return sum(weights[k] * v for k, v in valid.items()) / weight_sum


def _group_for_score(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= COMPANY_GROUP_PRIORITY_THRESHOLD:
        return "priority"
    if score >= COMPANY_GROUP_WATCH_THRESHOLD:
        return "watch"
    return "cautious"


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_company_ranking(
    snapshot: MarketSnapshot, sector_id: str
) -> CompanyRankingResult:
    """计算板块内公司排名。

    调用方需保证 ``sector_id`` 存在；找不到时返回空结果并写入 warning，由 CLI
    层自行决定要不要终止。
    """

    target: Optional[SectorData] = next(
        (s for s in snapshot.sectors if s.sector_id == sector_id), None
    )
    if target is None:
        return CompanyRankingResult(
            sector_id=None,
            sector_name=None,
            companies=[],
            warnings=[f"sector_not_found: {sector_id}"],
        )

    companies: List[CompanyData] = [
        c for c in snapshot.companies if c.sector_id == sector_id
    ]
    if not companies:
        return CompanyRankingResult(
            sector_id=target.sector_id,
            sector_name=target.sector_name,
            companies=[],
            warnings=["no_companies_in_sector"],
        )

    # ---- 单公司原始指标 ----
    raw_records: List[Dict[str, Optional[float]]] = []
    for c in companies:
        per_warnings: List[str] = []
        last_bar = c.daily[-1] if c.daily else None
        turnover_amount = last_bar.turnover_amount if last_bar is not None else None
        turnover_rate = last_bar.turnover_rate if last_bar is not None else None
        return_1d = _last_day_return(c.daily)
        if last_bar is None:
            per_warnings.append("daily_unavailable")
        elif return_1d is None:
            per_warnings.append("return_1d_unavailable")
        if c.market_cap is None:
            per_warnings.append("market_cap_unavailable")
        if turnover_rate is None and last_bar is not None:
            per_warnings.append("turnover_rate_unavailable")
        raw_records.append(
            {
                "code": c.code,
                "name": c.name,
                "market_cap": c.market_cap,
                "turnover_amount": turnover_amount,
                "turnover_rate": turnover_rate,
                "return_1d": return_1d,
                "warnings": per_warnings,  # type: ignore[dict-item]
            }
        )

    # ---- 跨公司归一化 / 排名 ----
    market_cap_values = [r["market_cap"] for r in raw_records]
    turnover_values = [r["turnover_amount"] for r in raw_records]
    turnover_rate_values = [r["turnover_rate"] for r in raw_records]
    return_1d_values = [r["return_1d"] for r in raw_records]

    leader_norm = _min_max_normalize(market_cap_values)
    turnover_norm = _min_max_normalize(turnover_values)
    turnover_rate_norm = _min_max_normalize(turnover_rate_values)
    sector_return_ranks = _rank_descending(return_1d_values)

    # ---- 构造 CompanyEntry ----
    entries: List[CompanyEntry] = []
    for idx, r in enumerate(raw_records):
        leader_score = leader_norm[idx]
        attention_score = _attention_score(turnover_norm[idx], turnover_rate_norm[idx])
        combined = _aggregate_combined(leader_score, attention_score)
        entry_warnings = list(r["warnings"] or [])  # type: ignore[arg-type]
        entry = CompanyEntry(
            code=str(r["code"]),
            name=str(r["name"]),
            market_cap=r["market_cap"],
            turnover_amount=r["turnover_amount"],
            turnover_rate=r["turnover_rate"],
            sector_return_rank=sector_return_ranks[idx],
            leader_score=_round_or_none(leader_score, 2),
            attention_score=_round_or_none(attention_score, 2),
            financial_quality_score=None,
            valuation_score=None,
            combined_score=_round_or_none(combined, 2),
            group=_group_for_score(combined),
            flags=[],
            warnings=entry_warnings,
        )
        entries.append(entry)

    return CompanyRankingResult(
        sector_id=target.sector_id,
        sector_name=target.sector_name,
        companies=entries,
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------


_SORT_FIELD_TO_ATTR: Dict[str, str] = {
    "combined_score": "combined_score",
    "leader_score": "leader_score",
    "attention_score": "attention_score",
    "market_cap": "market_cap",
    "turnover_amount": "turnover_amount",
    "turnover_rate": "turnover_rate",
    "sector_return_rank": "sector_return_rank",
}


def sort_companies(
    entries: Sequence[CompanyEntry], sort_field: str
) -> List[CompanyEntry]:
    """按 ``sort_field`` 排序；None 永远排到末尾。

    默认从大到小排序；``sector_return_rank`` 例外：rank=1 表示最强，因此按升序。
    未识别字段保持原顺序，由 CLI 层负责参数校验。
    """

    if sort_field not in SUPPORTED_COMPANY_SORTS:
        return list(entries)
    attr = _SORT_FIELD_TO_ATTR[sort_field]
    ascending = sort_field in COMPANY_SORT_ASCENDING

    def sort_key(e: CompanyEntry) -> Tuple[int, float]:
        value = getattr(e, attr)
        if value is None:
            return (1, 0.0)
        signed = float(value) if ascending else -float(value)
        return (0, signed)

    return sorted(entries, key=sort_key)


__all__ = [
    "CompanyRankingResult",
    "compute_company_ranking",
    "sort_companies",
]
