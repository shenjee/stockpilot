"""数据质量报告（Phase 6A 最小骨架）。

Phase 6A 只定义结构和级别枚举，不实现复杂质量检查规则（属于 Phase 6D）。

设计：
- ``QualityIssue`` 表示一条具体问题：``level`` 必须是 ``error|warning|info``。
- ``QualityReport`` 聚合若干 ``QualityIssue``，并暴露 ``status`` 用于映射到
  snapshot 顶层 ``data_quality_status`` (``ok|degraded|stale|invalid``)。
- 状态决策：``error`` 越多越倾向 ``invalid``；至少一个 ``warning`` 但没有
  ``error`` 时为 ``degraded``；``info`` 不影响状态；全清空为 ``ok``。
  ``stale`` 由调用方在判断行情/估值过旧时显式设置（Phase 6D 接入）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .lineage import (
    QUALITY_STATUS_DEGRADED,
    QUALITY_STATUS_INVALID,
    QUALITY_STATUS_OK,
    QUALITY_STATUS_STALE,
    new_quality_report_id,
)

LEVEL_ERROR: str = "error"
LEVEL_WARNING: str = "warning"
LEVEL_INFO: str = "info"

LEVELS = (LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO)


@dataclass
class QualityIssue:
    """单条质量问题。

    ``entity_type`` / ``entity_id`` 可选，用来定位是哪条板块/公司/指标。
    ``raw_field_name`` 用于记录上游字段名，便于追踪上游字段变更。
    """

    code: str  # 例如 "sector_daily_too_short"
    level: str  # error | warning | info
    message: str = ""
    entity_type: Optional[str] = None  # sector | company | metric | snapshot
    entity_id: Optional[str] = None
    raw_field_name: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(
                f"unknown quality issue level: {self.level!r} (allowed: {LEVELS})"
            )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "code": self.code,
            "level": self.level,
            "message": self.message,
        }
        if self.entity_type is not None:
            out["entity_type"] = self.entity_type
        if self.entity_id is not None:
            out["entity_id"] = self.entity_id
        if self.raw_field_name is not None:
            out["raw_field_name"] = self.raw_field_name
        if self.details is not None:
            out["details"] = dict(self.details)
        return out


@dataclass
class QualityReport:
    """质量报告。

    与 snapshot 一一对应：每生成一个 ``MarketSnapshot`` 都应产出 ``QualityReport``，
    并把 ``status`` 写入 snapshot 的 ``data_quality_status``。
    """

    quality_report_id: str = field(default_factory=new_quality_report_id)
    fetch_run_id: Optional[str] = None
    analysis_date: Optional[str] = None
    issues: List[QualityIssue] = field(default_factory=list)
    stale: bool = False  # 由调用方判断行情/估值是否过旧

    def add(self, issue: QualityIssue) -> None:
        self.issues.append(issue)

    def add_issue(
        self,
        code: str,
        level: str,
        message: str = "",
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        raw_field_name: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> QualityIssue:
        issue = QualityIssue(
            code=code,
            level=level,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            raw_field_name=raw_field_name,
            details=details,
        )
        self.issues.append(issue)
        return issue

    @property
    def counts(self) -> Dict[str, int]:
        out = {level: 0 for level in LEVELS}
        for issue in self.issues:
            out[issue.level] += 1
        return out

    @property
    def status(self) -> str:
        counts = self.counts
        if counts[LEVEL_ERROR] > 0:
            return QUALITY_STATUS_INVALID
        if self.stale:
            return QUALITY_STATUS_STALE
        if counts[LEVEL_WARNING] > 0:
            return QUALITY_STATUS_DEGRADED
        return QUALITY_STATUS_OK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality_report_id": self.quality_report_id,
            "fetch_run_id": self.fetch_run_id,
            "analysis_date": self.analysis_date,
            "status": self.status,
            "counts": self.counts,
            "issues": [i.to_dict() for i in self.issues],
        }


# ---------------------------------------------------------------------------
# 质量检查规则（Phase 6D）
# ---------------------------------------------------------------------------

# 行情/估值数据过旧的阈值：latest trade_date 距 analysis_date 超过这么多天
# 即视为 stale。7 个自然日约等于一个完整的交易周 + 缓冲，避免周末误报。
STALE_THRESHOLD_DAYS: int = 7

# 板块日线最少需要的交易日数（docs §18: 板块日线至少覆盖 60 个交易日）。
MIN_SECTOR_DAILY_BARS: int = 60

# 板块成分股行情覆盖率阈值（docs §18: "行业板块成分覆盖率应达到配置阈值，
# 低于阈值时降级或阻断"）。覆盖率低于此值 → error（阻断）；高于阈值但有个别
# 缺失 → warning（降级）。
MIN_CONSTITUENT_QUOTE_COVERAGE: float = 0.5


def run_quality_checks(
    conn,
    analysis_date: str,
    classification_system: str,
    benchmark: str,
) -> QualityReport:
    """读取 SQLite 并产出结构化质量报告。

    检查规则按 docs §18 质量检查 + 严重级别表实现：
    - ``error``：无 benchmark、板块日线为 0、核心表不存在 → 阻断 MarketSnapshot。
    - ``warning``：板块日线 < 60、板块无成分股、部分公司缺财务/估值。
    - ``info``：使用最近缓存（trade_date 早于 analysis_date）。
    - ``stale``：最新行情距 analysis_date 超过阈值 → report.stale = True。
    """

    report = QualityReport(analysis_date=analysis_date)

    # ---- benchmark 必须有历史行情 ----
    bm_count = conn.execute(
        "SELECT COUNT(*) FROM benchmark_daily_bars "
        "WHERE benchmark = ? AND trade_date <= ?",
        (benchmark, analysis_date),
    ).fetchone()[0]
    if bm_count == 0:
        report.add_issue(
            "no_benchmark_history",
            LEVEL_ERROR,
            f"benchmark {benchmark!r} has no daily bars on or before {analysis_date}",
            entity_type="benchmark",
            entity_id=benchmark,
        )

    # ---- 板块列表 ----
    sector_rows = conn.execute(
        "SELECT sector_id, sector_name FROM sectors "
        "WHERE classification_system = ?",
        (classification_system,),
    ).fetchall()

    if not sector_rows:
        report.add_issue(
            "no_sectors",
            LEVEL_ERROR,
            f"no sectors found for classification_system={classification_system!r}",
            entity_type="snapshot",
        )
        return report

    for row in sector_rows:
        sector_id = row[0]
        sector_name = row[1]

        # 板块必须有 sector_name
        if not sector_name:
            report.add_issue(
                "sector_missing_name",
                LEVEL_WARNING,
                f"sector {sector_id!r} has no sector_name",
                entity_type="sector",
                entity_id=sector_id,
            )

        # 板块成分股
        const_count = conn.execute(
            "SELECT COUNT(*) FROM sector_constituents "
            "WHERE sector_id = ? AND classification_system = ? "
            "AND as_of_date <= ?",
            (sector_id, classification_system, analysis_date),
        ).fetchone()[0]
        if const_count == 0:
            report.add_issue(
                "sector_no_constituents",
                LEVEL_WARNING,
                f"sector {sector_id!r} has no constituents on or before {analysis_date}",
                entity_type="sector",
                entity_id=sector_id,
            )

        # 板块日线覆盖
        daily_count = conn.execute(
            "SELECT COUNT(*) FROM sector_daily_bars "
            "WHERE sector_id = ? AND classification_system = ? "
            "AND trade_date <= ?",
            (sector_id, classification_system, analysis_date),
        ).fetchone()[0]
        if daily_count == 0:
            report.add_issue(
                "sector_daily_empty",
                LEVEL_ERROR,
                f"sector {sector_id!r} has no daily bars on or before {analysis_date}",
                entity_type="sector",
                entity_id=sector_id,
            )
        elif daily_count < MIN_SECTOR_DAILY_BARS:
            report.add_issue(
                "sector_daily_too_short",
                LEVEL_ERROR,
                f"sector {sector_id!r} has only {daily_count} daily bars "
                f"(minimum {MIN_SECTOR_DAILY_BARS})",
                entity_type="sector",
                entity_id=sector_id,
                details={"count": daily_count, "minimum": MIN_SECTOR_DAILY_BARS},
            )

    # ---- 行情新鲜度（stale 检测）----
    # 检查板块日线、基准、公司行情、估值历史的新鲜度。
    # 使用 MIN(MAX(trade_date)) 避免一个实体新鲜掩盖另一个实体陈旧。
    # 公司级行情/估值/覆盖范围仅检查被选板块成分股，避免全表数据污染。
    from datetime import date as _date

    def _check_stale(label, latest_date_str):
        if latest_date_str:
            try:
                delta = (_date.fromisoformat(analysis_date) - _date.fromisoformat(latest_date_str)).days
                if delta > STALE_THRESHOLD_DAYS:
                    return delta, latest_date_str
            except ValueError:
                pass
        return None, None

    # sector daily bars (scoped by classification_system, per-sector min)
    row = conn.execute(
        "SELECT MIN(latest_date) FROM ("
        "  SELECT MAX(trade_date) AS latest_date FROM sector_daily_bars "
        "  WHERE classification_system = ? AND trade_date <= ? "
        "  GROUP BY sector_id"
        ")",
        (classification_system, analysis_date),
    ).fetchone()
    latest_sector_date = row[0] if row else None
    delta, ld = _check_stale("sector", latest_sector_date)
    if delta is not None:
        report.stale = True
        report.add_issue(
            "stale_sector_data",
            LEVEL_INFO,
            f"oldest sector latest trade_date is {ld}, {delta} days before analysis_date {analysis_date}",
            entity_type="snapshot",
            details={"latest_date": ld, "age_days": delta},
        )

    # benchmark daily bars
    row = conn.execute(
        "SELECT MAX(trade_date) FROM benchmark_daily_bars "
        "WHERE benchmark = ? AND trade_date <= ?",
        (benchmark, analysis_date),
    ).fetchone()
    latest_bm_date = row[0] if row else None
    delta, ld = _check_stale("benchmark", latest_bm_date)
    if delta is not None:
        report.stale = True
        report.add_issue(
            "stale_benchmark_data",
            LEVEL_INFO,
            f"latest benchmark trade_date is {ld}, {delta} days before analysis_date {analysis_date}",
            entity_type="snapshot",
            details={"latest_date": ld, "age_days": delta},
        )

    # ---- 板块成分股缺行情检测（docs §18: "个别板块成分缺行情" 为 warning）----
    total_constituents = conn.execute(
        "SELECT COUNT(DISTINCT sc.code) FROM sector_constituents sc "
        "WHERE sc.classification_system = ? "
        "AND sc.as_of_date = ("
        "  SELECT MAX(as_of_date) FROM sector_constituents "
        "  WHERE sector_id = sc.sector_id AND classification_system = ? "
        "  AND as_of_date <= ?"
        ")",
        (classification_system, classification_system, analysis_date),
    ).fetchone()[0]
    constituents_with_quotes = conn.execute(
        "SELECT COUNT(DISTINCT sc.code) FROM sector_constituents sc "
        "WHERE sc.classification_system = ? "
        "AND sc.as_of_date = ("
        "  SELECT MAX(as_of_date) FROM sector_constituents "
        "  WHERE sector_id = sc.sector_id AND classification_system = ? "
        "  AND as_of_date <= ?"
        ") "
        "AND EXISTS ("
        "  SELECT 1 FROM company_daily_snapshot c "
        "  WHERE c.code = sc.code AND c.trade_date <= ?"
        ")",
        (classification_system, classification_system, analysis_date, analysis_date),
    ).fetchone()[0]
    missing_quotes = total_constituents - constituents_with_quotes
    if missing_quotes > 0:
        coverage = constituents_with_quotes / total_constituents if total_constituents > 0 else 0.0
        if coverage < MIN_CONSTITUENT_QUOTE_COVERAGE:
            # 覆盖率低于阈值 → 阻断（docs §18: "低于阈值时...阻断"）
            report.add_issue(
                "low_constituent_quote_coverage",
                LEVEL_ERROR,
                f"constituent quote coverage: {constituents_with_quotes}/{total_constituents} "
                f"({coverage:.0%}) below threshold {MIN_CONSTITUENT_QUOTE_COVERAGE:.0%}",
                entity_type="snapshot",
                details={
                    "with_quotes": constituents_with_quotes,
                    "missing": missing_quotes,
                    "total": total_constituents,
                    "threshold": MIN_CONSTITUENT_QUOTE_COVERAGE,
                },
            )
        else:
            # 个别缺失 → warning（docs §18: "个别板块成分缺行情" 为 warning）
            report.add_issue(
                "missing_constituent_quotes",
                LEVEL_WARNING,
                f"{missing_quotes}/{total_constituents} sector constituents have no quote data "
                f"on or before {analysis_date}",
                entity_type="snapshot",
                details={"missing": missing_quotes, "total": total_constituents},
            )

    # company daily snapshot (quotes) — scoped to selected sector constituents
    row = conn.execute(
        "SELECT MIN(latest_date) FROM ("
        "  SELECT MAX(c.trade_date) AS latest_date FROM company_daily_snapshot c "
        "  WHERE c.trade_date <= ? "
        "  AND EXISTS ("
        "    SELECT 1 FROM sector_constituents sc "
        "    WHERE sc.code = c.code AND sc.classification_system = ? "
        "    AND sc.as_of_date = ("
        "      SELECT MAX(as_of_date) FROM sector_constituents "
        "      WHERE sector_id = sc.sector_id AND classification_system = ? "
        "      AND as_of_date <= ?"
        "    )"
        "  ) GROUP BY c.code"
        ")",
        (analysis_date, classification_system, classification_system, analysis_date),
    ).fetchone()
    latest_quote_date = row[0] if row else None
    delta, ld = _check_stale("quote", latest_quote_date)
    if delta is not None:
        report.stale = True
        report.add_issue(
            "stale_quote_data",
            LEVEL_INFO,
            f"oldest company quote latest trade_date is {ld}, {delta} days before analysis_date {analysis_date}",
            entity_type="snapshot",
            details={"latest_date": ld, "age_days": delta},
        )

    # company valuation history — scoped to selected sector constituents
    row = conn.execute(
        "SELECT MIN(latest_date) FROM ("
        "  SELECT MAX(v.trade_date) AS latest_date FROM company_valuation_history v "
        "  WHERE v.trade_date <= ? "
        "  AND EXISTS ("
        "    SELECT 1 FROM sector_constituents sc "
        "    WHERE sc.code = v.code AND sc.classification_system = ? "
        "    AND sc.as_of_date = ("
        "      SELECT MAX(as_of_date) FROM sector_constituents "
        "      WHERE sector_id = sc.sector_id AND classification_system = ? "
        "      AND as_of_date <= ?"
        "    )"
        "  ) GROUP BY v.code"
        ")",
        (analysis_date, classification_system, classification_system, analysis_date),
    ).fetchone()
    latest_val_date = row[0] if row else None
    delta, ld = _check_stale("valuation", latest_val_date)
    if delta is not None:
        report.stale = True
        report.add_issue(
            "stale_valuation_data",
            LEVEL_INFO,
            f"oldest company valuation latest trade_date is {ld}, {delta} days before analysis_date {analysis_date}",
            entity_type="snapshot",
            details={"latest_date": ld, "age_days": delta},
        )

    # ---- 财务指标覆盖率 ----
    # 以全部板块成分股为分母（docs §18: companies/financials/valuations 对齐）。
    company_count = conn.execute(
        "SELECT COUNT(DISTINCT sc.code) FROM sector_constituents sc "
        "WHERE sc.classification_system = ? "
        "AND sc.as_of_date = ("
        "  SELECT MAX(as_of_date) FROM sector_constituents "
        "  WHERE sector_id = sc.sector_id AND classification_system = ? "
        "  AND as_of_date <= ?"
        ")",
        (classification_system, classification_system, analysis_date),
    ).fetchone()[0]
    if company_count > 0:
        fin_count = conn.execute(
            "SELECT COUNT(DISTINCT f.code) FROM financial_metrics f "
            "WHERE f.disclosure_date <= ? AND f.as_of_date <= ? "
            "AND EXISTS ("
            "  SELECT 1 FROM sector_constituents sc "
            "  WHERE sc.code = f.code AND sc.classification_system = ? "
            "  AND sc.as_of_date = ("
            "    SELECT MAX(as_of_date) FROM sector_constituents "
            "    WHERE sector_id = sc.sector_id AND classification_system = ? "
            "    AND as_of_date <= ?"
            "  )"
            ")",
            (analysis_date, analysis_date,
             classification_system, classification_system, analysis_date),
        ).fetchone()[0]
        coverage = fin_count / company_count if company_count > 0 else 0.0
        if coverage < 0.5:
            report.add_issue(
                "low_financial_coverage",
                LEVEL_WARNING,
                f"financial metrics coverage: {fin_count}/{company_count} companies "
                f"({coverage:.0%})",
                entity_type="snapshot",
                details={"with_financials": fin_count, "total": company_count},
            )
        else:
            report.add_issue(
                "financial_coverage",
                LEVEL_INFO,
                f"financial metrics coverage: {fin_count}/{company_count} companies "
                f"({coverage:.0%})",
                entity_type="snapshot",
                details={"with_financials": fin_count, "total": company_count},
            )

    # ---- 估值覆盖率 ----
    # 以全部板块成分股为分母（docs §18: companies/financials/valuations 对齐）。
    # 估值的 point-in-time 过滤为 trade_date <= analysis_date（与 _load_valuations 一致）。
    # coverage numerator 必须按 repository 实际会选中的最新估值行判断；只有
    # pe/pb 关键字段可用时才算“可用估值”，避免空估值行把 snapshot 误判为 ok。
    if company_count > 0:
        # 单次查询同时取 usable_count (pe/pb 非空) 和 row_count (有估值行)，
        # 两者共用同一 ROW_NUMBER 子查询，避免逻辑漂移和冗余 round-trip。
        row = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN pe IS NOT NULL AND pb IS NOT NULL THEN 1 ELSE 0 END), "
            "  COUNT(*) "
            "FROM ("
            "  SELECT v.code, v.pe, v.pb, ROW_NUMBER() OVER ("
            "    PARTITION BY v.code ORDER BY v.trade_date DESC"
            "  ) AS rn "
            "  FROM company_valuation_history v "
            "  WHERE v.trade_date <= ? "
            "  AND EXISTS ("
            "    SELECT 1 FROM sector_constituents sc "
            "    WHERE sc.code = v.code AND sc.classification_system = ? "
            "    AND sc.as_of_date = ("
            "      SELECT MAX(as_of_date) FROM sector_constituents "
            "      WHERE sector_id = sc.sector_id AND classification_system = ? "
            "      AND as_of_date <= ?"
            "    )"
            "  )"
            ") WHERE rn = 1",
            (analysis_date,
             classification_system, classification_system, analysis_date),
        ).fetchone()
        val_count = row[0] or 0
        valuation_row_count = row[1] or 0
        coverage = val_count / company_count
        if coverage < 0.5:
            report.add_issue(
                "low_valuation_coverage",
                LEVEL_WARNING,
                f"valuation history coverage: {val_count}/{company_count} companies "
                f"({coverage:.0%})",
                entity_type="snapshot",
                details={
                    "with_usable_valuations": val_count,
                    "with_valuation_rows": valuation_row_count,
                    "total": company_count,
                },
            )
        else:
            report.add_issue(
                "valuation_coverage",
                LEVEL_INFO,
                f"valuation history coverage: {val_count}/{company_count} companies "
                f"({coverage:.0%})",
                entity_type="snapshot",
                details={
                    "with_usable_valuations": val_count,
                    "with_valuation_rows": valuation_row_count,
                    "total": company_count,
                },
            )

    # ---- 公司 code 对齐检测（docs §18: "公司 code 必须能在 companies、
    # financials、valuations 中对齐"）----
    # 覆盖率检查给出聚合比例，此处给出实体级明细，便于定位具体哪些成分股
    # 缺财务或缺估值。仅报告 INFO，不改变 status（已由覆盖率检查决定）。
    if company_count > 0:
        # 缺财务的成分股
        missing_fin = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT sc.code FROM sector_constituents sc "
                "WHERE sc.classification_system = ? "
                "AND sc.as_of_date = ("
                "  SELECT MAX(as_of_date) FROM sector_constituents "
                "  WHERE sector_id = sc.sector_id AND classification_system = ? "
                "  AND as_of_date <= ?"
                ") "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM financial_metrics f "
                "  WHERE f.code = sc.code AND f.disclosure_date <= ? "
                "  AND f.as_of_date <= ?"
                ")",
                (classification_system, classification_system,
                 analysis_date, analysis_date, analysis_date),
            ).fetchall()
        ]
        # 缺估值的成分股
        missing_val = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT sc.code FROM sector_constituents sc "
                "WHERE sc.classification_system = ? "
                "AND sc.as_of_date = ("
                "  SELECT MAX(as_of_date) FROM sector_constituents "
                "  WHERE sector_id = sc.sector_id AND classification_system = ? "
                "  AND as_of_date <= ?"
                ") "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM company_valuation_history v "
                "  WHERE v.code = sc.code AND v.trade_date <= ?"
                ")",
                (classification_system, classification_system,
                 analysis_date, analysis_date),
            ).fetchall()
        ]
        if missing_fin or missing_val:
            report.add_issue(
                "code_misalignment",
                LEVEL_INFO,
                f"{len(missing_fin)} constituents missing financials, "
                f"{len(missing_val)} missing valuations",
                entity_type="snapshot",
                details={
                    "missing_financials": missing_fin,
                    "missing_valuations": missing_val,
                },
            )

    return report


__all__ = [
    "LEVELS",
    "LEVEL_ERROR",
    "LEVEL_INFO",
    "LEVEL_WARNING",
    "MIN_CONSTITUENT_QUOTE_COVERAGE",
    "MIN_SECTOR_DAILY_BARS",
    "QualityIssue",
    "QualityReport",
    "STALE_THRESHOLD_DAYS",
    "run_quality_checks",
]
