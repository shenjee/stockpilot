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
