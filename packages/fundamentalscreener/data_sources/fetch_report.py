"""Per-security fetch facts carried alongside row lists.

Data-source methods still return a list. ``ReportedRows`` keeps that contract
and attaches how many requested securities succeeded, were legitimately empty,
or failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CodeFetchReport:
    """Counts and failure reasons for one per-security fetch."""

    requested_count: int = 0
    succeeded_count: int = 0
    empty_count: int = 0
    failed_count: int = 0
    failures: Tuple[Dict[str, str], ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested_count": self.requested_count,
            "succeeded_count": self.succeeded_count,
            "empty_count": self.empty_count,
            "failed_count": self.failed_count,
            "failures": [dict(item) for item in self.failures],
        }


class ReportedRows(list):
    """Row list plus the per-security report for the sync layer."""

    def __init__(
        self,
        rows: Sequence[Dict[str, Any]] = (),
        report: Optional[CodeFetchReport] = None,
    ) -> None:
        super().__init__(rows)
        self.fetch_report = report


class CodeFetchTracker:
    """Accumulate per-code outcomes inside a company-layer fetch."""

    def __init__(self) -> None:
        self.requested_count = 0
        self.succeeded_count = 0
        self.empty_count = 0
        self.failures: List[Dict[str, str]] = []

    def start(self, code: str) -> bool:
        """Return False for a blank code, which is not a request."""

        if not code:
            return False
        self.requested_count += 1
        return True

    def record_success(self) -> None:
        self.succeeded_count += 1

    def record_empty(self) -> None:
        self.empty_count += 1

    def record_failure(self, code: str, exc: BaseException) -> None:
        self.failures.append(
            {"code": str(code), "error": f"{type(exc).__name__}: {exc}"}
        )

    def report(self) -> CodeFetchReport:
        return CodeFetchReport(
            requested_count=self.requested_count,
            succeeded_count=self.succeeded_count,
            empty_count=self.empty_count,
            failed_count=len(self.failures),
            failures=tuple(self.failures),
        )

    def finish(self, rows: List[Dict[str, Any]]) -> ReportedRows:
        return ReportedRows(rows, self.report())


__all__ = ["CodeFetchReport", "CodeFetchTracker", "ReportedRows"]
