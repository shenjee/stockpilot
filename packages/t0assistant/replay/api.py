"""Transport-independent Replay v1.0 command facade.

The facade owns protocol validation, stable error mapping, and the boundary
between synchronous rejection and asynchronous operation failure. Replay
loading, playback, stepping, seeking, and snapshot construction remain behind
``ReplayCommandPort`` so the API can be integrated before the engine exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationStatus,
)
from packages.t0assistant.runtime.replay_data import (
    ReplayDataInvalidError,
    ReplayDataTimeoutError,
    ReplayDataUnavailableError,
)

from .validation import validate_replay_snapshot


logger = logging.getLogger(__name__)
REPLAY_SCHEMA_VERSION = "t0_replay_v2"
REPLAY_COMMANDS = frozenset(
    {
        "select_symbol",
        "begin_replay",
        "set_replay_playback",
        "set_replay_speed",
        "step_replay",
        "seek_replay",
        "end_replay",
        "get_replay_snapshot",
    }
)
_OPERATION_COMMANDS = frozenset({"begin_replay", "step_replay", "seek_replay"})


class ReplayDeliveryChannel(str, Enum):
    """Default channel on which a Replay error is delivered."""

    SYNCHRONOUS = "synchronous_rejection"
    ASYNCHRONOUS = "operation_failed"


@dataclass(frozen=True, slots=True)
class _ErrorDefinition:
    category: str
    affected_capability: str
    retryable: bool
    default_channel: ReplayDeliveryChannel
    message: str


_ERRORS: dict[str, _ErrorDefinition] = {
    "invalid_request": _ErrorDefinition(
        "validation", "replay", False, ReplayDeliveryChannel.SYNCHRONOUS, "回放请求无效"
    ),
    "symbol_not_found": _ErrorDefinition(
        "data", "symbol_selection", True, ReplayDeliveryChannel.SYNCHRONOUS, "未找到证券"
    ),
    "invalid_trade_date": _ErrorDefinition(
        "validation", "replay", False, ReplayDeliveryChannel.SYNCHRONOUS, "回放日期无效"
    ),
    "replay_price_data_unavailable": _ErrorDefinition(
        "data",
        "replay",
        True,
        ReplayDeliveryChannel.ASYNCHRONOUS,
        "目标日没有可用的价格 K 线，无法开始回放",
    ),
    "replay_data_invalid": _ErrorDefinition(
        "data",
        "replay",
        True,
        ReplayDeliveryChannel.ASYNCHRONOUS,
        "回放价格数据非法，无法开始回放",
    ),
    "session_not_found": _ErrorDefinition(
        "session", "replay", False, ReplayDeliveryChannel.SYNCHRONOUS, "回放会话不存在"
    ),
    "session_retired": _ErrorDefinition(
        "session", "replay", False, ReplayDeliveryChannel.SYNCHRONOUS, "回放会话已结束"
    ),
    "invalid_replay_state": _ErrorDefinition(
        "session", "replay", True, ReplayDeliveryChannel.SYNCHRONOUS, "当前回放状态不允许此操作"
    ),
    "operation_superseded": _ErrorDefinition(
        "session", "replay", False, ReplayDeliveryChannel.ASYNCHRONOUS, "回放操作已被新的定位操作取代"
    ),
    "replay_busy": _ErrorDefinition(
        "session", "replay", True, ReplayDeliveryChannel.SYNCHRONOUS, "回放正在处理其他游标操作"
    ),
    "calculation_failed": _ErrorDefinition(
        "calculation", "five_minute_chart", True, ReplayDeliveryChannel.ASYNCHRONOUS, "回放计算失败"
    ),
    "service_unavailable": _ErrorDefinition(
        "service", "service", True, ReplayDeliveryChannel.SYNCHRONOUS, "本地服务尚未就绪"
    ),
}

DEFAULT_ERROR_DELIVERY = MappingProxyType(
    {code: definition.default_channel for code, definition in _ERRORS.items()}
)

_HTTP_STATUS_BY_ERROR = {
    "invalid_request": 400,
    "invalid_trade_date": 400,
    "symbol_not_found": 404,
    "replay_price_data_unavailable": 503,
    "replay_data_invalid": 422,
    "session_not_found": 404,
    "session_retired": 409,
    "invalid_replay_state": 409,
    "operation_superseded": 409,
    "replay_busy": 409,
    "calculation_failed": 500,
    "service_unavailable": 503,
}


@dataclass(frozen=True, slots=True)
class ReplayAccepted:
    """Prepared successful result returned by a Replay command port.

    A port must not start background work inside ``execute``. Commands that
    create an operation return ``start_operation``; the transport calls it only
    after the synchronous acceptance response has been delivered. The starter
    must only schedule work and must not wait for a worker that calls back into
    this API. ``end_replay`` returns ``commit`` so API operation retirement and
    engine retirement share one lock boundary. The commit callback must be a
    short state transition with the same non-blocking constraint.
    """

    session_id: str | None = None
    operation_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    start_operation: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )
    commit: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )


class ReplayCommandPort(Protocol):
    """Engine-facing command boundary used by :class:`ReplayCommandApi`."""

    def execute(
        self, command: str, request: Mapping[str, Any]
    ) -> ReplayAccepted: ...


class ReplayApiError(RuntimeError):
    """Stable domain rejection raised by a Replay command port."""

    def __init__(
        self,
        error_code: str,
        *,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        affected_capability: str | None = None,
    ) -> None:
        if error_code not in _ERRORS:
            raise ValueError(f"unknown Replay error code: {error_code}")
        self.error_code = error_code
        self.user_message = message
        self.details = dict(details or {})
        self.affected_capability = affected_capability
        super().__init__(message or error_code)


def map_computation_outcome_to_replay_error(
    outcome: ComputationOutcome,
) -> tuple[ReplayApiError | None, ReplayDeliveryChannel | None]:
    """Map a runtime computation outcome onto a stable Replay error contract.

    Returns ``(None, None)`` when the outcome must be silently dropped instead
    of being exposed to the transport layer, for example when the caller
    explicitly cancelled the task or the owning Session/generation has already
    retired.
    """

    if not isinstance(outcome, ComputationOutcome):
        raise TypeError("outcome must be a ComputationOutcome")
    if outcome.status is ComputationStatus.COMPLETED:
        return None, None
    if outcome.status is ComputationStatus.FAILED:
        return (
            ReplayApiError("calculation_failed"),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    if outcome.status is not ComputationStatus.CANCELLED:
        raise ValueError(f"unsupported computation status: {outcome.status}")

    if outcome.cancel_reason in {
        CancelReason.CANCELLED,
        CancelReason.SESSION_INVALID,
    }:
        return None, None
    if outcome.cancel_reason is CancelReason.SUPERSEDED:
        return (
            ReplayApiError("operation_superseded"),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    if outcome.cancel_reason is CancelReason.DEADLINE_EXCEEDED:
        return (
            ReplayApiError(
                "calculation_failed",
                details={"cancel_reason": outcome.cancel_reason.value},
            ),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    if outcome.cancel_reason is CancelReason.EXECUTOR_CLOSED:
        return (
            ReplayApiError("service_unavailable"),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    raise ValueError(f"unsupported cancel_reason: {outcome.cancel_reason}")


def map_replay_prepare_error_to_replay_error(
    error: Exception,
) -> tuple[ReplayApiError, ReplayDeliveryChannel]:
    """Map Replay preparation failures onto stable Replay errors.

    Quantity gaps are never preparation failures. Invalid OHLC facts map to
    ``replay_data_invalid``. Missing coverage, timeouts, and other unknown
    preparation failures map to ``replay_price_data_unavailable``. Only a real
    Python process / local-transport outage may use ``service_unavailable``.
    """

    if isinstance(error, ReplayDataInvalidError):
        return (
            ReplayApiError(
                "replay_data_invalid",
                details=error.details,
            ),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    if isinstance(error, (ReplayDataUnavailableError, ReplayDataTimeoutError)):
        return (
            ReplayApiError("replay_price_data_unavailable"),
            ReplayDeliveryChannel.ASYNCHRONOUS,
        )
    if isinstance(error, ReplayApiError):
        return error, DEFAULT_ERROR_DELIVERY[error.error_code]
    logger.error(
        "replay preparation failed with unexpected error",
        exc_info=error,
    )
    return (
        ReplayApiError(
            "replay_price_data_unavailable",
            details={"prepare_failed": True},
        ),
        ReplayDeliveryChannel.ASYNCHRONOUS,
    )


@dataclass(slots=True)
class ReplayHttpResult:
    """Transport-neutral result with a post-delivery operation hook."""

    status: int
    payload: dict[str, Any]
    _after_response: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )
    _delivery_lock: RLock = field(
        default_factory=RLock, init=False, repr=False, compare=False
    )
    _response_delivered: bool = field(
        default=False, init=False, repr=False, compare=False
    )

    def response_delivered(self) -> bool:
        """Start prepared work once, after the acceptance response is sent."""

        with self._delivery_lock:
            if self._response_delivered:
                return False
            self._response_delivered = True
            callback = self._after_response
            self._after_response = None
        if callback is not None:
            callback()
        return True


EventPublisher = Callable[[dict[str, Any]], None]


class ReplayCommandApi:
    """Validate and dispatch all frozen Replay v1.0 commands."""

    def __init__(
        self,
        port: ReplayCommandPort,
        *,
        service_generation: int,
        publish_event: EventPublisher | None = None,
    ) -> None:
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        self._port = port
        self._service_generation = service_generation
        self._publish_event = publish_event or (lambda _event: None)
        self._lock = RLock()
        self._operations: dict[str, tuple[str, str]] = {}
        self._active_operations: set[str] = set()

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def close(self) -> None:
        close = getattr(self._port, "close", None)
        if callable(close):
            close()

    def dispatch(
        self, command: str, raw_request: Mapping[str, Any]
    ) -> ReplayHttpResult:
        request_id = _request_id_for_error(raw_request)
        try:
            request = _validate_request(command, raw_request)
        except ReplayApiError as error:
            return ReplayHttpResult(
                _HTTP_STATUS_BY_ERROR.get(error.error_code, 400),
                self.error_payload(error, request_id=request_id),
            )
        except (TypeError, ValueError):
            error = ReplayApiError("invalid_request")
            return ReplayHttpResult(
                400, self.error_payload(error, request_id=request_id)
            )

        try:
            accepted = self._port.execute(command, request)
            self._validate_accepted(command, request, accepted)
        except ReplayApiError as error:
            return ReplayHttpResult(
                _HTTP_STATUS_BY_ERROR.get(error.error_code, 400),
                self.error_payload(error, request_id=request_id),
            )
        except Exception:
            error = ReplayApiError("service_unavailable")
            return ReplayHttpResult(
                503, self.error_payload(error, request_id=request_id)
            )

        try:
            with self._lock:
                if accepted.operation_id is not None:
                    if accepted.operation_id in self._operations:
                        raise RuntimeError("operation_id was reused")
                    self._operations[accepted.operation_id] = (
                        request["request_id"],
                        accepted.session_id or request.get("session_id", ""),
                    )
                if command == "end_replay":
                    assert accepted.commit is not None
                    accepted.commit()
                    self._retire_session_unlocked(request["session_id"])
        except Exception:
            error = ReplayApiError("service_unavailable")
            return ReplayHttpResult(
                503, self.error_payload(error, request_id=request_id)
            )

        payload: dict[str, Any] = {
            "request_id": request["request_id"],
            "service_generation": self._service_generation,
        }
        if accepted.session_id is not None:
            payload["session_id"] = accepted.session_id
        if accepted.operation_id is not None:
            payload["operation_id"] = accepted.operation_id
        payload.update(dict(accepted.data))
        after_response = None
        if accepted.operation_id is not None:
            assert accepted.start_operation is not None
            after_response = lambda: self._start_operation(
                accepted.operation_id,
                accepted.start_operation,
            )
        return ReplayHttpResult(200, payload, after_response)

    def _start_operation(
        self,
        operation_id: str,
        starter: Callable[[], None],
    ) -> None:
        with self._lock:
            if operation_id not in self._operations:
                return
            self._active_operations.add(operation_id)
            try:
                starter()
            except Exception:
                self._active_operations.discard(operation_id)
                self._operations.pop(operation_id, None)
                raise

    def deliver_operation_failure(
        self,
        *,
        operation_id: str,
        session_id: str,
        revision: int,
        error: ReplayApiError,
    ) -> bool:
        """Publish one failure for a previously accepted background operation.

        Returns ``False`` for an unknown, mismatched, or already completed
        operation. Removing the operation before publication prevents duplicate
        delivery even when a late worker reports the same failure twice.
        """

        if not _nonempty(operation_id) or not _nonempty(session_id):
            raise ValueError("operation_id and session_id must be non-empty")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        with self._lock:
            identity = self._operations.get(operation_id)
            if (
                identity is None
                or identity[1] != session_id
                or operation_id not in self._active_operations
            ):
                return False
            del self._operations[operation_id]
            self._active_operations.discard(operation_id)
        request_id, _ = identity
        payload = self.error_payload(
            error,
            request_id=request_id,
            operation_id=operation_id,
        )
        self._publish_event(
            {
                "schema_version": REPLAY_SCHEMA_VERSION,
                "service_generation": self._service_generation,
                "session_id": session_id,
                "revision": revision,
                "event_type": "operation_failed",
                "operation_id": operation_id,
                "payload": payload,
            }
        )
        return True

    def complete_operation(self, operation_id: str) -> bool:
        """Close a successful operation so late failures cannot be delivered."""

        with self._lock:
            if operation_id not in self._active_operations:
                return False
            self._active_operations.discard(operation_id)
            return self._operations.pop(operation_id, None) is not None

    def retire_session(self, session_id: str) -> int:
        """Forget all accepted operations belonging to a retired Session.

        Replay engines may call this at their exact retirement boundary.
        ``end_replay`` also calls it automatically after the port accepts the
        command, ensuring sequential late failures cannot cross that boundary.
        """

        if not _nonempty(session_id):
            raise ValueError("session_id must be a non-empty string")
        with self._lock:
            return self._retire_session_unlocked(session_id)

    def _retire_session_unlocked(self, session_id: str) -> int:
        retired = [
            operation_id
            for operation_id, (_, candidate_session_id) in self._operations.items()
            if candidate_session_id == session_id
        ]
        for operation_id in retired:
            del self._operations[operation_id]
            self._active_operations.discard(operation_id)
        return len(retired)

    @staticmethod
    def error_payload(
        error: ReplayApiError,
        *,
        request_id: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        definition = _ERRORS[error.error_code]
        payload: dict[str, Any] = {
            "error_code": error.error_code,
            "category": definition.category,
            "severity": "error",
            "retryable": definition.retryable,
            "affected_capability": (
                error.affected_capability or definition.affected_capability
            ),
            "message": error.user_message or definition.message,
            "request_id": request_id,
            "details": dict(error.details),
        }
        if operation_id is not None:
            payload["operation_id"] = operation_id
        return payload

    @staticmethod
    def _validate_accepted(
        command: str,
        request: Mapping[str, Any],
        accepted: ReplayAccepted,
    ) -> None:
        if not isinstance(accepted, ReplayAccepted):
            raise TypeError("Replay command port must return ReplayAccepted")
        if accepted.session_id is not None and not _nonempty(accepted.session_id):
            raise TypeError("session_id must be a non-empty string")
        if accepted.operation_id is not None and not _nonempty(accepted.operation_id):
            raise TypeError("operation_id must be a non-empty string")
        if accepted.operation_id is not None and command not in _OPERATION_COMMANDS:
            raise TypeError(f"{command} cannot create an operation")
        if accepted.operation_id is not None and not callable(
            accepted.start_operation
        ):
            raise TypeError(
                f"{command} must return a deferred operation starter"
            )
        if accepted.operation_id is None and accepted.start_operation is not None:
            raise TypeError(f"{command} cannot start work without an operation_id")
        if command == "end_replay" and not callable(accepted.commit):
            raise TypeError("end_replay must return a retirement commit callback")
        if command != "end_replay" and accepted.commit is not None:
            raise TypeError(f"{command} cannot return a retirement commit callback")
        if not isinstance(accepted.data, Mapping):
            raise TypeError("Replay accepted data must be a mapping")
        reserved = {
            "request_id",
            "service_generation",
            "session_id",
            "operation_id",
        }
        if reserved.intersection(accepted.data):
            raise TypeError("Replay accepted data cannot replace identity fields")
        expected_data = (
            {"security"}
            if command == "select_symbol"
            else {"snapshot"}
            if command == "get_replay_snapshot"
            else set()
        )
        if set(accepted.data) != expected_data:
            raise TypeError(f"{command} returned an invalid success payload")
        if command == "select_symbol":
            _validate_security(accepted.data["security"])
        if command == "get_replay_snapshot" and not isinstance(
            accepted.data["snapshot"], Mapping
        ):
            raise TypeError("snapshot must be a mapping")
        if command == "get_replay_snapshot":
            validate_replay_snapshot(
                accepted.data["snapshot"],
                expected_session_id=request["session_id"],
            )
        if command == "select_symbol" and accepted.session_id is not None:
            raise TypeError("select_symbol cannot return a session_id")
        if "session_id" in request and accepted.session_id != request["session_id"]:
            raise TypeError(f"{command} returned a mismatched session_id")
        if command in {"begin_replay", "seek_replay"} and (
            accepted.session_id is None or accepted.operation_id is None
        ):
            raise TypeError(f"{command} must return session_id and operation_id")
        if command == "step_replay" and accepted.session_id is None:
            raise TypeError("step_replay must return session_id")


def _validate_request(
    command: str, raw_request: Mapping[str, Any]
) -> dict[str, Any]:
    if command not in REPLAY_COMMANDS or not isinstance(raw_request, Mapping):
        raise ReplayApiError("invalid_request")
    request = dict(raw_request)
    required: dict[str, tuple[str, ...]] = {
        "select_symbol": ("schema_version", "request_id", "symbol"),
        "begin_replay": ("schema_version", "request_id", "symbol", "trade_date"),
        "set_replay_playback": (
            "schema_version", "request_id", "session_id", "playing"
        ),
        "set_replay_speed": (
            "schema_version", "request_id", "session_id", "playback_speed"
        ),
        "step_replay": ("schema_version", "request_id", "session_id"),
        "seek_replay": (
            "schema_version", "request_id", "session_id", "target_time"
        ),
        "end_replay": ("schema_version", "request_id", "session_id"),
        "get_replay_snapshot": ("schema_version", "request_id", "session_id"),
    }
    expected = set(required[command])
    if set(request) != expected:
        raise ReplayApiError("invalid_request")
    if request.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise ReplayApiError("invalid_request")
    if not _nonempty(request.get("request_id")):
        raise ReplayApiError("invalid_request")
    if "session_id" in expected and not _nonempty(request.get("session_id")):
        raise ReplayApiError("invalid_request")
    if "symbol" in expected and not _nonempty(request.get("symbol")):
        raise ReplayApiError("invalid_request")
    if command == "begin_replay":
        _parse_exact_datetime(request["trade_date"], "%Y-%m-%d")
    if command == "seek_replay":
        _parse_exact_datetime(request["target_time"], "%Y-%m-%d %H:%M:%S")
    if command == "set_replay_playback" and type(request["playing"]) is not bool:
        raise ReplayApiError("invalid_request")
    if command == "set_replay_speed" and (
        type(request["playback_speed"]) is not int
        or request["playback_speed"] not in {1, 2, 5, 10}
    ):
        raise ReplayApiError("invalid_request")
    return request


def _parse_exact_datetime(value: object, pattern: str) -> None:
    if not isinstance(value, str):
        raise ReplayApiError("invalid_request")
    try:
        parsed = datetime.strptime(value, pattern)
    except ValueError as error:
        raise ReplayApiError("invalid_request") from error
    if parsed.strftime(pattern) != value:
        raise ReplayApiError("invalid_request")


def _validate_security(value: object) -> None:
    if not isinstance(value, Mapping):
        raise TypeError("security must be a mapping")
    expected = {"symbol", "code", "market", "name", "instrument_type"}
    if set(value) != expected:
        raise TypeError("security has an invalid shape")
    if not all(_nonempty(value.get(key)) for key in expected):
        raise TypeError("security fields must be non-empty strings")
    if value["market"] not in {"sh", "sz"}:
        raise TypeError("security market is invalid")
    if value["instrument_type"] not in {"stock", "etf", "index"}:
        raise TypeError("security instrument_type is invalid")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _request_id_for_error(raw_request: object) -> str:
    if isinstance(raw_request, Mapping) and _nonempty(raw_request.get("request_id")):
        return str(raw_request["request_id"])
    return "missing-request-id"
