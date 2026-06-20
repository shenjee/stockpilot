"""板块轮动指标计算（Phase 1）。

输入：``MarketSnapshot``（来自 ``repositories.FixtureRepository``，未来可由
SQLiteRepository 复用）。

输出：板块条目列表（``SectorEntry``）和 ``chart_series``。计算保持简单：

- 收益按 close 序列计算 ``period_return = close[-1] / close[-1-N] - 1``。
- ``relative_return`` 固定使用 20 日窗口，与 MVP §5.2 表格示例一致。
- ``turnover_amount_change`` 使用最后一日成交额相对前 20 日均值的变化。
- ``market_turnover_share`` 以"所有板块当日成交额之和"作为市场总成交额代理。
- ``rising_stock_ratio`` 按板块内公司当日 close 与上一日 close 比较。
- ``rank_change_5d`` = 5 日前 return_1d 排名 - 当前 return_1d 排名（正值表
  示排名提升）。
- ``score`` 按 MVP §5.4 公式，分量先在样本内 min-max 归一化到 0-100。
- ``chart_series`` 给每个板块和基准生成"起点 = 100"的归一化走势曲线。

数据缺失时单个板块的对应字段返回 ``None`` 并把可读 warning 写入 entry 的
``warnings``，函数级 warnings 用于跨板块缺失（如基准缺失、板块数过少导致前
20% 无意义）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .repositories import (
    BenchmarkData,
    CompanyData,
    DailyBar,
    MarketSnapshot,
    SectorData,
)
from .schema import ChartSeries, ChartSeriesPoint, SectorEntry

# 用于计算 relative_return 的固定窗口。MVP §5.2 给的示例就是 20 日。
RELATIVE_RETURN_PERIOD = 20

# turnover_amount_change 的"过去均值"窗口。
TURNOVER_BASELINE_WINDOW = 20

# rank_change_5d 的偏移天数。
RANK_CHANGE_OFFSET = 5

# overheated 状态需要"前 20%"。
OVERHEATED_PERCENTILE = 0.2

# 板块强度分权重，与 MVP §5.4 一致。
SCORE_WEIGHTS: Dict[str, float] = {
    "short_term": 0.25,
    "mid_term": 0.20,
    "relative": 0.25,
    "turnover": 0.15,
    "breadth": 0.15,
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SectorRotationResult:
    """板块轮动计算结果。"""

    sectors: List[SectorEntry] = field(default_factory=list)
    chart_series: List[ChartSeries] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 基础数学帮助函数
# ---------------------------------------------------------------------------


def _period_return(daily: Sequence[DailyBar], period: int, offset: int = 0) -> Optional[float]:
    """计算给定周期的累计收益。

    ``offset=0`` 表示以最后一根为终点；``offset>0`` 把终点向前推 ``offset`` 根，
    用于"过去某日的同周期收益"。
    """

    end = len(daily) - 1 - offset
    start = end - period
    if start < 0 or end < 0:
        return None
    base = daily[start].close
    if base == 0:
        return None
    return daily[end].close / base - 1.0


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


def _rank_descending(values: Sequence[Optional[float]]) -> List[Optional[int]]:
    """按值从大到小给出 1-based 排名；None 不参与排名，对应位置返回 None。"""

    indexed: List[Tuple[int, float]] = [
        (i, v) for i, v in enumerate(values) if v is not None
    ]
    indexed.sort(key=lambda x: x[1], reverse=True)
    ranks: List[Optional[int]] = [None] * len(values)
    for rank, (idx, _) in enumerate(indexed, start=1):
        ranks[idx] = rank
    return ranks


def _min_max_normalize(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """将一组值按 min-max 归一化到 0-100；None 透传。"""

    valid = [v for v in values if v is not None]
    if not valid:
        return [None for _ in values]
    lo, hi = min(valid), max(valid)
    if hi == lo:
        return [50.0 if v is not None else None for v in values]
    span = hi - lo
    return [(v - lo) / span * 100.0 if v is not None else None for v in values]


# ---------------------------------------------------------------------------
# 单板块原始指标
# ---------------------------------------------------------------------------


def _compute_returns(
    daily: Sequence[DailyBar], periods: Sequence[int]
) -> Tuple[Dict[int, Optional[float]], List[str]]:
    """返回 ``{period: return}`` 和缺失警告列表。"""

    out: Dict[int, Optional[float]] = {}
    warnings: List[str] = []
    for p in periods:
        r = _period_return(daily, p)
        if r is None:
            warnings.append(f"insufficient_history_for_return_{p}d")
        out[p] = r
    return out, warnings


def _compute_turnover_amount_change(daily: Sequence[DailyBar]) -> Optional[float]:
    if len(daily) < TURNOVER_BASELINE_WINDOW + 1:
        return None
    recent = daily[-1].turnover_amount
    prior = [d.turnover_amount for d in daily[-(TURNOVER_BASELINE_WINDOW + 1) : -1]]
    baseline = _mean(prior)
    if baseline is None or baseline == 0:
        return None
    return recent / baseline - 1.0


def _compute_rising_stock_ratio(
    sector: SectorData, companies_by_code: Dict[str, CompanyData]
) -> Tuple[Optional[float], Optional[str]]:
    constituents = [companies_by_code[c] for c in sector.constituents if c in companies_by_code]
    eligible = [c for c in constituents if len(c.daily) >= 2]
    if not eligible:
        return None, "rising_stock_ratio_unavailable_no_company_history"
    rising = 0
    for c in eligible:
        last = c.daily[-1].close
        prev = c.daily[-2].close
        if last > prev:
            rising += 1
    return rising / len(eligible), None


# ---------------------------------------------------------------------------
# 状态规则
# ---------------------------------------------------------------------------


def _resolve_state(
    return_5d: Optional[float],
    return_20d: Optional[float],
    return_60d: Optional[float],
    relative_return: Optional[float],
    turnover_amount_change: Optional[float],
    overheated_threshold_5d: Optional[float],
    overheated_threshold_20d: Optional[float],
) -> str:
    """按优先级 overheated -> strong -> low_level_active -> improving -> neutral 解析。

    把 ``low_level_active`` 放在 ``improving`` 之前，是 Phase 1 review 的产品调
    整：当一个板块在 60 日维度仍弱、但近 5 日开始上涨并放量时，应识别为"低位
    异动"早期信号，而不是被通用的 ``improving`` 抢占。
    """

    if (
        return_5d is not None
        and return_20d is not None
        and overheated_threshold_5d is not None
        and overheated_threshold_20d is not None
        and return_5d >= overheated_threshold_5d
        and return_20d >= overheated_threshold_20d
    ):
        return "overheated"
    if (
        return_5d is not None
        and return_20d is not None
        and relative_return is not None
        and return_5d > 0
        and return_20d > 0
        and relative_return > 0
    ):
        return "strong"
    if (
        return_60d is not None
        and return_5d is not None
        and turnover_amount_change is not None
        and return_60d <= 0
        and return_5d > 0
        and turnover_amount_change > 0
    ):
        return "low_level_active"
    if (
        return_5d is not None
        and turnover_amount_change is not None
        and return_5d > 0
        and turnover_amount_change > 0
    ):
        return "improving"
    return "neutral"


def _percentile_threshold(values: Sequence[Optional[float]], top_pct: float) -> Optional[float]:
    """返回"前 top_pct"对应的最小入选值；样本不足时返回 None。"""

    valid = sorted([v for v in values if v is not None], reverse=True)
    if not valid:
        return None
    cutoff = max(1, int(round(len(valid) * top_pct)))
    cutoff = min(cutoff, len(valid))
    return valid[cutoff - 1]


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def _aggregate_score(
    short_norm: Optional[float],
    mid_norm: Optional[float],
    relative_norm: Optional[float],
    turnover_norm: Optional[float],
    breadth_norm: Optional[float],
) -> Optional[float]:
    components = {
        "short_term": short_norm,
        "mid_term": mid_norm,
        "relative": relative_norm,
        "turnover": turnover_norm,
        "breadth": breadth_norm,
    }
    valid = {k: v for k, v in components.items() if v is not None}
    if not valid:
        return None
    weight_sum = sum(SCORE_WEIGHTS[k] for k in valid)
    if weight_sum == 0:
        return None
    weighted = sum(SCORE_WEIGHTS[k] * v for k, v in valid.items())
    return weighted / weight_sum


# ---------------------------------------------------------------------------
# Chart series
# ---------------------------------------------------------------------------


def _normalize_series(daily: Sequence[DailyBar]) -> List[ChartSeriesPoint]:
    if not daily:
        return []
    base = daily[0].close
    if base == 0:
        return [ChartSeriesPoint(date=d.date, value=0.0) for d in daily]
    return [
        ChartSeriesPoint(date=d.date, value=round(d.close / base * 100.0, 4))
        for d in daily
    ]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_sector_rotation(
    snapshot: MarketSnapshot,
    periods: Sequence[int] = (1, 5, 20, 60),
) -> SectorRotationResult:
    """计算所有板块的轮动指标，并构造 chart_series。"""

    warnings: List[str] = []

    # ---- 基础引用 ----
    benchmark: BenchmarkData = snapshot.benchmark
    companies_by_code: Dict[str, CompanyData] = {c.code: c for c in snapshot.companies}

    # ---- 基准 20 日收益（用于 relative_return）----
    benchmark_relative_return: Optional[float] = _period_return(
        benchmark.daily, RELATIVE_RETURN_PERIOD
    )
    if benchmark_relative_return is None and benchmark.daily:
        warnings.append("benchmark_history_insufficient_for_relative_return")
    elif not benchmark.daily:
        warnings.append("benchmark_daily_missing")

    # ---- 全市场成交额代理（使用所有板块当日成交额之和）----
    market_turnover_total = 0.0
    for s in snapshot.sectors:
        if s.daily:
            market_turnover_total += s.daily[-1].turnover_amount
    if market_turnover_total <= 0:
        warnings.append("market_turnover_total_unavailable")

    # ---- 第一遍：每个板块的原始指标 ----
    raw: List[Dict[str, Any]] = []
    for sector in snapshot.sectors:
        entry_warnings: List[str] = []
        returns_by_period, return_warnings = _compute_returns(sector.daily, periods)
        entry_warnings.extend(return_warnings)

        return_1d = returns_by_period.get(1)
        return_5d = returns_by_period.get(5)
        return_20d = returns_by_period.get(20)
        return_60d = returns_by_period.get(60)

        sector_relative_return: Optional[float]
        if return_20d is None or benchmark_relative_return is None:
            sector_relative_return = None
        else:
            sector_relative_return = return_20d - benchmark_relative_return

        turnover_change = _compute_turnover_amount_change(sector.daily)
        if turnover_change is None and sector.daily:
            entry_warnings.append("turnover_baseline_unavailable")

        market_share: Optional[float]
        if sector.daily and market_turnover_total > 0:
            market_share = sector.daily[-1].turnover_amount / market_turnover_total
        else:
            market_share = None

        rising_ratio, rising_warning = _compute_rising_stock_ratio(sector, companies_by_code)
        if rising_warning:
            entry_warnings.append(rising_warning)

        raw.append(
            {
                "sector": sector,
                "returns": returns_by_period,
                "return_1d": return_1d,
                "return_5d": return_5d,
                "return_20d": return_20d,
                "return_60d": return_60d,
                "relative_return": sector_relative_return,
                "turnover_amount_change": turnover_change,
                "market_turnover_share": market_share,
                "rising_stock_ratio": rising_ratio,
                "warnings": entry_warnings,
            }
        )

    # ---- 第二遍：跨板块计算（rank_change_5d / overheated 阈值 / score 归一化）----
    current_return_1d = [r["return_1d"] for r in raw]
    current_ranks = _rank_descending(current_return_1d)

    past_return_1d: List[Optional[float]] = [
        _period_return(r["sector"].daily, 1, offset=RANK_CHANGE_OFFSET) for r in raw
    ]
    past_ranks = _rank_descending(past_return_1d)

    rank_changes: List[Optional[int]] = []
    for cur, past in zip(current_ranks, past_ranks):
        if cur is None or past is None:
            rank_changes.append(None)
        else:
            rank_changes.append(past - cur)

    overheated_threshold_5d = _percentile_threshold(
        [r["return_5d"] for r in raw], OVERHEATED_PERCENTILE
    )
    overheated_threshold_20d = _percentile_threshold(
        [r["return_20d"] for r in raw], OVERHEATED_PERCENTILE
    )

    short_norm = _min_max_normalize([r["return_5d"] for r in raw])
    mid_norm = _min_max_normalize([r["return_20d"] for r in raw])
    relative_norm = _min_max_normalize([r["relative_return"] for r in raw])
    turnover_norm = _min_max_normalize([r["turnover_amount_change"] for r in raw])
    breadth_norm = _min_max_normalize([r["rising_stock_ratio"] for r in raw])

    # ---- 第三遍：构造 SectorEntry ----
    entries: List[SectorEntry] = []
    for idx, r in enumerate(raw):
        sector: SectorData = r["sector"]
        state = _resolve_state(
            return_5d=r["return_5d"],
            return_20d=r["return_20d"],
            return_60d=r["return_60d"],
            relative_return=r["relative_return"],
            turnover_amount_change=r["turnover_amount_change"],
            overheated_threshold_5d=overheated_threshold_5d,
            overheated_threshold_20d=overheated_threshold_20d,
        )
        score = _aggregate_score(
            short_norm[idx],
            mid_norm[idx],
            relative_norm[idx],
            turnover_norm[idx],
            breadth_norm[idx],
        )
        entry = SectorEntry(
            sector_id=sector.sector_id,
            sector_name=sector.sector_name,
            classification_system=snapshot.classification_system,
            return_1d=_round_or_none(r["return_1d"], 6),
            return_5d=_round_or_none(r["return_5d"], 6),
            return_20d=_round_or_none(r["return_20d"], 6),
            return_60d=_round_or_none(r["return_60d"], 6),
            relative_return=_round_or_none(r["relative_return"], 6),
            turnover_amount_change=_round_or_none(r["turnover_amount_change"], 6),
            market_turnover_share=_round_or_none(r["market_turnover_share"], 6),
            rising_stock_ratio=_round_or_none(r["rising_stock_ratio"], 6),
            rank_change_5d=rank_changes[idx],
            state=state,
            score=_round_or_none(score, 2),
            warnings=list(r["warnings"]),
        )
        entries.append(entry)

    # ---- chart_series（板块归一化 + 基准线）----
    chart_series: List[ChartSeries] = []
    for r in raw:
        sector = r["sector"]
        chart_series.append(
            ChartSeries(
                series_id=sector.sector_id,
                series_name=sector.sector_name,
                type="sector",
                points=_normalize_series(sector.daily),
            )
        )
    chart_series.append(
        ChartSeries(
            series_id=benchmark.id or "benchmark",
            series_name=benchmark.name or "benchmark",
            type="benchmark",
            points=_normalize_series(benchmark.daily),
        )
    )

    return SectorRotationResult(
        sectors=entries, chart_series=chart_series, warnings=warnings
    )


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------

# sectors 命令支持的排序字段 → SectorEntry 属性名。
SORT_FIELD_TO_ATTR: Dict[str, str] = {
    "return_1d": "return_1d",
    "return_5d": "return_5d",
    "return_20d": "return_20d",
    "return_60d": "return_60d",
    "relative_return": "relative_return",
    "turnover_amount_change": "turnover_amount_change",
    "rising_stock_ratio": "rising_stock_ratio",
    "score": "score",
}


def sort_entries(
    entries: Sequence[SectorEntry], sort_field: str
) -> List[SectorEntry]:
    """按 ``sort_field`` 从大到小排序；None 排到末尾。"""

    attr = SORT_FIELD_TO_ATTR.get(sort_field)
    if attr is None:
        # 未识别字段交还原顺序，由 CLI 层做参数校验。
        return list(entries)

    def sort_key(e: SectorEntry) -> Tuple[int, float]:
        value = getattr(e, attr)
        if value is None:
            return (1, 0.0)
        return (0, -float(value))

    return sorted(entries, key=sort_key)


__all__ = [
    "RELATIVE_RETURN_PERIOD",
    "SORT_FIELD_TO_ATTR",
    "SectorRotationResult",
    "compute_sector_rotation",
    "sort_entries",
]
