"""板块内公司排名（Phase 2 + combined_score 升级）。

输入：``MarketSnapshot`` + 目标板块 ``sector_id``。

输出：``CompanyRankingResult``，包含 ``CompanyEntry`` 列表与跨公司 warnings。

Phase 2 行情口径：

- ``market_cap``、``turnover_amount``、``turnover_rate`` 直接来自数据。
- ``sector_return_rank`` 按板块内 ``return_1d`` 降序排名（1 = 最强）。
- ``leader_score`` 由 ``market_cap`` 在板块内做 min-max 归一化（板块内龙头优先）。
- ``attention_score`` 由 ``turnover_amount`` 与 ``turnover_rate`` 各自归一化后等权平均
  （资金关注 = 绝对成交额 + 相对换手率）。

Phase 3/4 接入后的升级：

- ``financial_quality_score`` 来自 ``financial_quality.compute_financial_quality``。
- ``valuation_score`` 来自 ``valuation.compute_valuation``。
- ``combined_score`` 改用 docs §14 的升级版权重：
  ``leader 0.20 / attention 0.20 / financial 0.35 / valuation 0.25``，
  缺失分量按可用权重重新归一（避免 fin/val 缺数据时整体分数归零）。
- ``flags`` Phase 2 留空，硬伤判断仍由 Phase 5 编排负责。
- ``group`` 阈值沿用 Phase 2（priority/watch/cautious），本轮不动。

数据缺失：单家公司缺关键列时对应字段为 None，并把可读 warning 写入 entry；
没有任何公司或板块本身不存在的情况由 CLI 层负责报错（``sector_not_found``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    COMBINED_SCORE_WEIGHTS,
    COMPANY_GROUP_PRIORITY_THRESHOLD,
    COMPANY_GROUP_WATCH_THRESHOLD,
    COMPANY_SORT_ASCENDING,
    SUPPORTED_COMPANY_SORTS,
)
from .financial_quality import compute_financial_quality
from .repositories import CompanyData, MarketSnapshot, SectorData
from .schema import CompanyEntry
from .valuation import compute_valuation


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
    financial_quality_score: Optional[float],
    valuation_score: Optional[float],
) -> Optional[float]:
    """按 ``COMBINED_SCORE_WEIGHTS`` 加权；缺失分量按可用权重重新归一。

    这是 docs §14 的升级版公式（leader 0.20 / attention 0.20 / financial 0.35 /
    valuation 0.25）。若 fin/val 两个分量都为 None，则自动退化为 Phase 2 的
    leader+attention 组合（不会把整体打成 0）。
    """

    components: Dict[str, Optional[float]] = {
        "leader_score": leader_score,
        "attention_score": attention_score,
        "financial_quality_score": financial_quality_score,
        "valuation_score": valuation_score,
    }
    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return None
    weights = dict(COMBINED_SCORE_WEIGHTS)
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

    # ---- 补齐财务质量分 / 估值分（Phase 3/4 接入） ----
    # 在板块内集中调一次，避免每家公司各做一次 cohort 归一化（财务分依赖
    # cohort）。fin/val 缺失某家公司时静默返回 None，不污染 ranking 顶层
    # warnings —— 单家公司的 missing_field 信息已经在 financials/valuations
    # 命令里独立展示，不应在 companies 视图里重复堆叠；但是 companies 视图
    # 自己会用 entry warnings 标记"该公司的 fin/val 分量缺失"，方便调用方
    # 判断 combined_score 是否被降级。
    codes = [str(r["code"]) for r in raw_records]
    fin_result = compute_financial_quality(snapshot, codes)
    val_result = compute_valuation(snapshot, codes)
    fin_score_index: Dict[str, Optional[float]] = {
        e.code: e.score for e in fin_result.companies
    }
    val_score_index: Dict[str, Optional[float]] = {
        e.code: e.score for e in val_result.companies
    }
    val_label_index: Dict[str, Optional[str]] = {
        e.code: e.label for e in val_result.companies
    }

    # ---- 构造 CompanyEntry ----
    entries: List[CompanyEntry] = []
    for idx, r in enumerate(raw_records):
        code = str(r["code"])
        leader_score = leader_norm[idx]
        attention_score = _attention_score(turnover_norm[idx], turnover_rate_norm[idx])
        financial_quality_score = fin_score_index.get(code)

        # 估值分量：label=not_applicable 表示关键字段缺失，分数虽然有数值
        # 但不可信，必须从 combined_score 排除，否则会让数据不完整的公司
        # 凭一个"看起来合理"的兜底分进入排名。
        val_score_raw = val_score_index.get(code)
        val_label = val_label_index.get(code)
        if val_label == "not_applicable":
            valuation_score: Optional[float] = None
        else:
            valuation_score = val_score_raw

        combined = _aggregate_combined(
            leader_score,
            attention_score,
            financial_quality_score,
            valuation_score,
        )
        entry_warnings = list(r["warnings"] or [])  # type: ignore[arg-type]

        # 摘要型 warnings：让 CLI/skill/UI 一眼看出 combined_score 是否被降级。
        # 不堆叠 missing_field 细节（仍由 financials/valuations 子命令展示）。
        # 财务分量为 None 可能因为 code_not_found 或 score 算不出，统一一个标记即可。
        if financial_quality_score is None:
            entry_warnings.append("missing_financial_quality_score")
        # 估值分量分别区分"完全缺失"与"label=not_applicable（关键字段不完整）"：
        # 后者更具体，便于调用方追查数据缺口。
        if val_label == "not_applicable":
            entry_warnings.append("valuation_not_applicable")
        elif valuation_score is None:
            entry_warnings.append("missing_valuation_score")

        entry = CompanyEntry(
            code=code,
            name=str(r["name"]),
            market_cap=r["market_cap"],
            turnover_amount=r["turnover_amount"],
            turnover_rate=r["turnover_rate"],
            sector_return_rank=sector_return_ranks[idx],
            leader_score=_round_or_none(leader_score, 2),
            attention_score=_round_or_none(attention_score, 2),
            financial_quality_score=_round_or_none(financial_quality_score, 2),
            valuation_score=_round_or_none(valuation_score, 2),
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
