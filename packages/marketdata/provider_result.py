from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, List, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderIssue:
    level: str
    reason_code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    exception_type: str = ""


@dataclass(frozen=True)
class MarketDataResult(Generic[T]):
    success: bool
    data: T
    issues: List[ProviderIssue] = field(default_factory=list)

    def errors(self) -> List[ProviderIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    def warnings(self) -> List[ProviderIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    def first_error_code(self) -> Optional[str]:
        errors = self.errors()
        if not errors:
            return None
        return errors[0].reason_code
