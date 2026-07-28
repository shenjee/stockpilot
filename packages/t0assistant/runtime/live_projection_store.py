"""Live Projection Store: the single revision authority for Live events.

T0-026 places the authoritative publish boundary between the Live Session
candidate producer (T0-023) and any future transport consumer.  The store is the
only component that assigns and advances a ``revision`` for a Live workbench.

Responsibilities (minimal, transport-free):

* Validate every publishable item against the Coordinator acceptance boundary
  (``session_id`` + ``generation`` must still be the active Live Session).
* Assign the next monotonic revision to an accepted full snapshot candidate.
  The candidate itself never carries a revision (T0-023 contract).
* Validate the proposed revision of a typed incremental update and apply it to
  the authoritative state only when it is exactly ``current + 1``.
* Reject old Session, old generation, duplicate, stale, and out-of-order
  (gap) events without mutating authoritative state or reviving a retired
  Session.
* Expose ``get_live_snapshot`` so a consumer can re-baseline after a revision
  gap or reconnect by fetching the latest complete authoritative snapshot, with
  ``snapshot.session.revision`` equal to the latest accepted revision.

The store emits ``t0_app_v1`` event envelopes but does not touch the backend
HTTP/WebSocket transport, the Electron gateway, or any public schema.  Increment
application uses whole-field replacement semantics for ``market_update``,
``indicators_updated`` and ``chan_analysis_replaced``; richer merge behaviour is
owned by the refresh layer (T0-024) and may refine this later.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from .coordinator import SessionType
from .live_session import LiveSnapshotCandidate


SCHEMA_VERSION = "t0_app_v1"

_INCREMENTAL_EVENT_TYPES = frozenset(
    {"market_update", "indicators_updated", "chan_analysis_replaced"}
)
_MARKET_TARGETS = frozenset({"quote", "bars_1m", "bars_5m", "daily_bars"})


class LiveProjectionStoreError(RuntimeError):
    """Base class for Live projection store failures."""


class LiveProjectionSnapshotUnavailable(LiveProjectionStoreError):
    """Raised when a requested authoritative snapshot cannot be returned."""


class _CoordinatorAcceptancePort(Protocol):
    """The slice of AppCoordinator the store relies on."""

    def accepts_result(
        self,
        *,
        session_type: SessionType | str,
        session_id: str,
        generation: int,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class LiveAcceptedEvent:
    """A published ``t0_app_v1`` event envelope produced by the authority."""

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
    """A typed incremental update carrying a producer-proposed revision.

    The producer (e.g. the Live refresh layer) builds the update on top of a
    known snapshot and proposes ``proposed_revision = that_snapshot.revision +
    1``.  The store is the authority that accepts or rejects it.
    """

    session_id: str
    generation: int
    proposed_revision: int
    event_type: str
    payload: dict[str, Any]


class LiveProjectionStore:
    """Single revision authority for the Live workbench.

    Thread-safe: every accept/apply/snapshot operation takes the internal lock
    so concurrent publishes produce a unique, strictly ordered revision
    sequence.
    """

    def __init__(
        self,
        coordinator: _CoordinatorAcceptancePort,
        *,
        service_generation: int,
    ) -> None:
        if not callable(getattr(coordinator, "accepts_result", None)):
            raise TypeError("coordinator must implement accepts_result")
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
        if not self._accepts(candidate.session_id, candidate.generation):
            return None

        with self._lock:
            # Re-check under the lock: the Session may have retired between
            # the unlocked pre-check and here.
            if not self._accepts(candidate.session_id, candidate.generation):
                return None

            session_key = (candidate.session_id, candidate.generation)
            if self._current_session is None or self._current_session != session_key:
                revision = 0
            else:
                assert self._current_revision is not None
                revision = self._current_revision + 1

            projection = candidate.build_projection(revision)
            payload = projection.to_dict()

            self._current_session = session_key
            self._current_revision = revision
            self._current_payload = payload

            return LiveAcceptedEvent(
                schema_version=SCHEMA_VERSION,
                service_generation=self._service_generation,
                session_id=candidate.session_id,
                revision=revision,
                event_type="workbench_snapshot",
                payload=copy.deepcopy(payload),
            )

    def accept_incremental(
        self,
        update: LiveIncrementalUpdate,
    ) -> LiveAcceptedEvent | None:
        """Accept a typed incremental update with a producer-proposed revision.

        Returns ``None`` for any rejected update (old Session, old generation,
        no baseline, duplicate/stale revision, or a gap).  Rejected updates do
        not advance revision, do not mutate the snapshot, and do not publish.
        """

        if not isinstance(update, LiveIncrementalUpdate):
            raise TypeError("update must be a LiveIncrementalUpdate")
        if update.event_type not in _INCREMENTAL_EVENT_TYPES:
            raise ValueError(
                "update.event_type must be one of "
                f"{sorted(_INCREMENTAL_EVENT_TYPES)}"
            )
        if (
            not isinstance(update.proposed_revision, int)
            or isinstance(update.proposed_revision, bool)
            or update.proposed_revision < 0
        ):
            raise ValueError("proposed_revision must be a non-negative integer")
        if not self._accepts(update.session_id, update.generation):
            return None

        with self._lock:
            if not self._accepts(update.session_id, update.generation):
                return None
            if (
                self._current_session is None
                or self._current_session != (update.session_id, update.generation)
                or self._current_payload is None
                or self._current_revision is None
            ):
                # No baseline for this Session yet; incremental cannot apply.
                return None

            if update.proposed_revision <= self._current_revision:
                # Duplicate or stale revision.
                return None
            if update.proposed_revision > self._current_revision + 1:
                # Gap / out-of-order: stop applying increments; the consumer
                # must re-baseline via get_live_snapshot.
                return None

            # proposed_revision == current + 1
            self._apply_incremental_unlocked(update.event_type, update.payload)
            self._current_revision = update.proposed_revision
            self._current_payload["session"]["revision"] = update.proposed_revision

            return LiveAcceptedEvent(
                schema_version=SCHEMA_VERSION,
                service_generation=self._service_generation,
                session_id=update.session_id,
                revision=update.proposed_revision,
                event_type=update.event_type,
                payload=copy.deepcopy(update.payload),
            )

    def get_live_snapshot(
        self,
        *,
        session_id: str,
        generation: int,
    ) -> dict[str, Any]:
        """Return the latest complete authoritative snapshot for rebaseline.

        Raises :class:`LiveProjectionSnapshotUnavailable` when the requested
        Session is not the current authoritative Live Session (wrong Session,
        retired, or no snapshot published yet).
        """

        with self._lock:
            if (
                self._current_session is None
                or self._current_session != (session_id, generation)
                or self._current_payload is None
                or not self._accepts(session_id, generation)
            ):
                raise LiveProjectionSnapshotUnavailable(
                    "no authoritative Live snapshot available for the requested Session"
                )
            snapshot = copy.deepcopy(self._current_payload)
            # Keep the published invariant even if a future code path leaves the
            # stored payload's revision briefly behind.
            snapshot["session"]["revision"] = self._current_revision
            return snapshot

    def _accepts(self, session_id: str, generation: int) -> bool:
        return self._coordinator.accepts_result(
            session_type=SessionType.LIVE,
            session_id=session_id,
            generation=generation,
        )

    def _apply_incremental_unlocked(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Apply an accepted increment to the authoritative payload in place.

        Whole-field replacement is used; merge refinement is owned by T0-024.
        """

        assert self._current_payload is not None
        if event_type == "market_update":
            target = payload.get("target")
            if target not in _MARKET_TARGETS:
                raise ValueError(
                    "market_update target must be one of "
                    f"{sorted(_MARKET_TARGETS)}"
                )
            market = self._current_payload["market"]
            if target == "quote":
                market["quote"] = copy.deepcopy(payload.get("quote"))
            else:
                # Typed bar updates are incremental: the producer sends the new
                # bars that extend the current series.  Ordering and dedup of the
                # bar series remain the refresh layer's responsibility (T0-024).
                market[target].extend(
                    copy.deepcopy(bar) for bar in payload.get("bars", ())
                )
        elif event_type == "indicators_updated":
            self._current_payload["indicators"] = copy.deepcopy(payload)
        else:  # chan_analysis_replaced
            self._current_payload["chan_analysis"] = copy.deepcopy(payload)
