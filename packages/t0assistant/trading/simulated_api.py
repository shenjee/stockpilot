"""Transport-neutral App v1 adapter for Replay Session trades."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from .models import TradeValidationError


class SimulatedTradeSessionPort(Protocol):
    session_id: str
    symbol: str
    trade_date: str

    @property
    def simulated_trades(self) -> tuple[Any, ...]: ...

    def create_simulated_trade(self, draft: Mapping[str, Any]) -> Any: ...
    def update_simulated_trade(self, trade_id: str, draft: Mapping[str, Any]) -> Any: ...
    def delete_simulated_trade(self, trade_id: str) -> bool: ...
    def publish_simulated_trades(self) -> None: ...


SessionResolver = Callable[[str], SimulatedTradeSessionPort | None]


class SimulatedTradeCommandApi:
    """Dispatch frozen trade commands to one active Replay Session.

    This adapter has no repository dependency. Session methods own validation,
    identity and ``trades_changed`` publication.
    """

    def __init__(self, resolver: SessionResolver) -> None:
        if not callable(resolver):
            raise TypeError("resolver must be callable")
        self._resolver = resolver

    def dispatch(self, command: str, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        session_id = request.get("session_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = "missing-request-id"
        if not isinstance(session_id, str) or not session_id:
            return self._reject(request_id, "invalid_trade_request", "Replay Session is required")
        session = self._resolver(session_id)
        if session is None:
            return self._reject(request_id, "session_not_found", "回放会话不存在")
        payload = request.get("payload")
        if not isinstance(payload, Mapping):
            return self._reject(request_id, "invalid_trade_request", "成交请求无效")
        try:
            if command == "list_trades":
                if (
                    payload.get("trade_scope") != "simulated"
                    or payload.get("symbol") != session.symbol
                    or payload.get("trade_date") != session.trade_date
                ):
                    raise TradeValidationError("trade_scope", "must match Replay Session")
                # Re-publish the complete Session fact, including an empty list.
                session.publish_simulated_trades()
            elif command == "create_trade":
                session.create_simulated_trade(payload.get("trade"))
            elif command == "update_trade":
                session.update_simulated_trade(
                    payload.get("trade_id"), payload.get("trade")
                )
            elif command == "delete_trade":
                if payload.get("trade_scope") != "simulated":
                    raise TradeValidationError("trade_scope", "must be simulated")
                session.delete_simulated_trade(payload.get("trade_id"))
            else:
                return self._reject(request_id, "invalid_trade_request", "成交命令无效")
        except (TradeValidationError, TypeError, ValueError) as error:
            return self._reject(request_id, "invalid_trade_request", str(error))
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": True,
            "operation_id": None,
            "data": {},
            "error": None,
        }

    @staticmethod
    def _reject(request_id: str, error_code: str, message: str) -> dict[str, Any]:
        return {
            "schema_version": "t0_app_v2",
            "request_id": request_id,
            "accepted": False,
            "operation_id": None,
            "data": None,
            "error": {
                "error_code": error_code,
                "category": "session" if error_code == "session_not_found" else "validation",
                "severity": "error",
                "retryable": False,
                "affected_capability": "trades",
                "message": message,
                "request_id": request_id,
                "details": {},
            },
        }


__all__ = ["SimulatedTradeCommandApi", "SimulatedTradeSessionPort"]
