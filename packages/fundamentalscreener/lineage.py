"""Snapshot 与采集血缘元数据（Phase 6A）。

集中定义 ``fetch_run_id``、``snapshot_id``、``quality_report_id``、``source_set``、
``config_version``、``formula_version``、``generated_at`` 的生成与传递位置。
真实数据接入（Phase 6B+）和 repository 层（Phase 6D）都必须从这里获取血缘 ID，
不允许自行拼字符串。

设计原则：
- ID 必须可读、唯一、不会跨进程冲突。`fetch_run_id` 使用 `fetch-<UTC date>-<8 hex>`，
  `snapshot_id` / `quality_report_id` 同构，便于人读 + grep 日志。
- ``SnapshotMetadata`` 是 CLI/JSON 顶层 ``snapshot`` 对象（docs §7）的承载结构，
  ``to_dict()`` 保证字段顺序稳定。
- ``SourceSet`` 是 ``{role: source_name}`` 的薄包装：role 例如 ``sector`` / ``quote`` /
  ``financial`` / ``valuation``，source_name 例如 ``akshare_em`` / ``tencent``。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Optional

# config / formula 版本号在 Phase 6 第一版冻结。后续若改算法或权重，必须递增并
# 在 CHANGELOG 里记录，便于 snapshot 重放。
DEFAULT_CONFIG_VERSION: str = "fundamental-screener-config-v1"
DEFAULT_FORMULA_VERSION: str = "fundamental-screener-formula-v1"

# Phase 6 默认时区，所有 ID 时间戳与 generated_at 统一使用 +08:00。
_CN_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------


def now_cn() -> datetime:
    """当前 +08:00 时间，精度到秒。"""

    return datetime.now(_CN_TZ).replace(microsecond=0)


def now_cn_isoformat() -> str:
    """``generated_at`` 用的 ISO 8601 字符串。"""

    return now_cn().isoformat()


def _date_token(reference: Optional[datetime] = None) -> str:
    ts = reference or now_cn()
    return ts.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------


def _new_token(suffix_bytes: int = 4) -> str:
    return secrets.token_hex(suffix_bytes)


def new_fetch_run_id(reference: Optional[datetime] = None) -> str:
    """生成一次同步任务的唯一 ID。

    格式 ``fetch-YYYYMMDD-<hex>``。reference 仅用于注入固定时间，方便测试。
    """

    return f"fetch-{_date_token(reference)}-{_new_token()}"


def new_snapshot_id(reference: Optional[datetime] = None) -> str:
    return f"snapshot-{_date_token(reference)}-{_new_token()}"


def new_quality_report_id(reference: Optional[datetime] = None) -> str:
    return f"quality-{_date_token(reference)}-{_new_token()}"


# ---------------------------------------------------------------------------
# Source set
# ---------------------------------------------------------------------------


@dataclass
class SourceSet:
    """``{role: source_name}`` 薄包装，使其能在 JSON 中稳定序列化。"""

    mapping: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SourceSet":
        if not raw:
            return cls()
        return cls(mapping={str(k): str(v) for k, v in raw.items()})

    def with_role(self, role: str, source: str) -> "SourceSet":
        """返回新的 ``SourceSet``，不就地修改，方便链式构造。"""

        merged = dict(self.mapping)
        merged[str(role)] = str(source)
        return SourceSet(mapping=merged)

    def update(self, role: str, source: str) -> None:
        self.mapping[str(role)] = str(source)

    def to_dict(self) -> Dict[str, str]:
        return dict(sorted(self.mapping.items()))


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------


@dataclass
class SnapshotMetadata:
    """CLI / JSON / Streamlit / skill 透传的快照血缘信息。

    与 docs §7 顶层 ``snapshot`` 对象一一对应。Phase 6A 仅定义结构和默认值，
    Phase 6D 在 ``MarketSnapshot`` 组装时实际填充。
    """

    snapshot_id: str = ""
    analysis_date: str = ""
    data_cutoff: str = ""
    data_quality_status: str = "ok"
    source_set: SourceSet = field(default_factory=SourceSet)
    fetch_run_id: str = ""
    quality_report_id: str = ""
    config_version: str = DEFAULT_CONFIG_VERSION
    formula_version: str = DEFAULT_FORMULA_VERSION
    generated_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        analysis_date: str,
        data_cutoff: Optional[str] = None,
        source_set: Optional[Iterable[tuple]] | SourceSet | Dict[str, str] = None,
        fetch_run_id: Optional[str] = None,
        quality_report_id: Optional[str] = None,
        data_quality_status: str = "ok",
        config_version: str = DEFAULT_CONFIG_VERSION,
        formula_version: str = DEFAULT_FORMULA_VERSION,
        reference: Optional[datetime] = None,
    ) -> "SnapshotMetadata":
        """工厂方法：在 repository 组装 ``MarketSnapshot`` 时调用。

        - 没传 ``data_cutoff`` 时默认与 ``analysis_date`` 一致。
        - 没传 ``fetch_run_id`` / ``quality_report_id`` 时自动生成新 ID。
        - ``source_set`` 接受 ``SourceSet`` / dict / iterable of pairs。
        """

        if isinstance(source_set, SourceSet):
            ss = SourceSet(mapping=dict(source_set.mapping))
        elif isinstance(source_set, dict):
            ss = SourceSet.from_dict(source_set)
        elif source_set is None:
            ss = SourceSet()
        else:
            ss = SourceSet(mapping={str(k): str(v) for k, v in source_set})

        return cls(
            snapshot_id=new_snapshot_id(reference),
            analysis_date=analysis_date,
            data_cutoff=data_cutoff or analysis_date,
            data_quality_status=data_quality_status,
            source_set=ss,
            fetch_run_id=fetch_run_id or new_fetch_run_id(reference),
            quality_report_id=quality_report_id or new_quality_report_id(reference),
            config_version=config_version,
            formula_version=formula_version,
            generated_at=now_cn_isoformat() if reference is None else reference.isoformat(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "analysis_date": self.analysis_date,
            "data_cutoff": self.data_cutoff,
            "data_quality_status": self.data_quality_status,
            "source_set": self.source_set.to_dict(),
            "fetch_run_id": self.fetch_run_id,
            "quality_report_id": self.quality_report_id,
            "config_version": self.config_version,
            "formula_version": self.formula_version,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# 质量状态枚举
# ---------------------------------------------------------------------------


# data_quality_status 在 CLI/JSON 顶层 snapshot 中使用。Phase 6A 只声明常量，
# Phase 6D 的质量检查会真正决定该状态。
QUALITY_STATUS_OK: str = "ok"
QUALITY_STATUS_DEGRADED: str = "degraded"
QUALITY_STATUS_STALE: str = "stale"
QUALITY_STATUS_INVALID: str = "invalid"

QUALITY_STATUSES = (
    QUALITY_STATUS_OK,
    QUALITY_STATUS_DEGRADED,
    QUALITY_STATUS_STALE,
    QUALITY_STATUS_INVALID,
)


__all__ = [
    "DEFAULT_CONFIG_VERSION",
    "DEFAULT_FORMULA_VERSION",
    "QUALITY_STATUSES",
    "QUALITY_STATUS_DEGRADED",
    "QUALITY_STATUS_INVALID",
    "QUALITY_STATUS_OK",
    "QUALITY_STATUS_STALE",
    "SnapshotMetadata",
    "SourceSet",
    "new_fetch_run_id",
    "new_quality_report_id",
    "new_snapshot_id",
    "now_cn",
    "now_cn_isoformat",
]
