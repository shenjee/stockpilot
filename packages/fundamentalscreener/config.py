"""Fundamental Screener 默认配置常量。

只声明不依赖真实数据的默认周期、排序、基准、格式、分类口径和枚举常量。
Phase 0 不读取任何用户私有配置；后续 Phase 可在此扩展权重和阈值。
"""

from __future__ import annotations

from typing import Tuple

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

# 板块状态枚举。
SECTOR_STATES: Tuple[str, ...] = (
    "strong",
    "improving",
    "overheated",
    "low_level_active",
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
    "DEFAULT_BENCHMARK",
    "DEFAULT_CLASSIFICATION_SYSTEM",
    "DEFAULT_FORMAT",
    "DEFAULT_PERIODS",
    "DEFAULT_SECTOR_SORT",
    "DEFAULT_TOP",
    "SECTOR_STATES",
    "SUPPORTED_CLASSIFICATION_SYSTEMS",
    "SUPPORTED_FORMATS",
    "SUPPORTED_SECTOR_SORTS",
    "VALUATION_LABELS",
]
