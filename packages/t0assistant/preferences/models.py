"""Validated, transport-independent T+0 application preferences."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


_SYMBOL_PATTERN = re.compile(r"^(sh|sz)\.[0-9]{6}$")
_CHART_SPLITS = frozenset({"64_36", "50_50"})


class PreferenceValidationError(ValueError):
    """A stable field-level validation failure."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PreferenceValidationError(field_name, "must be a boolean")
    return value


def _require_exact_keys(
    payload: Mapping[str, Any], expected: set[str], field_name: str
) -> None:
    if set(payload) != expected:
        expected_text = ", ".join(sorted(expected))
        raise PreferenceValidationError(
            field_name, f"must contain exactly: {expected_text}"
        )


@dataclass(frozen=True, slots=True)
class LayoutPreference:
    chart_split: str = "64_36"
    show_intraday: bool = True

    def __post_init__(self) -> None:
        if self.chart_split not in _CHART_SPLITS:
            raise PreferenceValidationError(
                "layout.chart_split", "must be one of: 64_36, 50_50"
            )
        _boolean(self.show_intraday, "layout.show_intraday")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> LayoutPreference:
        if not isinstance(payload, Mapping):
            raise PreferenceValidationError("layout", "must be an object")
        _require_exact_keys(
            payload, {"chart_split", "show_intraday"}, "layout"
        )
        return cls(
            chart_split=payload.get("chart_split"),
            show_intraday=_boolean(
                payload.get("show_intraday"), "layout.show_intraday"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_split": self.chart_split,
            "show_intraday": self.show_intraday,
        }


@dataclass(frozen=True, slots=True)
class LayerPreference:
    ma5: bool = False
    ma10: bool = False
    ma20: bool = False
    ma30: bool = False
    ma60: bool = False
    strokes: bool = True
    pivot_zones: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "ma5",
            "ma10",
            "ma20",
            "ma30",
            "ma60",
            "strokes",
            "pivot_zones",
        ):
            _boolean(getattr(self, field_name), f"layers.{field_name}")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> LayerPreference:
        if not isinstance(payload, Mapping):
            raise PreferenceValidationError("layers", "must be an object")
        field_names = {
            "ma5",
            "ma10",
            "ma20",
            "ma30",
            "ma60",
            "strokes",
            "pivot_zones",
        }
        _require_exact_keys(payload, field_names, "layers")
        values = {}
        for field_name in field_names:
            values[field_name] = _boolean(
                payload.get(field_name), f"layers.{field_name}"
            )
        return cls(**values)

    def to_dict(self) -> dict[str, bool]:
        return {
            "ma5": self.ma5,
            "ma10": self.ma10,
            "ma20": self.ma20,
            "ma30": self.ma30,
            "ma60": self.ma60,
            "strokes": self.strokes,
            "pivot_zones": self.pivot_zones,
        }


@dataclass(frozen=True, slots=True)
class PreferenceValues:
    """The persisted copy described by the frozen ``t0_app_v1`` contract."""

    last_symbol: str | None = None
    layout: LayoutPreference = field(default_factory=LayoutPreference)
    layers: LayerPreference = field(default_factory=LayerPreference)

    def __post_init__(self) -> None:
        if self.last_symbol is not None and (
            not isinstance(self.last_symbol, str)
            or not _SYMBOL_PATTERN.fullmatch(self.last_symbol)
        ):
            raise PreferenceValidationError(
                "last_symbol", "must use sh.###### or sz.######, or be null"
            )
        if not isinstance(self.layout, LayoutPreference):
            raise PreferenceValidationError("layout", "must be a LayoutPreference")
        if not isinstance(self.layers, LayerPreference):
            raise PreferenceValidationError("layers", "must be a LayerPreference")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> PreferenceValues:
        if not isinstance(payload, Mapping):
            raise PreferenceValidationError("preferences", "must be an object")
        expected = {"last_symbol", "layout", "layers"}
        _require_exact_keys(payload, expected, "preferences")
        return cls(
            last_symbol=payload["last_symbol"],
            layout=LayoutPreference.from_mapping(payload["layout"]),
            layers=LayerPreference.from_mapping(payload["layers"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_symbol": self.last_symbol,
            "layout": self.layout.to_dict(),
            "layers": self.layers.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PreferenceSnapshot:
    preference_revision: int
    preferences: PreferenceValues

    def __post_init__(self) -> None:
        if (
            isinstance(self.preference_revision, bool)
            or not isinstance(self.preference_revision, int)
            or self.preference_revision < 0
        ):
            raise PreferenceValidationError(
                "preference_revision", "must be a non-negative integer"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preference_revision": self.preference_revision,
            "preferences": self.preferences.to_dict(),
        }
