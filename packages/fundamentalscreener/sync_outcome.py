"""Classify one sync task, and a whole run, from fetch and write facts."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .data_sources.fetch_report import CodeFetchReport

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def classify_completed_task(
    *,
    report: Optional[CodeFetchReport],
    written: int,
    rejections: int,
) -> str:
    """Return success, partial, or failed after rows have been persisted.

    A per-security task with no request, or with only legitimate empty
    results, is success. Batch tasks keep the same rule: zero written rows
    are success unless every fetched row was rejected.
    """

    if report is not None and report.failed_count > 0:
        kept_any = (
            report.succeeded_count > 0
            or report.empty_count > 0
            or written > 0
            or report.failed_count < report.requested_count
        )
        return STATUS_PARTIAL if kept_any else STATUS_FAILED
    if written == 0 and rejections > 0:
        return STATUS_FAILED
    if rejections > 0:
        return STATUS_PARTIAL
    return STATUS_SUCCESS


def aggregate_status(tasks: Sequence[Mapping[str, Any]]) -> str:
    """Fold task statuses into one run status."""

    statuses = [_task_status(task) for task in tasks]
    if not statuses or all(status == STATUS_SUCCESS for status in statuses):
        return STATUS_SUCCESS
    if all(status == STATUS_FAILED for status in statuses):
        return STATUS_FAILED
    return STATUS_PARTIAL


def sum_count(tasks: Sequence[Mapping[str, Any]], key: str) -> int:
    """Sum integer per-security counts. Batch tasks contribute nothing."""

    total = 0
    for task in tasks:
        value = task.get(key)
        if isinstance(value, int):
            total += value
    return total


def _task_status(task: Mapping[str, Any]) -> str:
    status = task.get("status")
    if status in (STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED):
        return str(status)
    return STATUS_SUCCESS if task.get("success") else STATUS_FAILED


__all__ = [
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_SUCCESS",
    "aggregate_status",
    "classify_completed_task",
    "sum_count",
]
