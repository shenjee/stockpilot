"""Application service for startup restoration and confirmed preference saves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .models import PreferenceSnapshot, PreferenceValues


class PreferencePersistenceError(RuntimeError):
    """A stable persistence failure suitable for an app error adapter."""


class PreferencesReadOnlyError(PreferencePersistenceError):
    """The App database is readable but preference changes are disabled."""


@dataclass(frozen=True, slots=True)
class PreferenceCapability:
    readable: bool
    writable: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PreferenceRestoreResult:
    snapshot: PreferenceSnapshot
    capability: PreferenceCapability


class PreferenceRepository(Protocol):
    @property
    def capability(self) -> PreferenceCapability: ...

    def load(self) -> PreferenceSnapshot: ...

    def save(self, preferences: PreferenceValues) -> PreferenceSnapshot: ...


class PreferenceService:
    """Keeps persistence subordinate to React's current runtime UI state."""

    def __init__(self, repository: PreferenceRepository) -> None:
        self._repository = repository

    @property
    def capability(self) -> PreferenceCapability:
        return self._repository.capability

    def restore_for_startup(self) -> PreferenceRestoreResult:
        """Return the last confirmed copy, or first-run defaults."""

        return PreferenceRestoreResult(
            snapshot=self._repository.load(),
            capability=self._repository.capability,
        )

    def save(
        self, preferences: PreferenceValues | Mapping[str, Any]
    ) -> PreferenceSnapshot:
        values = (
            preferences
            if isinstance(preferences, PreferenceValues)
            else PreferenceValues.from_mapping(preferences)
        )
        if not self._repository.capability.writable:
            reason = self._repository.capability.reason or "App database is read-only"
            raise PreferencesReadOnlyError(reason)
        return self._repository.save(values)
