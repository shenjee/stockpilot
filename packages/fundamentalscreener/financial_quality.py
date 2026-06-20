"""财务质量评分与异常 flags（Phase 3）。

输入：``FinancialData``（来自 ``FixtureRepository``，未来由 SQLite/外部数据源
提供）。

输出：``FinancialQualityResult``（包含 ``FinancialEntry`` 列表）。

设计原则（与用户约定的 Phase 3 风格保持一致）：

- 只做量化指标和阈值规则，不写文字研报、不输出买卖建议。
- ``score`` 使用"分量分数 + 权重"的阈值打分，而不是横向 min-max。原因：
  ``--codes A`` 单条查询时 cohort 太小，min-max 没有意义；阈值打分独立于
  样本量，对单条查询和批量查询行为一致。
- 各分量分（profitability/growth/cashflow/leverage/efficiency）在 0-100 之间
  通过简单分段线性映射得到；缺失字段不参与该分量平均，分量本身缺失则不进入
  汇总。
- ``abnormal_flags`` 严格按 docs §15 第一版规则；阈值集中在
  ``config.FINANCIAL_FLAG_THRESHOLDS``。
- ``gross_margin_decline`` 仅在 ``gross_margin_yoy_change`` 提供时才参与判断，
  避免误报。
- 缺失字段写入 entry 级 ``warnings``，整命令不崩溃。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    FINANCIAL_ABNORMAL_FLAGS,
    FINANCIAL_FLAG_THRESHOLDS,
    FINANCIAL_SCORE_WEIGHTS,
    SUPPORTED_FINANCIAL_SORTS,
)
from .repositories import CompanyData, FinancialData, MarketSnapshot
from .schema import FinancialEntry


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class FinancialQualityResult:
    companies: List[FinancialEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 分量分映射（分段线性）
# ---------------------------------------------------------------------------


def _ramp(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """value 在 [lo, hi] 内做线性映射到 0-100。

    - ``lo`` 是该指标"低分锚点"；``hi`` 是"高分锚点"。
    - 当 ``hi > lo`` 时单调递增；当 ``hi < lo`` 时单调递减（用于"越低越好"
      的指标，如负债率）。
    - ``None`` 透传。
    """

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


def _avg_available(values: Sequence[Optional[float]]) -> Optional[float]:
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _profitability_score(f: FinancialData) -> Optional[float]:
    # ROE: 0% → 0；10% → 50；20% → 100。
    roe_score = _ramp(f.roe, 0.0, 0.20)
    # 毛利率：10% → 0；50% → 100。
    gm_score = _ramp(f.gross_margin, 0.10, 0.50)
    # 净利率：0% → 0；20% → 100。
    nm_score = _ramp(f.net_margin, 0.0, 0.20)
    return _avg_available([roe_score, gm_score, nm_score])


def _growth_score(f: FinancialData) -> Optional[float]:
    # 增速：-10% → 0；40% → 100。
    rev = _ramp(f.revenue_yoy, -0.10, 0.40)
    npy = _ramp(f.net_profit_yoy, -0.10, 0.40)
    dnp = _ramp(f.deducted_net_profit_yoy, -0.10, 0.40)
    return _avg_available([rev, npy, dnp])


def _cashflow_score(f: FinancialData) -> Optional[float]:
    # 经营现金流/利润：0 → 0；1.5 → 100（>1 即视为利润含金量良好）。
    ocf_score = _ramp(f.operating_cashflow_to_profit, 0.0, 1.5)
    # 自由现金流：只取符号，正现金流 100，负现金流 0；为 0 视为 50。
    fcf_score: Optional[float]
    if f.free_cashflow is None:
        fcf_score = None
    elif f.free_cashflow > 0:
        fcf_score = 100.0
    elif f.free_cashflow < 0:
        fcf_score = 0.0
    else:
        fcf_score = 50.0
    return _avg_available([ocf_score, fcf_score])


def _leverage_score(f: FinancialData) -> Optional[float]:
    # 越低越好：debt 0.3 → 100；debt 0.7 → 0。
    debt_score = _ramp(f.debt_to_asset, 0.7, 0.3)
    # 有息负债率：0 → 100；0.4 → 0。
    ibd_score = _ramp(f.interest_bearing_debt_ratio, 0.4, 0.0)
    return _avg_available([debt_score, ibd_score])


def _efficiency_score(f: FinancialData) -> Optional[float]:
    """运营效率：应收/存货 yoy 与营收 yoy 的差距越大越扣分。"""

    if f.revenue_yoy is None:
        return None
    components: List[Optional[float]] = []
    if f.accounts_receivable_yoy is not None:
        gap = f.accounts_receivable_yoy - f.revenue_yoy
        # gap = -0.1（应收增速远低于营收，理想）→ 100；
        # gap = 0.2（应收远超营收，差）→ 0。
        components.append(_ramp(gap, 0.2, -0.1))
    if f.inventory_yoy is not None:
        gap = f.inventory_yoy - f.revenue_yoy
        components.append(_ramp(gap, 0.2, -0.1))
    return _avg_available(components)


# ---------------------------------------------------------------------------
# 汇总评分
# ---------------------------------------------------------------------------


def _aggregate_score(component_scores: Dict[str, Optional[float]]) -> Optional[float]:
    """按 ``FINANCIAL_SCORE_WEIGHTS`` 加权；缺失分量按可用权重重新归一。"""

    weights = dict(FINANCIAL_SCORE_WEIGHTS)
    valid = {k: v for k, v in component_scores.items() if v is not None}
    if not valid:
        return None
    weight_sum = sum(weights[k] for k in valid)
    if weight_sum == 0:
        return None
    return sum(weights[k] * v for k, v in valid.items()) / weight_sum


# ---------------------------------------------------------------------------
# 异常 flags
# ---------------------------------------------------------------------------


def _detect_abnormal_flags(f: FinancialData) -> List[str]:
    """严格按 docs §15 第一版规则。缺失字段不构成 flag。"""

    flags: List[str] = []
    th = FINANCIAL_FLAG_THRESHOLDS

    # weak_cashflow: net_profit_yoy > 0 且 op_cashflow/profit < 0.5
    if (
        f.net_profit_yoy is not None
        and f.operating_cashflow_to_profit is not None
        and f.net_profit_yoy > 0
        and f.operating_cashflow_to_profit < th["weak_cashflow_op_cf_ratio"]
    ):
        flags.append("weak_cashflow")

    # receivable_growth_risk: ar_yoy > revenue_yoy + 0.2
    if (
        f.accounts_receivable_yoy is not None
        and f.revenue_yoy is not None
        and f.accounts_receivable_yoy
        > f.revenue_yoy + th["receivable_excess_over_revenue"]
    ):
        flags.append("receivable_growth_risk")

    # inventory_growth_risk: inv_yoy > revenue_yoy + 0.2
    if (
        f.inventory_yoy is not None
        and f.revenue_yoy is not None
        and f.inventory_yoy > f.revenue_yoy + th["inventory_excess_over_revenue"]
    ):
        flags.append("inventory_growth_risk")

    # high_debt: debt_to_asset > 0.7
    if f.debt_to_asset is not None and f.debt_to_asset > th["high_debt_ratio"]:
        flags.append("high_debt")

    # gross_margin_decline: 仅当 gross_margin_yoy_change 提供时
    if (
        f.gross_margin_yoy_change is not None
        and f.gross_margin_yoy_change < 0
    ):
        flags.append("gross_margin_decline")

    # weak_core_profit: deducted < net - 0.2
    if (
        f.deducted_net_profit_yoy is not None
        and f.net_profit_yoy is not None
        and f.deducted_net_profit_yoy
        < f.net_profit_yoy - th["weak_core_profit_gap"]
    ):
        flags.append("weak_core_profit")

    return flags


# ---------------------------------------------------------------------------
# 缺失字段警告
# ---------------------------------------------------------------------------


# 构成核心打分输入的字段；任一缺失时写入 entry warnings，方便 UI/skill
# 显示"该分项不可比"。``gross_margin_yoy_change`` 是可选字段（fixture/数据源
# 不强制提供），不写 warning。
_REQUIRED_FIELDS: Tuple[str, ...] = (
    "revenue_yoy",
    "net_profit_yoy",
    "deducted_net_profit_yoy",
    "gross_margin",
    "net_margin",
    "roe",
    "operating_cashflow_to_profit",
    "free_cashflow",
    "debt_to_asset",
    "interest_bearing_debt_ratio",
    "accounts_receivable_yoy",
    "inventory_yoy",
)


def _missing_field_warnings(f: FinancialData) -> List[str]:
    return [name for name in _REQUIRED_FIELDS if getattr(f, name) is None]


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_financial_quality(
    snapshot: MarketSnapshot, codes: Sequence[str]
) -> FinancialQualityResult:
    """按给定 codes 顺序计算财务质量。

    缺失的 code 会在顶层 ``warnings`` 中以 ``code_not_found: <code>`` 形式
    报出，但不会阻断已有 code 的计算。
    """

    if not codes:
        return FinancialQualityResult(companies=[], warnings=["no_codes_provided"])

    fin_index: Dict[str, FinancialData] = {f.code: f for f in snapshot.financials}
    company_index: Dict[str, CompanyData] = {c.code: c for c in snapshot.companies}

    warnings: List[str] = []
    entries: List[FinancialEntry] = []
    for code in codes:
        f = fin_index.get(code)
        if f is None:
            warnings.append(f"code_not_found: {code}")
            continue

        component_scores = {
            "profitability": _profitability_score(f),
            "growth": _growth_score(f),
            "cashflow": _cashflow_score(f),
            "leverage": _leverage_score(f),
            "efficiency": _efficiency_score(f),
        }
        score = _aggregate_score(component_scores)
        flags = _detect_abnormal_flags(f)
        missing = _missing_field_warnings(f)
        entry_warnings: List[str] = []
        for name in missing:
            entry_warnings.append(f"missing_field: {name}")

        # 选择展示用的 name：FinancialData 优先，其次 CompanyData，其次空字符串。
        display_name = f.name or (
            company_index[code].name if code in company_index else ""
        )

        entries.append(
            FinancialEntry(
                code=code,
                name=display_name,
                revenue_yoy=f.revenue_yoy,
                net_profit_yoy=f.net_profit_yoy,
                deducted_net_profit_yoy=f.deducted_net_profit_yoy,
                gross_margin=f.gross_margin,
                net_margin=f.net_margin,
                roe=f.roe,
                operating_cashflow_to_profit=f.operating_cashflow_to_profit,
                free_cashflow=f.free_cashflow,
                debt_to_asset=f.debt_to_asset,
                interest_bearing_debt_ratio=f.interest_bearing_debt_ratio,
                accounts_receivable_yoy=f.accounts_receivable_yoy,
                inventory_yoy=f.inventory_yoy,
                score=_round_or_none(score, 2),
                abnormal_flags=flags,
                warnings=entry_warnings,
            )
        )

    return FinancialQualityResult(companies=entries, warnings=warnings)


# ---------------------------------------------------------------------------
# 排序
# ---------------------------------------------------------------------------


_SORT_FIELD_TO_ATTR: Dict[str, str] = {
    "score": "score",
    "revenue_yoy": "revenue_yoy",
    "net_profit_yoy": "net_profit_yoy",
    "deducted_net_profit_yoy": "deducted_net_profit_yoy",
    "gross_margin": "gross_margin",
    "net_margin": "net_margin",
    "roe": "roe",
    "operating_cashflow_to_profit": "operating_cashflow_to_profit",
    "debt_to_asset": "debt_to_asset",
}

# debt_to_asset 越低越好，按升序排列；其余字段按降序。
_FINANCIAL_ASCENDING: Tuple[str, ...] = ("debt_to_asset",)


def sort_financials(
    entries: Sequence[FinancialEntry], sort_field: str
) -> List[FinancialEntry]:
    if sort_field not in SUPPORTED_FINANCIAL_SORTS:
        return list(entries)
    attr = _SORT_FIELD_TO_ATTR[sort_field]
    ascending = sort_field in _FINANCIAL_ASCENDING

    def sort_key(e: FinancialEntry) -> Tuple[int, float]:
        value = getattr(e, attr)
        if value is None:
            return (1, 0.0)
        signed = float(value) if ascending else -float(value)
        return (0, signed)

    return sorted(entries, key=sort_key)


__all__ = [
    "FINANCIAL_ABNORMAL_FLAGS",
    "FinancialQualityResult",
    "compute_financial_quality",
    "sort_financials",
]
