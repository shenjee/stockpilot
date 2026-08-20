"""Synchronous App-v1 command boundary for persistent fee plans and fee advice."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from typing import Any

from packages.t0assistant.repositories import FeePlanRecord
from packages.t0assistant.trading import calculate_fee

from .fee_plan_service import FeePlanNotFoundError, FeePlanService


class FeePlanCommandApi:
    """Expose FeePlanService without leaking repository or Decimal objects."""

    def __init__(self, service: FeePlanService, *, service_generation: int) -> None:
        self._service = service
        self.service_generation = service_generation

    def dispatch(self, command: str, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        request_id_valid = isinstance(request_id, str) and bool(request_id)
        request_id = (
            request_id
            if request_id_valid
            else "missing-request-id"
        )
        try:
            if not request_id_valid:
                raise ValueError("request_id must be a non-empty string")
            payload = request.get("payload")
            if not isinstance(payload, dict):
                raise TypeError("payload must be an object")
            if command == "list_fee_plans":
                data = {
                    "fee_plans": [
                        self._wire(plan) for plan in self._service.list_plans()
                    ]
                }
            elif command == "create_fee_plan":
                plan = self._service.create_plan(self._record(payload["fee_plan"]))
                data = {"fee_plan": self._wire(plan)}
            elif command == "update_fee_plan":
                plan = self._service.update_plan(self._record(payload["fee_plan"]))
                data = {"fee_plan": self._wire(plan)}
            elif command == "delete_fee_plan":
                if not self._service.delete_plan(payload["fee_plan_id"]):
                    raise FeePlanNotFoundError(
                        f"收费方案不存在：{payload['fee_plan_id']}"
                    )
                data = {"deleted": True}
            elif command == "calculate_trade_fee":
                plan = self._service.get_plan(payload["fee_plan_id"])
                data = calculate_fee(
                    security_type=payload["security_type"],
                    side=payload["side"],
                    price=payload["price"],
                    quantity=payload["quantity"],
                    plan=plan,
                ).to_dict()
            else:
                raise ValueError(f"未知收费方案命令：{command}")
        except Exception as error:
            return self._rejected(request_id, error)
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": data,
            "error": None,
        }

    @staticmethod
    def _record(payload: dict[str, Any]) -> FeePlanRecord:
        return FeePlanRecord(**payload)

    @staticmethod
    def _wire(plan: FeePlanRecord) -> dict[str, Any]:
        values = asdict(plan)
        for key, value in tuple(values.items()):
            if isinstance(value, Decimal):
                values[key] = str(value)
            elif isinstance(value, Enum):
                values[key] = value.value
        return values

    @staticmethod
    def _rejected(request_id: str, error: Exception) -> dict[str, Any]:
        from packages.t0assistant.repositories import (
            RepositoryConflictError,
            RepositoryNotFoundError,
            RepositoryPersistenceError,
            RepositoryReadOnlyError,
        )

        if isinstance(error, (FeePlanNotFoundError, RepositoryNotFoundError)):
            code, category, retryable = "fee_plan_not_found", "data", False
        elif isinstance(error, RepositoryConflictError):
            code, category, retryable = "fee_plan_conflict", "data", False
        elif isinstance(error, RepositoryReadOnlyError):
            code, category, retryable = "repository_read_only", "persistence", False
        elif isinstance(error, RepositoryPersistenceError):
            code, category, retryable = "fee_plan_persist_failed", "persistence", True
        elif isinstance(error, (ValueError, TypeError)):
            code, category, retryable = (
                "invalid_fee_plan_request",
                "validation",
                False,
            )
        else:
            code, category, retryable = "fee_plan_service_unavailable", "service", True
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": False,
            "operation_id": None,
            "data": None,
            "error": {
                "error_code": code,
                "category": category,
                "severity": "error",
                "retryable": retryable,
                "affected_capability": "preferences",
                "message": str(error) or "收费方案操作未完成",
                "request_id": request_id,
                "details": {},
            },
        }


__all__ = ["FeePlanCommandApi"]
