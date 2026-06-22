"""基于本地 SQLite 计算 PE/PB 历史分位与风险标记（Phase 6C）。

读取 sync 写入的 ``company_valuation_history`` / ``stocks`` /
``company_daily_snapshot`` / ``financial_metrics`` 表，计算：

- ``pe_percentile`` / ``pb_percentile``：当前估值在历史分布中的位置 ``[0.0, 1.0]``，
  0 = 历史最低，1.0 = 历史最高。下游 ``valuation.py`` 的 ``_history_percentile_score``
  期望此区间。
- ``warnings``：缺失 / 负 PE / 样本不足等质量警告（docs §20 要求不用 0 替代缺失）。
- ``risk_flags``：ST / 退市 / 停牌 / 亏损等风险标记。

分位配置由 ``config.PERCENTILE_CONFIG`` 提供，版本化以便复算和审计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import PERCENTILE_CONFIG


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class PercentileResult:
    """单只股票的估值分位计算结果。"""

    code: str
    pe_percentile: Optional[float] = None
    pb_percentile: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    config_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "pe_percentile": self.pe_percentile,
            "pb_percentile": self.pb_percentile,
            "warnings": list(self.warnings),
            "config_version": self.config_version,
        }


# ---------------------------------------------------------------------------
# 核心分位计算
# ---------------------------------------------------------------------------


def _percentile_rank(current: float, history: List[float]) -> float:
    """当前值在历史分布中的百分位 ``[0.0, 1.0]``。

    0 = 历史最低，1.0 = 历史最高。使用"低于当前值的比例"方法：
    ``count(v < current) / len(history)``。对于 ``len(history) >= 60``（默认
    ``min_samples``），最高值约 0.983，最低值 0.0，足以区分估值区间。
    """

    n = len(history)
    if n == 0:
        return 0.0
    below = sum(1 for v in history if v < current)
    return below / n


def compute_valuation_percentiles(
    conn,
    code: str,
    analysis_date: str,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> PercentileResult:
    """基于 ``company_valuation_history`` 计算 PE/PB 历史分位。

    步骤：
    1. 读取 ``trade_date <= analysis_date`` 的全部 PE/PB 行。
    2. 取最新非空 PE/PB 作为"当前值"。
    3. 在回看窗口内收集有效历史样本（按 config 排除非正 PE/PB）。
    4. 若样本数 < ``min_samples``，返回 ``None`` + warning。
    5. 若当前 PE/PB 为负或缺失，返回 ``None`` + warning。
    6. 计算 ``_percentile_rank``。
    """
    cfg = config or PERCENTILE_CONFIG
    lookback_days = int(cfg.get("lookback_days", 1825))
    min_samples = int(cfg.get("min_samples", 60))
    exclude_non_positive_pe = bool(cfg.get("exclude_non_positive_pe", True))
    exclude_non_positive_pb = bool(cfg.get("exclude_non_positive_pb", True))
    config_version = str(cfg.get("version", ""))

    start_date = (
        datetime.fromisoformat(analysis_date) - timedelta(days=lookback_days)
    ).date().isoformat()

    rows = conn.execute(
        "SELECT trade_date, pe, pb FROM company_valuation_history "
        "WHERE code = ? AND trade_date <= ? AND trade_date >= ? "
        "ORDER BY trade_date",
        (code, analysis_date, start_date),
    ).fetchall()

    result = PercentileResult(code=code, config_version=config_version)

    if not rows:
        result.warnings.extend(["missing_pe", "missing_pb"])
        return result

    # 当前值：最新的非空 PE/PB（rows 按 trade_date 升序，从尾部找）。
    current_pe: Optional[float] = None
    current_pb: Optional[float] = None
    for row in reversed(rows):
        if current_pe is None and row[1] is not None:
            current_pe = float(row[1])
        if current_pb is None and row[2] is not None:
            current_pb = float(row[2])
        if current_pe is not None and current_pb is not None:
            break

    # PE 分位
    pe_history = [float(r[1]) for r in rows if r[1] is not None]
    if exclude_non_positive_pe:
        pe_history = [v for v in pe_history if v > 0]

    if current_pe is None:
        result.warnings.append("missing_pe")
    elif current_pe <= 0:
        result.warnings.append("negative_pe")
        # 负 PE 不参与分位分布，无法定位 → None
    elif len(pe_history) < min_samples:
        result.warnings.append(f"insufficient_pe_samples:{len(pe_history)}")
    else:
        # 当前 PE 也加入分布再计算 rank（保证 rank 有意义）
        pe_history_with_current = pe_history + [current_pe]
        result.pe_percentile = _percentile_rank(current_pe, pe_history_with_current)

    # PB 分位
    pb_history = [float(r[2]) for r in rows if r[2] is not None]
    if exclude_non_positive_pb:
        pb_history = [v for v in pb_history if v > 0]

    if current_pb is None:
        result.warnings.append("missing_pb")
    elif current_pb <= 0:
        result.warnings.append("negative_pb")
    elif len(pb_history) < min_samples:
        result.warnings.append(f"insufficient_pb_samples:{len(pb_history)}")
    else:
        pb_history_with_current = pb_history + [current_pb]
        result.pb_percentile = _percentile_rank(current_pb, pb_history_with_current)

    return result


# ---------------------------------------------------------------------------
# 风险标记
# ---------------------------------------------------------------------------


def compute_company_risk_flags(
    conn,
    code: str,
    analysis_date: str,
) -> List[str]:
    """检查公司的风险标记：ST / 退市 / 停牌 / 亏损。

    读取 ``stocks``（名称）、``company_daily_snapshot``（停牌）、
    ``financial_metrics``（亏损）表，返回风险标记字符串列表。
    """
    flags: List[str] = []

    # ST / 退市：从 stocks 表名称判断
    stock_row = conn.execute(
        "SELECT name FROM stocks WHERE code = ?", (code,)
    ).fetchone()
    if stock_row and stock_row[0]:
        name = str(stock_row[0])
        if "ST" in name or "*ST" in name:
            flags.append("st")
        if "退" in name:
            flags.append("delisting_risk")

    # 停牌：analysis_date 当日无成交快照
    snap = conn.execute(
        "SELECT close FROM company_daily_snapshot "
        "WHERE code = ? AND trade_date = ?",
        (code, analysis_date),
    ).fetchone()
    if not snap or snap[0] is None:
        flags.append("suspended")

    # 亏损：最新已披露财报的 net_margin < 0
    fin = conn.execute(
        "SELECT net_margin FROM financial_metrics "
        "WHERE code = ? AND disclosure_date <= ? "
        "ORDER BY period_end_date DESC LIMIT 1",
        (code, analysis_date),
    ).fetchone()
    if fin and fin[0] is not None and float(fin[0]) < 0:
        flags.append("loss")

    return flags


__all__ = [
    "PercentileResult",
    "compute_valuation_percentiles",
    "compute_company_risk_flags",
]
