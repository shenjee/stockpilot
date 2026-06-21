"""完整筛选编排（Phase 5）。

把板块轮动、板块内公司排名、财务质量与估值的结果串成"selected_sectors +
candidates(priority/watch/cautious)"。设计原则：

- 不写新算法：板块指标走 ``sector_rotation``，公司排名 + fin/val 补齐走
  ``company_ranking.compute_company_ranking``（Phase 2 升级版 combined_score 已
  在那里实现）。本模块只做编排与硬约束。
- 顺序与边界严格：先按 sector_sort 取 Top N 板块，再在每个板块内按
  combined_score 取 Top N 公司，避免出现"全市场撒大网 + 后置过滤"的
  数据爆炸路径。
- 硬约束（docs §17 Supplement）：估值
  ``label=not_applicable`` 即使 ``score`` 算得出来，也必须降到
  ``cautious``。``company_ranking`` 已经在该情况把
  ``valuation_score`` 置 None 并写入 ``valuation_not_applicable``
  warning；本模块据此判定，而不是去重新读 valuation 数据。
- group 与桶一致：硬约束后会改变候选所在桶，candidate 自身的 ``group``
  字段必须同步覆盖，避免出现"在 cautious 桶里、字段写 watch"的歧义。
- 分数可追溯（docs §17 DoD）：每个 candidate 除了带回完整
  ``CompanyEntry`` 字段，还附带 ``financial`` / ``valuation`` 子对象，
  让 skill / UI 能解释 ``financial_quality_score`` / ``valuation_score``
  / 异常 flags 的来源。
- 缺失可降级：板块或公司任意一层失败都只追加 ``warnings``，不抛错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .company_ranking import compute_company_ranking, sort_companies
from .config import (
    DEFAULT_PERIODS,
    DEFAULT_SECTOR_SORT,
    SUPPORTED_SECTOR_SORTS,
)
from .repositories import MarketSnapshot
from .schema import CompanyEntry, FinancialEntry, SectorEntry, ValuationEntry
from .sector_rotation import compute_sector_rotation, sort_entries


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ScreeningResult:
    """编排结果。``candidates`` 已按 priority/watch/cautious 分桶。"""

    selected_sectors: List[SectorEntry] = field(default_factory=list)
    candidates: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=lambda: {"priority": [], "watch": [], "cautious": []}
    )
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 编排辅助
# ---------------------------------------------------------------------------


# 估值"硬约束"标记：company_ranking 写入这个 warning 即代表关键估值字段
# 不完整，不能进入 priority/watch。
_VALUATION_HARD_CONSTRAINT_FLAG = "valuation_not_applicable"


def _resolve_group(entry: CompanyEntry) -> str:
    """根据 Phase 5 硬约束规则决定候选分组。

    - 估值不适用 → ``cautious``（即便 ``combined_score`` 很高也必须降级）。
    - 排名结果没算出 ``group``（缺数据或样本太少）→ ``cautious``。
    - 其它情况沿用 ``company_ranking`` 给出的 group。
    """

    if _VALUATION_HARD_CONSTRAINT_FLAG in (entry.warnings or []):
        return "cautious"
    if entry.group is None:
        return "cautious"
    return entry.group


def _to_candidate_dict(
    entry: CompanyEntry,
    sector_id: str,
    sector_name: str,
    resolved_group: str,
    financial: FinancialEntry | None,
    valuation: ValuationEntry | None,
) -> Dict[str, Any]:
    """把 CompanyEntry 转成 candidate 字典并补板块上下文 + 分数来源。

    - 保留 ``CompanyEntry`` 全部字段，附加 ``sector_id`` / ``sector_name``，
      让 skill / UI 不需要再回查"这家公司属于哪个板块"。
    - 覆盖 ``group`` 为 ``resolved_group``：硬约束可能把候选从 watch 拉到
      cautious，字段必须与所在桶一致，避免 JSON 消费方在桶名和字段之间
      抉择。
    - 附带 ``financial`` / ``valuation`` 子对象：``financial_quality_score``
      和 ``valuation_score`` 都是分量分，没有原始指标和异常 flags 的话调用
      方无法解释 "为什么是 30 分 / 为什么 not_applicable"。这两个子对象
      已经包含完整 ``FinancialEntry`` / ``ValuationEntry`` 序列化结果。
    """

    payload = dict(entry.to_dict())
    payload["sector_id"] = sector_id
    payload["sector_name"] = sector_name
    payload["group"] = resolved_group
    payload["financial"] = financial.to_dict() if financial is not None else None
    payload["valuation"] = valuation.to_dict() if valuation is not None else None
    return payload


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_screening(
    snapshot: MarketSnapshot,
    sector_top: int = 10,
    company_top: int = 5,
    sector_sort: str = DEFAULT_SECTOR_SORT,
    periods: Sequence[int] = DEFAULT_PERIODS,
) -> ScreeningResult:
    """执行完整筛选。

    步骤：

    1. ``compute_sector_rotation`` 计算所有板块指标。
    2. 按 ``sector_sort`` 排序后取前 ``sector_top`` 个板块。
    3. 对每个板块调用 ``compute_company_ranking``（其中已完成 fin/val 补齐
       和升级 combined_score），按 ``combined_score`` 取前 ``company_top``。
    4. 应用硬约束生成 priority/watch/cautious 三桶。
    5. 跨板块的 warnings（如基准缺失）汇总到顶层；单板块缺失追加可读 warning。
    """

    warnings: List[str] = []

    # ---- Step 1/2: 板块层 ----
    if sector_sort not in SUPPORTED_SECTOR_SORTS:
        # CLI 层已用 argparse choices 校验，这里只做防御性兜底。
        warnings.append(f"invalid_sector_sort_fallback: {sector_sort!r}")
        sector_sort = DEFAULT_SECTOR_SORT

    rotation = compute_sector_rotation(snapshot, periods=tuple(periods))
    warnings.extend(rotation.warnings)

    ordered_sectors = sort_entries(rotation.sectors, sector_sort)
    if sector_top is not None and sector_top >= 0:
        selected_sectors = list(ordered_sectors[:sector_top])
    else:
        selected_sectors = list(ordered_sectors)

    if not selected_sectors:
        warnings.append("no_sectors_selected")
        return ScreeningResult(
            selected_sectors=[],
            candidates={"priority": [], "watch": [], "cautious": []},
            warnings=warnings,
        )

    # ---- Step 3/4: 公司层 + 硬约束 ----
    candidates: Dict[str, List[Dict[str, Any]]] = {
        "priority": [],
        "watch": [],
        "cautious": [],
    }

    for sector in selected_sectors:
        ranking = compute_company_ranking(snapshot, sector.sector_id)
        # 跨板块/无公司的可读提示提升到顶层，方便调用方排查。
        for w in ranking.warnings:
            warnings.append(f"sector={sector.sector_id}: {w}")

        ordered = sort_companies(ranking.companies, "combined_score")
        if company_top is not None and company_top >= 0:
            ordered = ordered[:company_top]

        for entry in ordered:
            group = _resolve_group(entry)
            candidates[group].append(
                _to_candidate_dict(
                    entry,
                    sector.sector_id,
                    sector.sector_name,
                    resolved_group=group,
                    financial=ranking.financials.get(entry.code),
                    valuation=ranking.valuations.get(entry.code),
                )
            )

    return ScreeningResult(
        selected_sectors=selected_sectors,
        candidates=candidates,
        warnings=warnings,
    )


__all__ = [
    "ScreeningResult",
    "run_screening",
]
