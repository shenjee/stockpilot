"""Structured errors for market review writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class MarketReviewError(Exception):
    code: str
    message: str
    problem_codes: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return self.message


class InvalidTradeDateError(MarketReviewError):
    def __init__(self, message: str, *, suggested_date: str | None = None) -> None:
        super().__init__(
            code="INVALID_TRADE_DATE",
            message=message,
            problem_codes=(suggested_date,) if suggested_date else (),
        )


class ForeignKeysUnavailableError(MarketReviewError):
    def __init__(self) -> None:
        super().__init__(
            code="FOREIGN_KEYS_UNAVAILABLE",
            message=(
                "无法在 SQLite 连接上启用外键约束。"
                "若连接已在事务中，请在 BEGIN 之前调用 configure_connection()。"
            ),
        )


class InvalidFieldValueError(MarketReviewError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="INVALID_FIELD_VALUE",
            message=f"字段值不合法，本次未写入任何数据：{detail}",
        )


class LadderStNotAllowedError(MarketReviewError):
    def __init__(self, problem_codes: Sequence[str]) -> None:
        codes = tuple(sorted({f"{market}.{code}" for market, code in problem_codes}))
        super().__init__(
            code="LADDER_ST_NOT_ALLOWED",
            message=(
                "连板名单包含 ST 股票，本次未写入任何数据。"
                f"请移除后重试：{', '.join(codes)}"
            ),
            problem_codes=codes,
        )


class LadderSnapshotRequiredError(MarketReviewError):
    def __init__(self) -> None:
        super().__init__(
            code="LADDER_SNAPSHOT_REQUIRED",
            message="当前连板快照未完成，请先提交完整快照（ladder_status=complete）。",
        )


class LadderInvalidTransitionError(MarketReviewError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            code="LADDER_INVALID_TRANSITION",
            message=f"连板写入请求不合法，本次未写入任何数据：{detail}",
        )
