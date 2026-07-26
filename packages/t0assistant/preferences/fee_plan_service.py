"""Application service for structured fee plans.

The service keeps fee-calculation rules out of the repository and UI layers.
It delegates persistence to a repository and only seeds the default
"申万宏源（示例）" plan once per database, so user edits to the default plan
are never overwritten on restart and deleting the default plan is permanent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from .service import PreferenceCapability

if TYPE_CHECKING:
    from packages.t0assistant.repositories import FeePlanRecord


class _FeePlanRepository(Protocol):
    """Narrow port used by FeePlanService."""

    @property
    def capability(self) -> PreferenceCapability: ...

    def initialize_default_plan(
        self, plan: FeePlanRecord
    ) -> "FeePlanRecord | None": ...

    def list_all(self) -> "tuple[FeePlanRecord, ...]": ...

    def get(self, fee_plan_id: str) -> "FeePlanRecord | None": ...

    def create(self, plan: FeePlanRecord) -> FeePlanRecord: ...

    def update(self, plan: FeePlanRecord) -> FeePlanRecord: ...

    def delete(self, fee_plan_id: str) -> bool: ...


class FeePlanNotFoundError(ValueError):
    """The requested fee plan does not exist."""


class FeePlanService:
    """Application service for listing, creating, updating and deleting fee plans.

    Persistence failures are raised as repository exceptions, so callers can
    never observe a "memory success" that was not confirmed on disk.
    """

    DEFAULT_PLAN_ID = "shenwan-hongyuan"

    def __init__(
        self,
        repository: _FeePlanRepository,
        *,
        seed_defaults: bool = True,
    ) -> None:
        self._repository = repository
        if seed_defaults:
            self.seed_default_plan()

    @property
    def capability(self) -> PreferenceCapability:
        return self._repository.capability

    def seed_default_plan(self) -> FeePlanRecord | None:
        """Idempotently seed the default plan exactly once per database.

        The repository writes the plan row and the initialization flag in a
        single transaction, so a crash or write failure between the two cannot
        leave the database in a half-initialized state. Deleting the default
        plan later will not cause it to resurrect.
        """

        return self._repository.initialize_default_plan(_default_plan())

    def list_plans(self) -> tuple[FeePlanRecord, ...]:
        return self._repository.list_all()

    def get_plan(self, fee_plan_id: str) -> FeePlanRecord:
        plan = self._repository.get(fee_plan_id)
        if plan is None:
            raise FeePlanNotFoundError(f"收费方案不存在：{fee_plan_id}")
        return plan

    def create_plan(self, plan: FeePlanRecord) -> FeePlanRecord:
        return self._repository.create(plan)

    def update_plan(self, plan: FeePlanRecord) -> FeePlanRecord:
        return self._repository.update(plan)

    def delete_plan(self, fee_plan_id: str) -> bool:
        return self._repository.delete(fee_plan_id)


def _default_plan() -> FeePlanRecord:
    from packages.t0assistant.repositories import FeePlanRecord

    return FeePlanRecord(
        fee_plan_id=FeePlanService.DEFAULT_PLAN_ID,
        name="申万宏源（示例）",
        a_share_commission_rate=Decimal("0.0003"),
        a_share_min_commission=Decimal("5"),
        etf_commission_rate=Decimal("0.0002"),
        etf_min_commission=Decimal("5"),
        stamp_duty_rate=Decimal("0.0005"),
        stamp_duty_sell_only=True,
        transfer_fee_rate=Decimal("0.00001"),
        transfer_fee_side="both",
        transfer_fee_enabled=True,
    )
