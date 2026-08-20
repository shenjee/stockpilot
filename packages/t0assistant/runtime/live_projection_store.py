"""Live Projection Store: the single revision authority for Live events.

T0-026 places the authoritative publish boundary between the Live Session
candidate producer (T0-023) and any future transport consumer.  The store is the
only component that assigns and advances a ``revision`` for a Live workbench.

Responsibilities (minimal, transport-free):

* Validate every publishable item against the Coordinator acceptance boundary
  using ``commit_if_accepted`` so identity verification and authoritative state
  commit share a single linearization point (no stale-Session race).
* Assign the next monotonic revision to every accepted full snapshot candidate
  **and** every accepted typed incremental update.  Producers never supply a
  revision; the store is the sole authority.
* Apply accepted increments to the authoritative state under the same atomic
  boundary, after validating the typed payload against the frozen contracts so
  invalid payloads can never contaminate the authoritative snapshot.
* Reject old Session / retired Session items without mutating authoritative
  state or reviving a retired Session.  Revision gap detection is a consumer
  concern (transport layer); the store never discards a valid domain update to
  manufacture a gap.  A strictly older ``projection_seq`` is not a valid
  successor of the current snapshot: applying it would rewind ``bars_5m``.
* Expose ``get_live_snapshot`` so a consumer can re-baseline after a revision
  gap or reconnect by fetching the latest complete authoritative snapshot, with
  ``snapshot.session.revision`` equal to the latest accepted revision.

The store emits ``t0_app_v2`` event envelopes but does not touch the backend
HTTP/WebSocket transport, the Electron gateway, or any public schema.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ._market_bars import RuntimeMarketDataError
from .coordinator import SessionType
from .live_session import LiveSnapshotCandidate


SCHEMA_VERSION = "t0_app_v2"

_INCREMENTAL_EVENT_TYPES = frozenset(
    {
        "market_update",
        "indicators_updated",
        "chan_analysis_replaced",
        "live_market_view_updated",
    }
)
_MARKET_TARGETS = frozenset({"quote", "bars_1m", "bars_5m", "daily_bars"})


class LiveProjectionStoreError(RuntimeMarketDataError):
    """Base class for Live projection store failures."""


class LiveProjectionSnapshotUnavailable(LiveProjectionStoreError):
    """Raised when a requested authoritative snapshot cannot be returned."""


class LiveProjectionValidationError(LiveProjectionStoreError, ValueError):
    """A typed incremental payload does not match the frozen contract."""


class _CoordinatorAcceptancePort(Protocol):
    """The slice of AppCoordinator the store relies on."""

    def commit_if_accepted(
        self,
        *,
        session_type: SessionType | str,
        session_id: str,
        generation: int,
        commit: Any,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class LiveAcceptedEvent:
    """A published ``t0_app_v2`` event envelope produced by the authority."""

    schema_version: str
    service_generation: int
    session_id: str
    revision: int
    event_type: str
    payload: dict[str, Any]
    operation_id: str | None = None

    def to_envelope(self) -> dict[str, Any]:
        """Return a fresh ``event_envelope`` dictionary matching the contract."""

        envelope: dict[str, Any] = {
            "schema_version": self.schema_version,
            "service_generation": self.service_generation,
            "session_id": self.session_id,
            "revision": self.revision,
            "event_type": self.event_type,
            "payload": copy.deepcopy(self.payload),
        }
        if self.operation_id is not None:
            envelope["operation_id"] = self.operation_id
        return envelope


@dataclass(frozen=True, slots=True)
class LiveIncrementalUpdate:
    """A typed incremental update produced by the Live refresh layer.

    The store is the sole revision authority, so the update carries no
    revision.  The producer builds the update on top of the latest accepted
    state; the store applies it and assigns ``current + 1`` atomically.

    ``market_epoch`` stamps the Live market epoch the increment belongs to.
    The store rejects increments whose epoch does not match the latest
    accepted full snapshot epoch inside its atomic commit boundary.

    ``projection_seq`` is the lock-order snapshot generation number.  The
    store rejects a strictly older sequence after a newer one has already
    been applied, so a delayed official ``bars_5m`` payload cannot delete
    the current dynamic 5m bar.
    """

    session_id: str
    generation: int
    event_type: str
    payload: dict[str, Any]
    market_epoch: int | None = None
    projection_seq: int | None = None


class LiveProjectionStore:
    """Single revision authority for the Live workbench.

    Thread-safe: ``accept_candidate`` and ``accept_incremental`` delegate to
    ``coordinator.commit_if_accepted`` so identity verification and the
    authoritative state commit run under the Coordinator state lock as one
    linearization point.  The store's own ``_lock`` serializes access to the
    authoritative payload/revision inside the commit callback.
    """

    def __init__(
        self,
        coordinator: _CoordinatorAcceptancePort,
        *,
        service_generation: int,
    ) -> None:
        if not callable(getattr(coordinator, "commit_if_accepted", None)):
            raise TypeError("coordinator must implement commit_if_accepted")
        if isinstance(service_generation, bool) or not isinstance(
            service_generation, int
        ):
            raise TypeError("service_generation must be an integer")
        if service_generation < 1:
            raise ValueError("service_generation must be >= 1")

        self._coordinator = coordinator
        self._service_generation = service_generation
        self._lock = RLock()
        self._current_session: tuple[str, int] | None = None
        self._current_revision: int | None = None
        self._current_payload: dict[str, Any] | None = None
        self._published_market_epoch: int | None = None
        self._last_projection_seq: int | None = None

    @property
    def current_revision(self) -> int | None:
        with self._lock:
            return self._current_revision

    @property
    def current_session(self) -> tuple[str, int] | None:
        with self._lock:
            return self._current_session

    @property
    def has_snapshot(self) -> bool:
        with self._lock:
            return self._current_payload is not None

    @property
    def published_market_epoch(self) -> int | None:
        with self._lock:
            return self._published_market_epoch

    def accept_candidate(
        self,
        candidate: LiveSnapshotCandidate,
    ) -> LiveAcceptedEvent | None:
        """Accept a full snapshot candidate and assign the next revision.

        Returns ``None`` when the Coordinator no longer accepts the candidate's
        Session (retired or superseded); the late result is dropped without
        mutating authoritative state or reviving the Session.
        """

        if not isinstance(candidate, LiveSnapshotCandidate):
            raise TypeError("candidate must be a LiveSnapshotCandidate")

        event_box: list[LiveAcceptedEvent] = []

        def commit() -> None:
            with self._lock:
                session_key = (candidate.session_id, candidate.generation)
                if (
                    self._current_session == session_key
                    and self._published_market_epoch is not None
                    and candidate.market_epoch < self._published_market_epoch
                ):
                    return
                if (
                    self._current_session is None
                    or self._current_session != session_key
                ):
                    revision = 0
                else:
                    assert self._current_revision is not None
                    revision = self._current_revision + 1

                projection = candidate.build_projection(revision)
                payload = projection.to_dict()

                self._current_session = session_key
                self._current_revision = revision
                self._current_payload = payload
                self._published_market_epoch = candidate.market_epoch
                self._last_projection_seq = None
                event_box.append(
                    LiveAcceptedEvent(
                        schema_version=SCHEMA_VERSION,
                        service_generation=self._service_generation,
                        session_id=candidate.session_id,
                        revision=revision,
                        event_type="workbench_snapshot",
                        payload=copy.deepcopy(payload),
                    )
                )

        accepted = self._coordinator.commit_if_accepted(
            session_type=SessionType.LIVE,
            session_id=candidate.session_id,
            generation=candidate.generation,
            commit=commit,
        )
        if not accepted:
            return None
        return event_box[0] if event_box else None

    def accept_incremental(
        self,
        update: LiveIncrementalUpdate,
    ) -> LiveAcceptedEvent | None:
        """Accept a typed incremental update and assign the next revision.

        The store is the sole revision authority: the update carries no
        revision, and the store assigns ``current + 1`` after applying the
        payload.  Returns ``None`` for an old/retired Session.  An invalid
        payload raises :class:`LiveProjectionValidationError` before any
        authoritative state is touched.
        """

        if not isinstance(update, LiveIncrementalUpdate):
            raise TypeError("update must be a LiveIncrementalUpdate")
        if update.event_type not in _INCREMENTAL_EVENT_TYPES:
            raise ValueError(
                "update.event_type must be one of "
                f"{sorted(_INCREMENTAL_EVENT_TYPES)}"
            )
        # Validate the typed payload shape before touching authoritative state.
        _validate_incremental_payload(update.event_type, update.payload)

        event_box: list[LiveAcceptedEvent] = []

        def commit() -> None:
            with self._lock:
                session_key = (update.session_id, update.generation)
                if (
                    self._current_session is None
                    or self._current_session != session_key
                    or self._current_payload is None
                    or self._current_revision is None
                    or self._published_market_epoch is None
                ):
                    # No baseline for this Session; an incremental cannot apply
                    # without a prior full snapshot.  Drop silently.
                    return
                if (
                    update.market_epoch is not None
                    and update.market_epoch != self._published_market_epoch
                ):
                    # Reject stale or ahead-of-baseline epochs atomically with
                    # the authoritative snapshot revision.
                    return
                if (
                    update.projection_seq is not None
                    and self._last_projection_seq is not None
                    and update.projection_seq < self._last_projection_seq
                ):
                    # Older lock-order snapshot.  Applying its full bars_5m
                    # would drop the current dynamic 5m bar.
                    return
                # Apply to a deep-copy staging area so a validation failure
                # leaves the authoritative state untouched.
                staged = copy.deepcopy(self._current_payload)
                _apply_incremental(update.event_type, update.payload, staged)
                _validate_full_snapshot(staged)

                revision = self._current_revision + 1
                staged["session"]["revision"] = revision
                self._current_payload = staged
                self._current_revision = revision
                if update.projection_seq is not None:
                    self._last_projection_seq = update.projection_seq
                event_box.append(
                    LiveAcceptedEvent(
                        schema_version=SCHEMA_VERSION,
                        service_generation=self._service_generation,
                        session_id=update.session_id,
                        revision=revision,
                        event_type=update.event_type,
                        payload=copy.deepcopy(update.payload),
                    )
                )

        accepted = self._coordinator.commit_if_accepted(
            session_type=SessionType.LIVE,
            session_id=update.session_id,
            generation=update.generation,
            commit=commit,
        )
        if not accepted:
            return None
        return event_box[0] if event_box else None

    def accept_operation_failure(
        self,
        *,
        session_id: str,
        generation: int,
        operation_id: str,
        payload: dict[str, Any],
        market_epoch: int | None = None,
    ) -> LiveAcceptedEvent | None:
        """Publish a recoverable failure without replacing market facts.

        A running Session with an accepted baseline advances that baseline's
        revision.  A newly selected/rebuilt Session can fail before producing
        any baseline; its failure is still published at revision ``0`` while
        an older successful snapshot remains retained for later recovery.

        ``market_epoch`` stamps the Live market epoch the failure belongs to.
        Failures whose epoch does not match the latest accepted full snapshot
        epoch are rejected inside the atomic commit boundary.
        """

        if not session_id or not operation_id:
            raise ValueError("session_id and operation_id must be non-empty")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        event_box: list[LiveAcceptedEvent] = []

        def commit() -> None:
            with self._lock:
                session_key = (session_id, generation)
                if (
                    self._current_session == session_key
                    and self._published_market_epoch is not None
                    and market_epoch is not None
                    and market_epoch != self._published_market_epoch
                ):
                    return
                current_payload = self._current_payload
                current_revision = self._current_revision
                if (
                    self._current_session == session_key
                    and current_payload is not None
                    and current_revision is not None
                ):
                    revision = current_revision + 1
                    staged = copy.deepcopy(current_payload)
                    staged["session"]["revision"] = revision
                    self._current_payload = staged
                    self._current_revision = revision
                else:
                    revision = 0
                event_box.append(
                    LiveAcceptedEvent(
                        schema_version=SCHEMA_VERSION,
                        service_generation=self._service_generation,
                        session_id=session_id,
                        revision=revision,
                        event_type="operation_failed",
                        operation_id=operation_id,
                        payload=copy.deepcopy(payload),
                    )
                )

        accepted = self._coordinator.commit_if_accepted(
            session_type=SessionType.LIVE,
            session_id=session_id,
            generation=generation,
            commit=commit,
        )
        if not accepted:
            return None
        return event_box[0] if event_box else None

    def get_live_snapshot(
        self,
        *,
        session_id: str,
        generation: int,
    ) -> dict[str, Any]:
        """Return the latest complete authoritative snapshot for rebaseline.

        Raises :class:`LiveProjectionSnapshotUnavailable` when the requested
        Session is not the current authoritative Live Session (wrong Session,
        retired, or no snapshot published yet).  Uses ``commit_if_accepted``
        so the read is atomic with respect to a concurrent retirement.
        """

        snapshot_box: list[dict[str, Any]] = []

        def commit() -> None:
            with self._lock:
                if (
                    self._current_session is None
                    or self._current_session != (session_id, generation)
                    or self._current_payload is None
                    or self._current_revision is None
                ):
                    return
                snapshot = copy.deepcopy(self._current_payload)
                snapshot["session"]["revision"] = self._current_revision
                snapshot_box.append(snapshot)

        accepted = self._coordinator.commit_if_accepted(
            session_type=SessionType.LIVE,
            session_id=session_id,
            generation=generation,
            commit=commit,
        )
        if not accepted or not snapshot_box:
            raise LiveProjectionSnapshotUnavailable(
                "no authoritative Live snapshot available for the requested Session"
            )
        return snapshot_box[0]


# ---------------------------------------------------------------------------
# Payload validation and application
# ---------------------------------------------------------------------------


def _validate_incremental_payload(event_type: str, payload: dict[str, Any]) -> None:
    """Validate a typed incremental payload against the frozen app-v1 contract."""

    validator = _INCREMENTAL_VALIDATORS.get(event_type)
    if validator is None:  # pragma: no cover - guarded by caller
        raise LiveProjectionValidationError(
            f"no validator for event_type {event_type!r}"
        )
    errors = list(validator.iter_errors(payload))
    if not errors:
        return
    messages = "; ".join(
        f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )
    raise LiveProjectionValidationError(
        f"incremental payload does not match frozen contract: {messages}"
    )


def _validate_full_snapshot(payload: dict[str, Any]) -> None:
    """Validate the authoritative workbench snapshot after applying an increment."""

    errors = list(_LOGICAL_SNAPSHOT_VALIDATOR.iter_errors(payload))
    if not errors:
        return
    messages = "; ".join(
        f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )
    raise LiveProjectionValidationError(
        f"authoritative snapshot does not match frozen contract: {messages}"
    )


def _apply_incremental(
    event_type: str,
    payload: dict[str, Any],
    target: dict[str, Any],
) -> None:
    """Apply an accepted increment to ``target`` in place.

    Bar arrays and indicator series use timestamp-keyed upsert with ascending
    sort, mirroring the Renderer's ``mergeTimestampRows`` so a rebaseline
    snapshot never loses history and stays chronologically ordered (a
    late-arriving earlier row lands in order, not at the tail).  Five-minute
    updates also drop unclosed rows whose timestamps are absent from the
    increment so a new dynamic bucket can replace the previous one.  ``quote``
    and ``chan_analysis`` are authoritative full replacements.  Incoming rows
    are deep-copied so the caller's payload can never alias the authoritative
    state.
    """

    if event_type == "market_update":
        market = target["market"]
        target_field = payload["target"]
        if target_field == "quote":
            market["quote"] = copy.deepcopy(payload["quote"])
        elif target_field == "bars_5m":
            market[target_field] = _merge_five_minute_bars(
                market[target_field], payload["bars"]
            )
        else:
            market[target_field] = _merge_rows_by_timestamp(
                market[target_field], payload["bars"]
            )
    elif event_type == "indicators_updated":
        _merge_indicators(target["indicators"], payload)
    elif event_type == "live_market_view_updated":
        target["live_market_view"] = copy.deepcopy(payload)
    else:  # chan_analysis_replaced
        target["chan_analysis"] = copy.deepcopy(payload)


def merge_five_minute_bars(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Upsert 5m bars and drop unclosed rows absent from the increment.

    One-minute refreshes publish only the current dynamic (unclosed) 5m bar.
    Timestamp merge cannot delete the previous bucket's dynamic bar, so any
    unclosed row whose timestamp is missing from ``incoming`` is removed.
    Closed bars are never deleted here; official 5m increments include the
    current dynamic bar so they cannot wipe it.

    Public test surface for cross-runtime parity with Renderer
    ``mergeFiveMinuteBars``; locked by
    ``apps/t0-assistant/contracts/fixtures/live-five-minute-merge-v1.json``.
    """

    incoming_timestamps = {
        row["timestamp"] for row in incoming or () if "timestamp" in row
    }
    retained = [
        row
        for row in current or ()
        if row.get("closed") is True or row.get("timestamp") in incoming_timestamps
    ]
    return _merge_rows_by_timestamp(retained, incoming)


def _merge_five_minute_bars(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Compatibility alias for :func:`merge_five_minute_bars`."""

    return merge_five_minute_bars(current, incoming)


def _merge_rows_by_timestamp(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Timestamp-keyed upsert with ascending sort.

    Mirrors the Renderer's ``mergeTimestampRows``: rows in ``incoming`` replace
    rows with a matching ``timestamp`` in ``current``; new timestamps are
    inserted; the result is sorted ascending by ``timestamp``.  Incoming rows
    are deep-copied so the caller's payload stays detached from the
    authoritative state; ``current`` rows are assumed already detached.
    """

    by_timestamp: dict[str, dict[str, Any]] = {}
    for row in current or ():
        by_timestamp[row["timestamp"]] = row
    for row in incoming or ():
        by_timestamp[row["timestamp"]] = copy.deepcopy(row)
    return sorted(by_timestamp.values(), key=lambda row: row["timestamp"])


def _merge_indicators(
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    """Merge an indicators increment into ``current`` in place.

    Mirrors the Renderer's ``applyIndicatorUpdate``: each indicator series is
    merged by timestamp-keyed upsert with ascending sort, scalar structure
    fields (``period``, ``stddev``, ``fast_period`` ...) are overwritten by the
    increment, and historical points absent from the increment are preserved.
    This keeps the authoritative snapshot consistent with the Renderer's
    projection after a rebaseline.
    """

    for timeframe in ("five_minute", "one_minute"):
        current_tf = current.get(timeframe) or {}
        incoming_tf = incoming.get(timeframe) or {}
        if not incoming_tf:
            continue
        merged: dict[str, Any] = {**current_tf, **incoming_tf}
        if timeframe == "five_minute":
            current_ma = current_tf.get("ma") or {}
            incoming_ma = incoming_tf.get("ma") or {}
            merged["ma"] = {
                key: _merge_rows_by_timestamp(
                    current_ma.get(key), incoming_ma.get(key)
                )
                for key in ("ma5", "ma10", "ma20", "ma30", "ma60")
            }
            current_boll = current_tf.get("boll") or {}
            incoming_boll = incoming_tf.get("boll") or {}
            merged["boll"] = {
                **current_boll,
                **incoming_boll,
                **{
                    key: _merge_rows_by_timestamp(
                        current_boll.get(key), incoming_boll.get(key)
                    )
                    for key in ("upper", "middle", "lower")
                },
            }
            volume_keys = ("values", "ma5", "ma10")
        else:
            volume_keys = ("values",)
        current_volume = current_tf.get("volume") or {}
        incoming_volume = incoming_tf.get("volume") or {}
        merged["volume"] = {
            **current_volume,
            **incoming_volume,
            **{
                key: _merge_rows_by_timestamp(
                    current_volume.get(key), incoming_volume.get(key)
                )
                for key in volume_keys
            },
        }
        if timeframe == "one_minute":
            merged["vwap"] = _merge_rows_by_timestamp(
                current_tf.get("vwap"), incoming_tf.get("vwap")
            )
        current_macd = current_tf.get("macd") or {}
        incoming_macd = incoming_tf.get("macd") or {}
        merged["macd"] = {
            **current_macd,
            **incoming_macd,
            **{
                key: _merge_rows_by_timestamp(
                    current_macd.get(key), incoming_macd.get(key)
                )
                for key in ("dif", "dea", "histogram")
            },
        }
        current[timeframe] = merged


# ---------------------------------------------------------------------------
# Contract loaders
# ---------------------------------------------------------------------------


def _load_contract(package: str, name: str) -> dict[str, Any]:
    from importlib import resources

    data_file = resources.files(package) / "contracts" / name
    with data_file.open(encoding="utf-8") as stream:
        import json

        return json.load(stream)


def _build_incremental_validators() -> dict[str, Draft202012Validator]:
    logical = _load_contract("packages.t0assistant", "logical-v2.schema.json")
    registry = Registry().with_resource(
        logical["$id"], Resource.from_contents(logical)
    )
    logic_id = logical["$id"]
    # market_update_payload mirrors app-v2.schema.json#$defs/market_update_payload
    # without taking a runtime dependency on the app contracts directory.
    market_update_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["target", "bars", "quote"],
        "properties": {
            "target": {"enum": ["quote", "bars_1m", "bars_5m", "daily_bars"]},
            "bars": {"type": "array", "items": {"$ref": f"{logic_id}#/$defs/bar"}},
            "quote": {
                "oneOf": [
                    {"$ref": f"{logic_id}#/$defs/quote"},
                    {"type": "null"},
                ]
            },
        },
        "allOf": [
            {
                "if": {"properties": {"target": {"const": "quote"}}},
                "then": {
                    "properties": {
                        "bars": {"maxItems": 0},
                        "quote": {"$ref": f"{logic_id}#/$defs/quote"},
                    }
                },
            },
            {
                "if": {"properties": {"target": {"enum": ["bars_1m", "bars_5m", "daily_bars"]}}},
                "then": {"properties": {"quote": {"type": "null"}}},
            },
        ],
    }
    validators: dict[str, Draft202012Validator] = {
        "market_update": Draft202012Validator(market_update_schema, registry=registry),
        "indicators_updated": Draft202012Validator(
            {"$ref": f"{logic_id}#/$defs/indicators"}, registry=registry
        ),
        "chan_analysis_replaced": Draft202012Validator(
            {"$ref": f"{logic_id}#/$defs/chan_analysis"}, registry=registry
        ),
        "live_market_view_updated": Draft202012Validator(
            {"$ref": f"{logic_id}#/$defs/live_market_view"}, registry=registry
        ),
    }
    return validators


def _build_logical_snapshot_validator() -> Draft202012Validator:
    logical = _load_contract("packages.t0assistant", "logical-v2.schema.json")
    registry = Registry().with_resource(
        logical["$id"], Resource.from_contents(logical)
    )
    schema = {"$ref": f"{logical['$id']}#/$defs/workbench_snapshot"}
    return Draft202012Validator(schema, registry=registry)


_INCREMENTAL_VALIDATORS = _build_incremental_validators()
_LOGICAL_SNAPSHOT_VALIDATOR = _build_logical_snapshot_validator()
