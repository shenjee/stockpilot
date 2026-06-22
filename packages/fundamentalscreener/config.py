"""Fundamental Screener 默认配置常量。

只声明不依赖真实数据的默认周期、排序、基准、格式、分类口径和枚举常量。
Phase 0 不读取任何用户私有配置；后续 Phase 可在此扩展权重和阈值。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# 默认周期，用于板块收益计算（Phase 1 起真正使用）。
DEFAULT_PERIODS: Tuple[int, ...] = (1, 5, 20, 60)

# 默认基准指数。
DEFAULT_BENCHMARK: str = "hs300"

# 默认 sectors 排序字段。
DEFAULT_SECTOR_SORT: str = "return_1d"

# 默认输出格式。
DEFAULT_FORMAT: str = "json"

# 默认 Top N。
DEFAULT_TOP: int = 20

# 默认板块分类口径。Phase 0/1 仅作为字符串字段保留。
DEFAULT_CLASSIFICATION_SYSTEM: str = "concept"

# 受支持的板块分类口径。Phase 0/1 不强制校验真实分类。
SUPPORTED_CLASSIFICATION_SYSTEMS: Tuple[str, ...] = (
    "sw_l1",
    "citic_l1",
    "concept",
    "custom",
)

# 受支持的输出格式。
SUPPORTED_FORMATS: Tuple[str, ...] = ("json", "markdown", "csv")

# sectors 命令支持的排序字段。
SUPPORTED_SECTOR_SORTS: Tuple[str, ...] = (
    "return_1d",
    "return_5d",
    "return_20d",
    "return_60d",
    "relative_return",
    "turnover_amount_change",
    "rising_stock_ratio",
    "score",
)

# companies 命令默认排序字段。
DEFAULT_COMPANY_SORT: str = "combined_score"

# companies 命令支持的排序字段。
SUPPORTED_COMPANY_SORTS: Tuple[str, ...] = (
    "combined_score",
    "leader_score",
    "attention_score",
    "market_cap",
    "turnover_amount",
    "turnover_rate",
    "sector_return_rank",
)

# 排序方向：默认 desc，sector_return_rank 用 asc（rank=1 表示最强）。
COMPANY_SORT_ASCENDING: Tuple[str, ...] = ("sector_return_rank",)

# Phase 2 第一版 combined_score 权重（仅保留作为历史参考，不再被 runtime 使用）：
#   combined_score = leader_score * 0.4 + attention_score * 0.6
# 现在 runtime 默认使用 COMBINED_SCORE_WEIGHTS（Phase 3/4 接入后的升级版）。
COMBINED_SCORE_WEIGHTS_PHASE2: Tuple[Tuple[str, float], ...] = (
    ("leader_score", 0.4),
    ("attention_score", 0.6),
)

# Phase 3/4 接入后的 combined_score 权重，对齐 docs §14：
#   combined_score =
#     leader_score * 0.20
#   + attention_score * 0.20
#   + financial_quality_score * 0.35
#   + valuation_score * 0.25
# 缺失分量按可用权重重新归一（与板块强度分、估值分一致），单字段缺失不会
# 把整体打成 0；fin/val 同时缺失时由调用方按 Phase 2 兜底回退到 leader/attention。
COMBINED_SCORE_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("leader_score", 0.20),
    ("attention_score", 0.20),
    ("financial_quality_score", 0.35),
    ("valuation_score", 0.25),
)

# 候选分组阈值（基于 combined_score）。Phase 2 仅依赖板块内强弱信号，
# 不带财务/估值约束；Phase 5 会引入 flags 与硬伤判断。
COMPANY_GROUP_PRIORITY_THRESHOLD: float = 70.0
COMPANY_GROUP_WATCH_THRESHOLD: float = 50.0

# financials 命令默认排序字段。
DEFAULT_FINANCIAL_SORT: str = "score"

# financials 命令支持的排序字段。
SUPPORTED_FINANCIAL_SORTS: Tuple[str, ...] = (
    "score",
    "revenue_yoy",
    "net_profit_yoy",
    "deducted_net_profit_yoy",
    "gross_margin",
    "net_margin",
    "roe",
    "operating_cashflow_to_profit",
    "debt_to_asset",
)

# 财务质量分各维度权重，与 docs §7.3 一致：
#   profitability 25 / growth 25 / cashflow 25 / leverage 15 / efficiency 10。
# 缺失分量按可用权重重新归一（与板块强度分一致），避免单字段缺失把整体打成 0。
FINANCIAL_SCORE_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("profitability", 0.25),
    ("growth", 0.25),
    ("cashflow", 0.25),
    ("leverage", 0.15),
    ("efficiency", 0.10),
)

# 财务异常 flags 阈值，与 §15 第一版规则对齐。仅在数据可用时触发，
# 缺失字段不构成 flag（避免误警）。
FINANCIAL_FLAG_THRESHOLDS: Dict[str, float] = {
    # weak_cashflow: net_profit_yoy > 0 且 op_cashflow/profit < 0.5
    "weak_cashflow_op_cf_ratio": 0.5,
    # receivable_growth_risk: ar_yoy > revenue_yoy + delta
    "receivable_excess_over_revenue": 0.2,
    # inventory_growth_risk: inv_yoy > revenue_yoy + delta
    "inventory_excess_over_revenue": 0.2,
    # high_debt: debt_to_asset > 0.7
    "high_debt_ratio": 0.7,
    # weak_core_profit: deducted < net - delta
    "weak_core_profit_gap": 0.2,
}

# 财务异常 flag 枚举，方便测试和后续 UI 引用。
FINANCIAL_ABNORMAL_FLAGS: Tuple[str, ...] = (
    "weak_cashflow",
    "receivable_growth_risk",
    "inventory_growth_risk",
    "high_debt",
    "gross_margin_decline",
    "weak_core_profit",
)

# valuations 命令默认排序字段。
DEFAULT_VALUATION_SORT: str = "score"

# valuations 命令支持的排序字段。
SUPPORTED_VALUATION_SORTS: Tuple[str, ...] = (
    "score",
    "pe",
    "pb",
    "ps",
    "peg",
    "dividend_yield",
    "pe_percentile",
    "pb_percentile",
)

# 估值评分各维度权重，与 docs §8.4 一致：
#   pe_percentile 35 / pb_percentile / industry_valuation_position 合计 35 /
#   peg(成长匹配) 20 / dividend_yield(股息) 10。
# 这里把"历史估值分位分"拆成 pe_pct 与 pb_pct 等权（合计 35%）；
# "行业相对估值分"使用 industry_valuation_position 映射（35%）；
# 成长匹配分使用 peg 映射；股息分使用 dividend_yield 映射。
VALUATION_SCORE_WEIGHTS: Tuple[Tuple[str, float], ...] = (
    ("history_pe", 0.175),
    ("history_pb", 0.175),
    ("industry", 0.35),
    ("growth_match", 0.20),
    ("dividend", 0.10),
)

# 估值 label 规则阈值，与 docs §16 第一版规则对齐。
VALUATION_LABEL_THRESHOLDS: Dict[str, float] = {
    # low_need_quality_check: pe_pct < 0.35 或 pb_pct < 0.35
    "low_pct_threshold": 0.35,
    # fair: 0.35 <= pe_pct/pb_pct <= 0.70
    "fair_lower": 0.35,
    "fair_upper": 0.70,
    # expensive: pe_pct > 0.80 或 pb_pct > 0.80
    "expensive_pct_threshold": 0.80,
    # expensive_but_supported: pe_pct/pb_pct 高位但 peg <= 1.5
    "supported_peg_threshold": 1.5,
}

# 估值分位计算配置（Phase 6C）。版本化以便复算和审计。
# docs §20 要求：窗口长度、最小有效样本数、非正 PE 和亏损公司的处理规则。
PERCENTILE_CONFIG: Dict[str, Any] = {
    "version": "percentile-v1",
    "lookback_days": 1825,       # 5 年回看窗口
    "min_samples": 60,           # 有效样本下限（约 3 个月交易日）
    "exclude_non_positive_pe": True,   # 负 PE 不参与分位分布
    "exclude_non_positive_pb": True,   # 负 PB 不参与分位分布
}

# label 优先级：not_applicable > expensive_but_supported > expensive >
# low_need_quality_check > fair。docs §16 第一版规则。
# expensive_but_supported 与 expensive 在新规则里互斥（同一公司只可能命中其
# 中之一），优先级仅用于多规则同时命中时的兜底裁决。
VALUATION_LABEL_PRIORITY: Tuple[str, ...] = (
    "not_applicable",
    "expensive_but_supported",
    "expensive",
    "low_need_quality_check",
    "fair",
)

# industry_valuation_position → 行业相对估值分映射。
INDUSTRY_POSITION_SCORE: Dict[str, float] = {
    "low": 80.0,
    "mid": 60.0,
    "high": 30.0,
    "unknown": 50.0,
}

# 板块状态枚举。顺序与 sector_rotation._resolve_state 的优先级一致：
# overheated > strong > low_level_active > improving > neutral。
SECTOR_STATES: Tuple[str, ...] = (
    "overheated",
    "strong",
    "low_level_active",
    "improving",
    "neutral",
)

# 候选分组枚举。
CANDIDATE_GROUPS: Tuple[str, ...] = ("priority", "watch", "cautious")

# 估值标签枚举。
VALUATION_LABELS: Tuple[str, ...] = (
    "low_need_quality_check",
    "fair",
    "expensive_but_supported",
    "expensive",
    "not_applicable",
)

__all__ = [
    "CANDIDATE_GROUPS",
    "COMBINED_SCORE_WEIGHTS",
    "COMBINED_SCORE_WEIGHTS_PHASE2",
    "COMPANY_GROUP_PRIORITY_THRESHOLD",
    "COMPANY_GROUP_WATCH_THRESHOLD",
    "COMPANY_SORT_ASCENDING",
    "DEFAULT_BENCHMARK",
    "DEFAULT_CLASSIFICATION_SYSTEM",
    "DEFAULT_COMPANY_SORT",
    "DEFAULT_FINANCIAL_SORT",
    "DEFAULT_FORMAT",
    "DEFAULT_PERIODS",
    "DEFAULT_SECTOR_SORT",
    "DEFAULT_TOP",
    "DEFAULT_VALUATION_SORT",
    "FINANCIAL_ABNORMAL_FLAGS",
    "FINANCIAL_FLAG_THRESHOLDS",
    "FINANCIAL_SCORE_WEIGHTS",
    "INDUSTRY_POSITION_SCORE",
    "PERCENTILE_CONFIG",
    "SECTOR_STATES",
    "SUPPORTED_CLASSIFICATION_SYSTEMS",
    "SUPPORTED_COMPANY_SORTS",
    "SUPPORTED_FINANCIAL_SORTS",
    "SUPPORTED_FORMATS",
    "SUPPORTED_SECTOR_SORTS",
    "SUPPORTED_VALUATION_SORTS",
    "VALUATION_LABELS",
    "VALUATION_LABEL_PRIORITY",
    "VALUATION_LABEL_THRESHOLDS",
    "VALUATION_SCORE_WEIGHTS",
]
