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
        if counts[LEVEL_WARNING] > 0:
            return QUALITY_STATUS_DEGRADED
        if self.stale:
            return QUALITY_STATUS_STALE
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


__all__ = [
    "LEVELS",
    "LEVEL_ERROR",
    "LEVEL_INFO",
    "LEVEL_WARNING",
    "QualityIssue",
    "QualityReport",
]
