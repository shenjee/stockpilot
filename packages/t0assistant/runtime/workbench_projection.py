"""Workbench Projection: atomic assembly of a front-end workbench snapshot.

A :class:`WorkbenchProjection` is a frozen, serializable payload that matches
``logical-schema.json#/$defs/workbench_snapshot``.  It does **not** produce an
event envelope; callers that publish events must wrap ``to_dict()`` with their
own ``schema_version``, ``service_generation``, ``session_id``, ``revision`` and
``event_type``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._market_bars import (
    MARKET_TIMESTAMP_FORMAT,
    RuntimeMarketDataError,
    parse_trade_date,
)
from .pipeline import PipelineResult


class WorkbenchProjectionError(RuntimeMarketDataError):
    """Raised when a workbench snapshot cannot be assembled atomically."""


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")
_TRADE_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_ALLOWED_SESSION_TYPES = frozenset({"live", "replay"})
_LIVE_STATES = frozenset({"created", "loading", "ready", "failed", "retired"})
_REPLAY_STATES = frozenset({"loading", "ready", "playing", "paused", "failed", "retired"})
_ALLOWED_GRANULARITIES = frozenset({"one_minute", "five_minute"})
_ALLOWED_PLAYBACK_SPEEDS = frozenset({1, 2, 5, 10})
_ALLOWED_STEP_SECONDS = frozenset({60, 300})


@dataclass(frozen=True, slots=True)
class SessionProjectionInput:
    """Immutable Session metadata explicitly supplied by the caller.

    The Projection layer does **not** read Coordinator state, wall-clock time,
    or any other implicit source.  The caller is responsible for supplying a
    consistent revision; Projection never increments it.
    """

    session_id: str
    session_type: str
    symbol: str
    trade_date: str | None
    state: str
    revision: int


@dataclass(frozen=True, slots=True)
class ReplayProjectionInput:
    """Immutable Replay cursor metadata explicitly supplied by the caller.

    The field set and value ranges mirror ``replay-v1.schema.json#/$defs/replay_state``.
    Projection validates both the shape and the consistency between the Replay
    cursor, the Session state, and the computed :class:`PipelineResult`.
    """

    granularity: str
    current_time: str
    next_bar_time: str | None
    start_time: str
    end_time: str
    playing: bool
    playback_speed: int
    step_seconds: int


@dataclass(frozen=True, slots=True)
class WorkbenchProjection:
    """Frozen ``workbench_snapshot`` payload.

    The internal ``_payload`` is detached from all caller-provided mutable
    objects by deep copy.  ``to_dict()`` returns a fresh deep copy so that
    external mutation can never contaminate the Projection or future calls.
    """

    _payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a deep-copied snapshot dictionary."""
        return copy.deepcopy(self._payload)


def build_workbench_projection(
    pipeline_result: PipelineResult,
    session: SessionProjectionInput,
    replay: ReplayProjectionInput | None = None,
) -> WorkbenchProjection:
    """Atomically build a complete Workbench Snapshot from a successful pipeline result.

    Args:
        pipeline_result: a fully computed :class:`PipelineResult`.  Only its
            public fields are projected; ``closed_5m_prefix`` and ``daily_bar``
            are deliberately excluded from the output.
        session: explicit Session metadata.  For ``live`` the ``trade_date``
            must be ``None``; for ``replay`` it must equal
            ``pipeline_result.trade_date``.
        replay: Replay cursor metadata supplied by the caller.  Must be
            ``None`` for ``live`` and a :class:`ReplayProjectionInput` for
            ``replay``.

    Raises:
        WorkbenchProjectionError: if any consistency check fails.  No partial
            snapshot is returned.
    """

    if not isinstance(session, SessionProjectionInput):
        raise WorkbenchProjectionError("session must be a SessionProjectionInput")

    _validate_session_fields(session)
    _validate_session_consistency(session, pipeline_result)
    _validate_replay_input(session, replay, pipeline_result.target_time)

    replay_payload: dict[str, Any] | None = None
    if session.session_type == "replay":
        assert replay is not None
        replay_payload = {
            "granularity": replay.granularity,
            "current_time": replay.current_time,
            "next_bar_time": replay.next_bar_time,
            "start_time": replay.start_time,
            "end_time": replay.end_time,
            "playing": replay.playing,
            "playback_speed": replay.playback_speed,
            "step_seconds": replay.step_seconds,
        }

    payload: dict[str, Any] = {
        "timezone": "Asia/Shanghai",
        "session": {
            "session_id": session.session_id,
            "session_type": session.session_type,
            "symbol": session.symbol,
            "trade_date": session.trade_date,
            "state": session.state,
            "revision": session.revision,
        },
        "replay": replay_payload,
        "market": {
            "bars_1m": list(pipeline_result.bars_1m),
            "bars_5m": list(pipeline_result.bars_5m),
            "daily_bars": list(pipeline_result.daily_bars),
            "quote": pipeline_result.quote,
        },
        "indicators": {
            "five_minute": pipeline_result.indicators_5m,
            "one_minute": pipeline_result.indicators_1m,
        },
        "chan_analysis": pipeline_result.chan_analysis,
        "warnings": list(pipeline_result.warnings),
    }

    _validate_payload(payload, session.session_type)

    # Detach from all mutable caller state.  ``to_dict()`` performs another deep
    # copy so repeated calls remain isolated from each other.
    return WorkbenchProjection(_payload=copy.deepcopy(payload))


def _validate_session_fields(session: SessionProjectionInput) -> None:
    """Validate the shape of the Session metadata independent of the result."""

    if not isinstance(session.session_id, str) or not session.session_id:
        raise WorkbenchProjectionError("session_id must be a non-empty string")

    if session.session_type not in _ALLOWED_SESSION_TYPES:
        raise WorkbenchProjectionError(
            "session_type must be 'live' or 'replay'"
        )

    if not _SYMBOL_PATTERN.fullmatch(session.symbol):
        raise WorkbenchProjectionError(
            "symbol must use canonical sh.###### or sz.######"
        )

    allowed_states = _LIVE_STATES if session.session_type == "live" else _REPLAY_STATES
    if session.state not in allowed_states:
        raise WorkbenchProjectionError(
            f"state must be one of {sorted(allowed_states)}"
        )

    if isinstance(session.revision, bool) or not isinstance(
        session.revision, int
    ):
        raise WorkbenchProjectionError("revision must be an integer")
    if session.revision < 0:
        raise WorkbenchProjectionError("revision must be non-negative")

    if session.trade_date is not None:
        if not isinstance(session.trade_date, str):
            raise WorkbenchProjectionError(
                "trade_date must be an ISO date string or null"
            )
        if not _TRADE_DATE_PATTERN.fullmatch(session.trade_date):
            raise WorkbenchProjectionError(
                "trade_date must match YYYY-MM-DD"
            )


def _validate_session_consistency(
    session: SessionProjectionInput,
    pipeline_result: PipelineResult,
) -> None:
    """Validate that Session metadata is consistent with the computed result."""

    if session.symbol != pipeline_result.symbol:
        raise WorkbenchProjectionError(
            "session.symbol must match pipeline_result.symbol"
        )

    result_trade_date = (
        pipeline_result.trade_date.isoformat()
        if isinstance(pipeline_result.trade_date, date)
        else pipeline_result.trade_date
    )

    if session.session_type == "live":
        if session.trade_date is not None:
            raise WorkbenchProjectionError(
                "live session trade_date must be null"
            )
    else:  # replay
        if session.trade_date is None:
            raise WorkbenchProjectionError(
                "replay session trade_date is required"
            )
        if session.trade_date != result_trade_date:
            raise WorkbenchProjectionError(
                "session.trade_date must match pipeline_result.trade_date"
            )


def _validate_replay_input(
    session: SessionProjectionInput,
    replay: ReplayProjectionInput | None,
    target_time: datetime,
) -> None:
    """Validate the Replay cursor metadata matches the session type and result."""

    if session.session_type == "live":
        if replay is not None:
            raise WorkbenchProjectionError(
                "live projection replay must be null"
            )
        return

    # replay
    if replay is None:
        raise WorkbenchProjectionError(
            "replay projection requires replay metadata"
        )
    if not isinstance(replay, ReplayProjectionInput):
        raise WorkbenchProjectionError(
            "replay metadata must be a ReplayProjectionInput"
        )

    if replay.granularity not in _ALLOWED_GRANULARITIES:
        raise WorkbenchProjectionError(
            "replay.granularity must be 'one_minute' or 'five_minute'"
        )

    if replay.playback_speed not in _ALLOWED_PLAYBACK_SPEEDS:
        raise WorkbenchProjectionError(
            "replay.playback_speed must be one of 1, 2, 5, 10"
        )

    if replay.step_seconds not in _ALLOWED_STEP_SECONDS:
        raise WorkbenchProjectionError(
            "replay.step_seconds must be 60 or 300"
        )

    if replay.granularity == "one_minute" and replay.step_seconds != 60:
        raise WorkbenchProjectionError(
            "one_minute replay requires step_seconds == 60"
        )
    if replay.granularity == "five_minute" and replay.step_seconds != 300:
        raise WorkbenchProjectionError(
            "five_minute replay requires step_seconds == 300"
        )

    try:
        current_dt = datetime.strptime(replay.current_time, MARKET_TIMESTAMP_FORMAT)
        start_dt = datetime.strptime(replay.start_time, MARKET_TIMESTAMP_FORMAT)
        end_dt = datetime.strptime(replay.end_time, MARKET_TIMESTAMP_FORMAT)
    except (ValueError, TypeError) as exc:
        raise WorkbenchProjectionError(
            f"replay time must use {MARKET_TIMESTAMP_FORMAT}"
        ) from exc

    trade_date = parse_trade_date(session.trade_date)

    for label, dt in (
        ("start_time", start_dt),
        ("current_time", current_dt),
        ("end_time", end_dt),
    ):
        if dt.date() != trade_date:
            raise WorkbenchProjectionError(
                f"replay.{label} must belong to session.trade_date ({trade_date})"
            )

    if not (start_dt <= current_dt <= end_dt):
        raise WorkbenchProjectionError(
            "replay.current_time must be between start_time and end_time"
        )

    target_time_str = target_time.strftime(MARKET_TIMESTAMP_FORMAT)
    if replay.current_time != target_time_str:
        raise WorkbenchProjectionError(
            f"replay.current_time ({replay.current_time}) must match "
            f"pipeline_result.target_time ({target_time_str})"
        )

    if replay.next_bar_time is not None:
        try:
            next_dt = datetime.strptime(
                replay.next_bar_time, MARKET_TIMESTAMP_FORMAT
            )
        except (ValueError, TypeError) as exc:
            raise WorkbenchProjectionError(
                f"replay.next_bar_time must use {MARKET_TIMESTAMP_FORMAT}"
            ) from exc
        if next_dt.date() != trade_date:
            raise WorkbenchProjectionError(
                f"replay.next_bar_time must belong to session.trade_date ({trade_date})"
            )
        if not (current_dt < next_dt <= end_dt):
            raise WorkbenchProjectionError(
                "replay.next_bar_time must satisfy current_time < next_bar_time <= end_time"
            )

    expected_playing = session.state == "playing"
    if replay.playing != expected_playing:
        raise WorkbenchProjectionError(
            f"replay.playing ({replay.playing}) must equal "
            f"session.state == 'playing' ({expected_playing})"
        )


def _validate_payload(payload: dict[str, Any], session_type: str) -> None:
    """Validate the assembled payload against the frozen contract."""

    validator = _REPLAY_VALIDATOR if session_type == "replay" else _LOGICAL_VALIDATOR
    errors = list(validator.iter_errors(payload))
    if not errors:
        return

    messages = "; ".join(
        f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )
    raise WorkbenchProjectionError(
        f"snapshot does not match frozen contract: {messages}"
    )


def _load_contract(name: str) -> dict[str, Any]:
    """Load a contract document bundled as package data.

    The schemas are shipped with ``packages.t0assistant`` so that the runtime
    works from source, editable installs, and built wheels without relying on
    the repository layout.
    """

    data_file = resources.files("packages.t0assistant") / "contracts" / name
    with data_file.open(encoding="utf-8") as stream:
        return json.load(stream)


def _build_logical_validator() -> Draft202012Validator:
    schema_doc = _load_contract("logical-schema.json")
    registry = Registry().with_resource(
        schema_doc["$id"], Resource.from_contents(schema_doc)
    )
    schema = {"$ref": f"{schema_doc['$id']}#/$defs/workbench_snapshot"}
    return Draft202012Validator(schema, registry=registry)


def _build_replay_validator() -> Draft202012Validator:
    logical = _load_contract("logical-schema.json")
    replay = _load_contract("replay-v1.schema.json")
    registry = Registry().with_resources(
        [
            (logical["$id"], Resource.from_contents(logical)),
            (replay["$id"], Resource.from_contents(replay)),
        ]
    )
    schema = {"$ref": f"{replay['$id']}#/$defs/workbench_snapshot"}
    return Draft202012Validator(schema, registry=registry)


_LOGICAL_VALIDATOR = _build_logical_validator()
_REPLAY_VALIDATOR = _build_replay_validator()
