"""Port-based application and Session lifecycle coordination.

The coordinator deliberately knows nothing about market providers, clocks,
pipelines, or transport adapters. Concrete Live and Replay implementations
enter through :class:`SessionFactoryPort`; the coordinator owns only their
identity, generation, visibility, and retirement boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import re
from threading import RLock
from typing import Callable, Protocol
from uuid import uuid4


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")


class CoordinatorError(RuntimeError):
    """Base class for stable coordinator lifecycle failures."""


class CoordinatorStateError(CoordinatorError):
    """The requested transition is not valid in the current App state."""


class CoordinatorValidationError(CoordinatorError, ValueError):
    """A lifecycle input is malformed."""


class CoordinatorRetirementError(CoordinatorError):
    """One or more Sessions could not be retired during final cleanup."""

    def __init__(self, failures: tuple[Exception, ...]) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} Session retirement(s) failed")


class AppMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"


class SessionType(str, Enum):
    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Immutable identity passed to a concrete Session implementation."""

    session_id: str
    session_type: SessionType
    symbol: str
    generation: int
    trade_date: str | None = None


class SessionPort(Protocol):
    """The lifecycle surface required from a Live or Replay Session."""

    def retire(self) -> None:
        """Cancel outstanding work and release Session resources."""


class SessionFactoryPort(Protocol):
    """Creates concrete Sessions without exposing pipeline details."""

    def create_live(self, spec: SessionSpec) -> SessionPort: ...

    def create_replay(self, spec: SessionSpec) -> SessionPort: ...


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """Read-only identity exposed by the coordinator."""

    session_id: str
    session_type: SessionType
    symbol: str
    generation: int
    trade_date: str | None

    @classmethod
    def from_spec(cls, spec: SessionSpec) -> SessionIdentity:
        return cls(
            session_id=spec.session_id,
            session_type=spec.session_type,
            symbol=spec.symbol,
            generation=spec.generation,
            trade_date=spec.trade_date,
        )

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "session_id": self.session_id,
            "session_type": self.session_type.value,
            "symbol": self.symbol,
            "generation": self.generation,
            "trade_date": self.trade_date,
        }


@dataclass(frozen=True, slots=True)
class CoordinatorSnapshot:
    """Transport-independent view of the App lifecycle state."""

    current_symbol: str | None
    mode: AppMode
    session_generation: int
    live_session: SessionIdentity | None
    replay_session: SessionIdentity | None

    @property
    def visible_session(self) -> SessionIdentity | None:
        if self.mode is AppMode.REPLAY:
            return self.replay_session
        return self.live_session

    def to_dict(self) -> dict[str, object]:
        visible = self.visible_session
        return {
            "current_symbol": self.current_symbol,
            "mode": self.mode.value,
            "session_generation": self.session_generation,
            "live_session": (
                None if self.live_session is None else self.live_session.to_dict()
            ),
            "replay_session": (
                None if self.replay_session is None else self.replay_session.to_dict()
            ),
            "visible_session": None if visible is None else visible.to_dict(),
        }


@dataclass(slots=True)
class _ManagedSession:
    spec: SessionSpec
    port: SessionPort

    @property
    def identity(self) -> SessionIdentity:
        return SessionIdentity.from_spec(self.spec)


SessionIdFactory = Callable[[SessionType, int], str]


def _random_session_id(session_type: SessionType, generation: int) -> str:
    return f"{session_type.value}-{generation}-{uuid4().hex}"


class AppCoordinator:
    """Runtime authority for the current stock, mode, and Session generations.

    A generation is consumed for every attempted concrete Session creation.
    Replacements are created outside the state lock before the active state is
    changed, so a slow factory cannot block result acceptance and a factory
    failure leaves the previous Session usable. A lifecycle revision prevents
    a candidate created across a concurrent transition from being installed.
    Retiring a Session removes it from the acceptance boundary before calling
    the concrete port outside the state lock, so late work can never become
    current again. Failed retirements are retained and retried by
    :meth:`retire_all`.
    """

    def __init__(
        self,
        session_factory: SessionFactoryPort,
        *,
        session_id_factory: SessionIdFactory = _random_session_id,
    ) -> None:
        self._session_factory = session_factory
        self._session_id_factory = session_id_factory
        self._lock = RLock()
        self._current_symbol: str | None = None
        self._mode = AppMode.LIVE
        self._generation = 0
        self._revision = 0
        self._live: _ManagedSession | None = None
        self._replay: _ManagedSession | None = None
        self._pending_retirements: list[_ManagedSession] = []

    @property
    def snapshot(self) -> CoordinatorSnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def select_symbol(self, symbol: str) -> CoordinatorSnapshot:
        """Select the App stock and create its background Live Session."""

        resolved_symbol = _validate_symbol(symbol)
        with self._lock:
            if resolved_symbol == self._current_symbol:
                return self._snapshot_unlocked()
            expected_revision = self._revision
            generation = self._reserve_generation_unlocked()

        replacement = self._create_session(
            SessionType.LIVE,
            resolved_symbol,
            generation=generation,
            trade_date=None,
        )
        retired: tuple[_ManagedSession | None, ...] = ()
        prior_symbol: str | None = None
        prior_live: _ManagedSession | None = None
        prior_replay: _ManagedSession | None = None
        with self._lock:
            if self._revision != expected_revision:
                conflict = True
            else:
                conflict = False
                prior_symbol = self._current_symbol
                prior_live = self._live
                prior_replay = self._replay
                retired = (self._detach_replay(), self._detach_live())
                self._current_symbol = resolved_symbol
                self._live = replacement
                self._revision += 1
                snapshot = self._snapshot_unlocked()

        if conflict:
            self._retire_sessions((replacement,))
            raise CoordinatorStateError(
                "App state changed while creating the Live Session"
            )

        try:
            self._activate_session(replacement)
        except Exception as exc:
            cleanup: tuple[_ManagedSession | None, ...] = ()
            with self._lock:
                if self._live is replacement:
                    self._live = prior_live
                    if self._current_symbol == resolved_symbol:
                        self._current_symbol = prior_symbol

                    removed_replay: _ManagedSession | None = None
                    if self._replay is not None:
                        replay_symbol = self._replay.spec.symbol
                        current_symbol = self._current_symbol
                        replay_matches_symbol = (
                            current_symbol is not None
                            and replay_symbol == current_symbol
                        )
                        if self._mode is AppMode.LIVE or not replay_matches_symbol:
                            removed_replay = self._detach_replay()

                    restored_prior_replay = False
                    if (
                        self._replay is None
                        and prior_replay is not None
                        and self._mode is AppMode.REPLAY
                        and self._current_symbol == prior_symbol
                    ):
                        self._replay = prior_replay
                        restored_prior_replay = True

                    self._revision += 1
                    cleanup = (
                        replacement,
                        removed_replay,
                        None if restored_prior_replay else prior_replay,
                    )
            self._retire_sessions(self._unique_sessions(cleanup))
            raise CoordinatorStateError(
                "Live Session activation failed"
            ) from exc
        self._retire_sessions(retired)
        return snapshot

    def set_mode(self, mode: AppMode | str) -> CoordinatorSnapshot:
        """Switch the visible App mode without stopping background Live."""

        resolved_mode = _validate_mode(mode)
        with self._lock:
            if resolved_mode is self._mode:
                return self._snapshot_unlocked()
            retired: tuple[_ManagedSession | None, ...] = ()
            if resolved_mode is AppMode.LIVE:
                retired = (self._detach_replay(),)
            self._mode = resolved_mode
            self._revision += 1
            snapshot = self._snapshot_unlocked()

        self._retire_sessions(retired)
        return snapshot

    def begin_replay(self, trade_date: date | str) -> CoordinatorSnapshot:
        """Create a fresh one-shot Replay for the current stock and date."""

        resolved_date = _validate_trade_date(trade_date)
        with self._lock:
            if self._mode is not AppMode.REPLAY:
                raise CoordinatorStateError(
                    "begin_replay requires the App to be in replay mode"
                )
            if self._current_symbol is None:
                raise CoordinatorStateError(
                    "begin_replay requires a current symbol"
                )
            symbol = self._current_symbol
            expected_revision = self._revision
            generation = self._reserve_generation_unlocked()

        replacement = self._create_session(
            SessionType.REPLAY,
            symbol,
            generation=generation,
            trade_date=resolved_date,
        )
        retired: tuple[_ManagedSession | None, ...] = ()
        prior_replay: _ManagedSession | None = None
        with self._lock:
            if self._revision != expected_revision:
                conflict = True
            else:
                conflict = False
                prior_replay = self._replay
                retired = (self._detach_replay(),)
                self._replay = replacement
                self._revision += 1
                snapshot = self._snapshot_unlocked()

        if conflict:
            self._retire_sessions((replacement,))
            raise CoordinatorStateError(
                "App state changed while creating the Replay Session"
            )

        try:
            self._activate_session(replacement)
        except Exception as exc:
            owns_replacement = False
            with self._lock:
                if self._replay is replacement:
                    owns_replacement = True
                    self._replay = prior_replay
                    self._revision += 1
            if owns_replacement:
                self._retire_sessions((replacement,))
            raise CoordinatorStateError(
                "Replay Session activation failed"
            ) from exc
        self._retire_sessions(retired)
        return snapshot

    def retry_live(self) -> CoordinatorSnapshot:
        """Retire and rebuild Live for the same stock with a new generation."""

        with self._lock:
            if self._current_symbol is None:
                raise CoordinatorStateError(
                    "retry_live requires a current symbol"
                )
            symbol = self._current_symbol
            expected_revision = self._revision
            generation = self._reserve_generation_unlocked()

        replacement = self._create_session(
            SessionType.LIVE,
            symbol,
            generation=generation,
            trade_date=None,
        )
        retired: tuple[_ManagedSession | None, ...] = ()
        prior_live: _ManagedSession | None = None
        with self._lock:
            if self._revision != expected_revision:
                conflict = True
            else:
                conflict = False
                prior_live = self._live
                retired = (self._detach_live(),)
                self._live = replacement
                self._revision += 1
                snapshot = self._snapshot_unlocked()

        if conflict:
            self._retire_sessions((replacement,))
            raise CoordinatorStateError(
                "App state changed while rebuilding the Live Session"
            )

        try:
            self._activate_session(replacement)
        except Exception as exc:
            owns_replacement = False
            with self._lock:
                if self._live is replacement:
                    owns_replacement = True
                    self._live = prior_live
                    self._revision += 1
            if owns_replacement:
                self._retire_sessions((replacement,))
            raise CoordinatorStateError(
                "Live Session activation failed"
            ) from exc
        self._retire_sessions(retired)
        return snapshot

    def retire_all(self) -> CoordinatorSnapshot:
        """Retire all Sessions and retry prior retirement failures."""

        with self._lock:
            retired = (self._detach_replay(), self._detach_live())
            self._revision += 1
            snapshot = self._snapshot_unlocked()

        failures = self._retire_sessions(retired, include_pending=True)
        if failures:
            raise CoordinatorRetirementError(failures)
        return snapshot

    def accepts_result(
        self,
        *,
        session_type: SessionType | str,
        session_id: str,
        generation: int,
    ) -> bool:
        """Return whether a result still belongs to an active Session."""

        try:
            resolved_type = SessionType(session_type)
        except (TypeError, ValueError):
            return False
        if (
            not isinstance(session_id, str)
            or not session_id
            or isinstance(generation, bool)
            or not isinstance(generation, int)
        ):
            return False

        with self._lock:
            managed = (
                self._live
                if resolved_type is SessionType.LIVE
                else self._replay
            )
            return (
                managed is not None
                and managed.spec.session_id == session_id
                and managed.spec.generation == generation
            )

    def is_visible_session(
        self,
        *,
        session_id: str,
        generation: int,
    ) -> bool:
        """Return whether the identity currently owns the visible workbench."""

        visible = self.snapshot.visible_session
        return (
            visible is not None
            and visible.session_id == session_id
            and visible.generation == generation
        )

    def _create_session(
        self,
        session_type: SessionType,
        symbol: str,
        *,
        generation: int,
        trade_date: str | None,
    ) -> _ManagedSession:
        session_id = self._session_id_factory(session_type, generation)
        if not isinstance(session_id, str) or not session_id:
            raise CoordinatorValidationError(
                "session_id_factory must return a non-empty string"
            )
        spec = SessionSpec(
            session_id=session_id,
            session_type=session_type,
            symbol=symbol,
            generation=generation,
            trade_date=trade_date,
        )
        port = (
            self._session_factory.create_live(spec)
            if session_type is SessionType.LIVE
            else self._session_factory.create_replay(spec)
        )
        if port is None or not callable(getattr(port, "retire", None)):
            raise TypeError("Session factory must return a SessionPort")
        return _ManagedSession(spec=spec, port=port)

    def _reserve_generation_unlocked(self) -> int:
        self._generation += 1
        return self._generation

    def _snapshot_unlocked(self) -> CoordinatorSnapshot:
        return CoordinatorSnapshot(
            current_symbol=self._current_symbol,
            mode=self._mode,
            session_generation=self._generation,
            live_session=None if self._live is None else self._live.identity,
            replay_session=None if self._replay is None else self._replay.identity,
        )

    @staticmethod
    def _activate_session(managed: _ManagedSession) -> None:
        activate = getattr(managed.port, "activate", None)
        if callable(activate):
            activate()

    def _detach_live(self) -> _ManagedSession | None:
        managed, self._live = self._live, None
        return managed

    def _detach_replay(self) -> _ManagedSession | None:
        managed, self._replay = self._replay, None
        return managed

    def _retire_sessions(
        self,
        sessions: tuple[_ManagedSession | None, ...],
        *,
        include_pending: bool = False,
    ) -> tuple[Exception, ...]:
        retired = [managed for managed in sessions if managed is not None]
        if include_pending:
            with self._lock:
                retired = [*self._pending_retirements, *retired]
                self._pending_retirements.clear()

        failures: list[tuple[_ManagedSession, Exception]] = []
        for managed in retired:
            try:
                managed.port.retire()
            except Exception as exc:
                failures.append((managed, exc))

        if failures:
            with self._lock:
                self._pending_retirements.extend(
                    managed for managed, _ in failures
                )
        return tuple(exc for _, exc in failures)

    @staticmethod
    def _unique_sessions(
        sessions: tuple[_ManagedSession | None, ...],
    ) -> tuple[_ManagedSession | None, ...]:
        unique: list[_ManagedSession | None] = []
        seen: set[int] = set()
        for managed in sessions:
            if managed is None:
                continue
            identity = id(managed)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(managed)
        return tuple(unique)


def _validate_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol):
        raise CoordinatorValidationError(
            "symbol must use canonical sh.###### or sz.######"
        )
    return symbol


def _validate_mode(mode: AppMode | str) -> AppMode:
    try:
        return AppMode(mode)
    except (TypeError, ValueError) as exc:
        raise CoordinatorValidationError("mode must be live or replay") from exc


def _validate_trade_date(value: date | str) -> str:
    if isinstance(value, datetime):
        raise CoordinatorValidationError("trade_date must be an ISO date")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise CoordinatorValidationError("trade_date must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CoordinatorValidationError(
            "trade_date must be an ISO date"
        ) from exc
