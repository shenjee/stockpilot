from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List

import streamlit as st
import streamlit.components.v1 as components

_WIDGET_PATH = Path(__file__).parent / "chan_chart_widget"
chan_chart_widget = components.declare_component("chan_chart_widget", path=str(_WIDGET_PATH))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "skills" / "china-stock-analysis" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from charts.axis_policy import (  # noqa: E402
    build_daily_rangebreaks,
    build_intraday_date_ticks,
    build_x_axis_range,
    build_y_axis_range,
    is_minute_timeframe,
)
from charts.figure_builder import build_figure  # noqa: E402
from services.analysis_service import run_multi_timeframe_analysis  # noqa: E402
from services.market_service import fetch_rows_for_timeframes, probe_market_suggestions  # noqa: E402
from ui_text import (  # noqa: E402
    SUPPORTED_LANGUAGES,
    _build_display_summary,
    _display_timestamp,
    _format_alert_message,
    _format_severity,
    _format_timeframe,
    _format_warning_message,
    _frontend_template,
    _layer_label,
    _page_title,
    _sidebar_section_title,
    _t,
)


DEFAULT_END = date.today()
DEFAULT_START = DEFAULT_END - timedelta(days=240)
TIMEFRAME_OPTIONS = ("1m", "5m", "30m", "60m", "day")
DEFAULT_TIMEFRAME = "day"
MULTI_TIMEFRAME_LINKS = {
    "1m": ("1m", "5m", "30m"),
    "5m": ("5m", "30m", "day"),
    "15m": ("15m", "60m", "day"),
    "30m": ("30m", "60m", "day"),
    "60m": ("60m", "day", "week"),
    "day": ("day", "week", "month"),
}
LAYER_KEYS = (
    "fractals",
    "strokes",
    "segments",
    "pivot_zones",
    "divergences",
    "alerts",
    "candidate_points",
    "volume_panel",
    "macd_panel",
)
DEFAULT_X_WINDOW = 90
Y_ZOOM_STEP = 1.2
MIN_Y_ZOOM = 0.45
MAX_Y_ZOOM = 3.0

_fetch_rows_for_timeframes = fetch_rows_for_timeframes
_probe_market_suggestions = probe_market_suggestions
_build_figure = build_figure
_is_minute_timeframe = is_minute_timeframe
_build_x_axis_range = build_x_axis_range
_build_intraday_date_ticks = build_intraday_date_ticks
_build_y_axis_range = build_y_axis_range
_build_daily_rangebreaks = build_daily_rangebreaks
_run_multi_timeframe_analysis = run_multi_timeframe_analysis


def _ordered_rows(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted((dict(item) for item in rows), key=lambda item: str(item["date"]))


def _build_chart_key(
    symbol: str,
    market: str,
    timeframe: str,
    start_date: date,
    end_date: date,
    rows: List[Dict[str, object]],
) -> str:
    first_date = str(rows[0]["date"]) if rows else ""
    last_date = str(rows[-1]["date"]) if rows else ""
    return "|".join([
        symbol,
        market,
        timeframe,
        start_date.isoformat(),
        end_date.isoformat(),
        str(len(rows)),
        first_date,
        last_date,
    ])


def _build_linked_timeframes(timeframe: str) -> List[str]:
    linked = MULTI_TIMEFRAME_LINKS.get(timeframe, (timeframe,))
    ordered: List[str] = []
    for item in linked:
        if item not in ordered:
            ordered.append(item)
    if timeframe not in ordered:
        ordered.insert(0, timeframe)
    return ordered


def _extract_primary_analysis(multi_result: object, timeframe: str) -> object | None:
    for level in list(getattr(multi_result, "levels", []) or []):
        if getattr(level, "timeframe", "") == timeframe:
            return getattr(level, "analysis", None)
    return None


def _build_debug_payload(result: object, multi_result: object | None = None) -> Dict[str, object]:
    payload = {
        "engine_probe": result.meta.get("engine_probe", {}),
        "mapping": result.meta.get("mapping", {}),
        "rendering": {"count_plot_primitives": len(result.plot_primitives)},
        "engine_assumptions": result.meta.get("engine_assumptions", {}),
        "signals": result.meta.get("signals", {}),
    }
    if multi_result is not None:
        payload["multi_timeframe"] = getattr(multi_result, "meta", {})
    return payload


def _build_signal_timeline_payload(result: object) -> Dict[str, object]:
    signal_series = list(getattr(result, "signal_series", []) or [])
    signal_events = list(getattr(result, "signal_events", []) or [])
    signal_snapshots = list(getattr(result, "signal_snapshots", []) or [])
    signal_names: Dict[str, str] = {}
    rows_by_key: Dict[tuple[int, str, str], Dict[str, object]] = {}

    for series in signal_series:
        signal_key = str(getattr(series, "signal_key", ""))
        if signal_key:
            signal_names[signal_key] = str(getattr(series, "signal_name", signal_key))

    for snapshot in signal_snapshots:
        timestamp = str(getattr(snapshot, "timestamp", ""))
        bar_index = int(getattr(snapshot, "bar_index", 0))
        reference_id = str(getattr(snapshot, "reference_id", ""))
        meta = dict(getattr(snapshot, "meta", {}) or {})
        for signal_key, signal_name in dict(meta.get("signal_names", {}) or {}).items():
            signal_names.setdefault(str(signal_key), str(signal_name))

        row = rows_by_key.setdefault(
            (bar_index, timestamp, reference_id),
            {
                "timestamp": timestamp,
                "bar_index": bar_index,
                "reference_id": reference_id,
                "price": getattr(snapshot, "price", None),
                "values": [],
                "active_signals": [],
                "events": [],
            },
        )
        values = dict(getattr(snapshot, "values", {}) or {})
        active_signals = dict(getattr(snapshot, "active_signals", {}) or {})
        row["values"] = [
            {
                "signal_key": signal_key,
                "signal_name": signal_names.get(signal_key, signal_key),
                "value": value,
                "active": signal_key in active_signals,
            }
            for signal_key, value in sorted(values.items())
        ]
        row["active_signals"] = [
            {
                "signal_key": signal_key,
                "signal_name": signal_names.get(signal_key, signal_key),
                "value": value,
            }
            for signal_key, value in sorted(active_signals.items())
        ]

    for event in signal_events:
        signal_key = str(getattr(event, "signal_key", ""))
        signal_name = str(getattr(event, "signal_name", signal_key))
        if signal_key:
            signal_names.setdefault(signal_key, signal_name)
        timestamp = str(getattr(event, "timestamp", ""))
        bar_index = int(getattr(event, "bar_index", 0))
        reference_id = str(getattr(event, "reference_id", ""))
        row = rows_by_key.setdefault(
            (bar_index, timestamp, reference_id),
            {
                "timestamp": timestamp,
                "bar_index": bar_index,
                "reference_id": reference_id,
                "price": getattr(event, "price", None),
                "values": [],
                "active_signals": [],
                "events": [],
            },
        )
        row["events"].append(
            {
                "signal_key": signal_key,
                "signal_name": signal_name,
                "event_type": str(getattr(event, "event_type", "")),
                "value": str(getattr(event, "value", "")),
                "active": bool(getattr(event, "active", False)),
                "previous_value": str(dict(getattr(event, "meta", {}) or {}).get("previous_value", "")),
            }
        )

    rows = sorted(rows_by_key.values(), key=lambda item: (int(item["bar_index"]), str(item["reference_id"])))
    for item in rows:
        item["events"] = sorted(
            list(item["events"]),
            key=lambda event: (str(event["signal_key"]), str(event["event_type"])),
        )
        if item["price"] is not None:
            item["price"] = float(item["price"])

    return {
        "meta": {
            "series_count": len(signal_series),
            "snapshot_count": len(signal_snapshots),
            "event_count": len(signal_events),
            "row_count": len(rows),
            "latest_timestamp": rows[-1]["timestamp"] if rows else "",
        },
        "rows": rows,
    }


def _format_signal_timeline_event(event: Dict[str, object], language: str) -> str:
    signal_name = str(event.get("signal_name", event.get("signal_key", "")))
    event_type = _t(language, f"signal_event_type_{event.get('event_type', '')}")
    value = str(event.get("value", ""))
    previous_value = str(event.get("previous_value", ""))
    if previous_value and previous_value != value:
        return f"{signal_name} {event_type}: {previous_value} -> {value}"
    if value:
        return f"{signal_name} {event_type}: {value}"
    return f"{signal_name} {event_type}"


def _build_signal_timeline_table_rows(timeline_payload: Dict[str, object], language: str) -> List[Dict[str, object]]:
    display_rows: List[Dict[str, object]] = []
    for item in list(timeline_payload.get("rows", []) or []):
        active_signals = list(item.get("active_signals", []) or [])
        events = list(item.get("events", []) or [])
        display_rows.append(
            {
                _t(language, "signal_timeline_col_timestamp"): _display_timestamp(str(item.get("timestamp", ""))),
                _t(language, "signal_timeline_col_bar_index"): int(item.get("bar_index", 0)),
                _t(language, "signal_timeline_col_price"): item.get("price", ""),
                _t(language, "signal_timeline_col_active_signals"): (
                    " | ".join(
                        f"{signal['signal_name']}={signal['value']}"
                        for signal in active_signals
                    )
                    or _t(language, "signal_timeline_none")
                ),
                _t(language, "signal_timeline_col_events"): (
                    " | ".join(_format_signal_timeline_event(event, language) for event in events)
                    or _t(language, "signal_timeline_none")
                ),
                _t(language, "signal_timeline_col_reference"): str(item.get("reference_id", "")),
            }
        )
    return display_rows


def _build_current_bar_signal_payload(timeline_payload: Dict[str, object]) -> Dict[str, object]:
    rows = list(timeline_payload.get("rows", []) or [])
    current_row = dict(rows[-1]) if rows else {}
    values = list(current_row.get("values", []) or [])
    active_signal_keys = {str(item.get("signal_key", "")) for item in list(current_row.get("active_signals", []) or [])}
    signal_rows = [
        {
            "signal_key": str(item.get("signal_key", "")),
            "signal_name": str(item.get("signal_name", item.get("signal_key", ""))),
            "value": str(item.get("value", "")),
            "active": str(item.get("signal_key", "")) in active_signal_keys,
        }
        for item in values
    ]
    event_rows = [
        {
            "signal_key": str(item.get("signal_key", "")),
            "signal_name": str(item.get("signal_name", item.get("signal_key", ""))),
            "event_type": str(item.get("event_type", "")),
            "value": str(item.get("value", "")),
            "previous_value": str(item.get("previous_value", "")),
        }
        for item in list(current_row.get("events", []) or [])
    ]
    return {
        "meta": {
            "timestamp": str(current_row.get("timestamp", "")),
            "bar_index": int(current_row.get("bar_index", 0)) if current_row else 0,
            "price": current_row.get("price"),
            "reference_id": str(current_row.get("reference_id", "")),
            "signal_count": len(signal_rows),
            "active_signal_count": len(active_signal_keys),
            "event_count": len(event_rows),
        },
        "signals": signal_rows,
        "events": event_rows,
    }


def _build_current_bar_signal_summary_rows(current_payload: Dict[str, object], language: str) -> List[Dict[str, object]]:
    meta = dict(current_payload.get("meta", {}) or {})
    if not meta.get("timestamp"):
        return []
    return [
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_timestamp"),
            _t(language, "current_bar_summary_col_value"): str(_display_timestamp(str(meta.get("timestamp", "")))),
        },
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_bar_index"),
            _t(language, "current_bar_summary_col_value"): str(int(meta.get("bar_index", 0))),
        },
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_price"),
            _t(language, "current_bar_summary_col_value"): str(meta.get("price", _t(language, "signal_timeline_none"))),
        },
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_reference"),
            _t(language, "current_bar_summary_col_value"): str(meta.get("reference_id", "")) or _t(language, "signal_timeline_none"),
        },
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_active_count"),
            _t(language, "current_bar_summary_col_value"): str(int(meta.get("active_signal_count", 0))),
        },
        {
            _t(language, "current_bar_summary_col_field"): _t(language, "current_bar_summary_event_count"),
            _t(language, "current_bar_summary_col_value"): str(int(meta.get("event_count", 0))),
        },
    ]


def _build_current_bar_signal_table_rows(current_payload: Dict[str, object], language: str) -> List[Dict[str, object]]:
    return [
        {
            _t(language, "current_bar_signal_col_name"): str(item.get("signal_name", item.get("signal_key", ""))),
            _t(language, "current_bar_signal_col_key"): str(item.get("signal_key", "")),
            _t(language, "current_bar_signal_col_value"): str(item.get("value", "")),
            _t(language, "current_bar_signal_col_status"): _t(
                language,
                "current_bar_signal_status_active" if bool(item.get("active")) else "current_bar_signal_status_inactive",
            ),
        }
        for item in list(current_payload.get("signals", []) or [])
    ]


def _build_current_bar_event_table_rows(current_payload: Dict[str, object], language: str) -> List[Dict[str, object]]:
    return [
        {
            _t(language, "current_bar_event_col_name"): str(item.get("signal_name", item.get("signal_key", ""))),
            _t(language, "current_bar_event_col_type"): _t(language, f"signal_event_type_{item.get('event_type', '')}"),
            _t(language, "current_bar_event_col_change"): (
                f"{item.get('previous_value', '')} -> {item.get('value', '')}"
                if item.get("previous_value") and item.get("previous_value") != item.get("value")
                else str(item.get("value", ""))
            )
            or _t(language, "signal_timeline_none"),
        }
        for item in list(current_payload.get("events", []) or [])
    ]


def _build_overview_card_rows(
    result: object,
    chart_rows: List[Dict[str, object]],
    multi_result: object | None,
    current_bar_payload: Dict[str, object],
    language: str,
) -> List[Dict[str, object]]:
    meta = dict(current_bar_payload.get("meta", {}) or {})
    latest_row = dict(chart_rows[-1]) if chart_rows else {}
    linked_timeframes = [
        _format_timeframe(str(getattr(level, "timeframe", "")), language)
        for level in list(getattr(multi_result, "levels", []) or [])
        if getattr(level, "timeframe", "")
    ]
    candidate_count = len(list(getattr(result, "candidate_buy_points", []) or [])) + len(
        list(getattr(result, "candidate_sell_points", []) or [])
    )
    return [
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_timeframe"),
            _t(language, "overview_col_value"): str(_format_timeframe(str(getattr(result, "timeframe", "")), language)),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_linked_timeframes"),
            _t(language, "overview_col_value"): str(" | ".join(linked_timeframes) or _t(language, "signal_timeline_none")),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_latest_bar"),
            _t(language, "overview_col_value"): str(_display_timestamp(str(latest_row.get("date", meta.get("timestamp", ""))))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_latest_close"),
            _t(language, "overview_col_value"): str(latest_row.get("close", meta.get("price", _t(language, "signal_timeline_none")))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_bar_count"),
            _t(language, "overview_col_value"): str(len(chart_rows)),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_stroke_count"),
            _t(language, "overview_col_value"): str(len(list(getattr(result, "strokes", []) or []))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_segment_count"),
            _t(language, "overview_col_value"): str(len(list(getattr(result, "segments", []) or []))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_pivot_zone_count"),
            _t(language, "overview_col_value"): str(len(list(getattr(result, "pivot_zones", []) or []))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_signal_series_count"),
            _t(language, "overview_col_value"): str(len(list(getattr(result, "signal_series", []) or []))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_active_signal_count"),
            _t(language, "overview_col_value"): str(int(meta.get("active_signal_count", 0))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_warning_count"),
            _t(language, "overview_col_value"): str(len(list(getattr(result, "warnings", []) or []))),
        },
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_candidate_count"),
            _t(language, "overview_col_value"): str(candidate_count),
        },
    ]


def _build_multi_timeframe_payload(
    multi_result: object | None,
    rows_by_timeframe: Dict[str, List[Dict[str, object]]] | None = None,
) -> Dict[str, object]:
    levels = list(getattr(multi_result, "levels", []) or []) if multi_result is not None else []
    rows_by_timeframe = rows_by_timeframe or {}
    payload_levels: List[Dict[str, object]] = []
    higher_count = 0
    lower_count = 0
    for level in levels:
        timeframe = str(getattr(level, "timeframe", ""))
        role = str(getattr(level, "role", ""))
        if role == "higher":
            higher_count += 1
        elif role == "lower":
            lower_count += 1
        analysis = getattr(level, "analysis", None)
        level_rows = _ordered_rows(rows_by_timeframe.get(timeframe, []))
        latest_row = dict(level_rows[-1]) if level_rows else {}
        active_signal_count = 0
        signal_snapshots = list(getattr(analysis, "signal_snapshots", []) or []) if analysis is not None else []
        if signal_snapshots:
            active_signal_count = len(dict(getattr(signal_snapshots[-1], "active_signals", {}) or {}))
        payload_levels.append(
            {
                "timeframe": timeframe,
                "role": role,
                "bar_count": int(getattr(level, "bar_count", len(level_rows))),
                "latest_timestamp": str(latest_row.get("date", "")),
                "latest_close": latest_row.get("close"),
                "warning_count": len(list(getattr(analysis, "warnings", []) or [])) if analysis is not None else 0,
                "stroke_count": len(list(getattr(analysis, "strokes", []) or [])) if analysis is not None else 0,
                "segment_count": len(list(getattr(analysis, "segments", []) or [])) if analysis is not None else 0,
                "pivot_zone_count": len(list(getattr(analysis, "pivot_zones", []) or [])) if analysis is not None else 0,
                "signal_series_count": len(list(getattr(analysis, "signal_series", []) or [])) if analysis is not None else 0,
                "active_signal_count": active_signal_count,
                "candidate_count": (
                    len(list(getattr(analysis, "candidate_buy_points", []) or []))
                    + len(list(getattr(analysis, "candidate_sell_points", []) or []))
                )
                if analysis is not None
                else 0,
                "analysis": analysis,
                "rows": level_rows,
            }
        )
    return {
        "meta": {
            "base_timeframe": str(getattr(multi_result, "base_timeframe", "")) if multi_result is not None else "",
            "level_count": len(payload_levels),
            "higher_count": higher_count,
            "lower_count": lower_count,
        },
        "levels": payload_levels,
    }


def _build_multi_timeframe_card_rows(level_payload: Dict[str, object], language: str) -> List[Dict[str, object]]:
    return [
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_role"),
            _t(language, "multi_timeframe_card_value"): str(_t(
                language,
                f"multi_timeframe_role_{level_payload.get('role', '')}",
            )),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_latest_bar"),
            _t(language, "multi_timeframe_card_value"): str(_display_timestamp(str(level_payload.get("latest_timestamp", "")))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_latest_close"),
            _t(language, "multi_timeframe_card_value"): str(level_payload.get("latest_close", _t(language, "signal_timeline_none"))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_bar_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("bar_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_stroke_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("stroke_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_segment_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("segment_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_pivot_zone_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("pivot_zone_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_signal_series_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("signal_series_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_active_signal_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("active_signal_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_warning_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("warning_count", 0))),
        },
        {
            _t(language, "multi_timeframe_card_field"): _t(language, "multi_timeframe_field_candidate_count"),
            _t(language, "multi_timeframe_card_value"): str(int(level_payload.get("candidate_count", 0))),
        },
    ]


def main() -> None:
    if "language" not in st.session_state:
        st.session_state.language = "zh"
    if "chan_selected_timeframe" not in st.session_state:
        st.session_state.chan_selected_timeframe = DEFAULT_TIMEFRAME
    language = str(st.session_state.language)

    st.set_page_config(page_title=_t("zh", "page_title"), layout="wide")
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"] { display: none; }
        .block-container { padding-top: 0.75rem; padding-bottom: 2rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            min-height: 0px !important;
            height: 0px !important;
            margin-bottom: 4px !important;
            align-items: flex-start !important;
        }
        .sidebar-section-title { font-size: 1.1rem; font-weight: 700; line-height: 1.3; margin: 0.25rem 0 0.5rem 0; }
        .page-title { font-size: 1.2rem; font-weight: 700; line-height: 1.25; margin: 0 0 0.25rem 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(_page_title(_t(language, "page_title")), unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(_sidebar_section_title(_t(language, "inputs_header")), unsafe_allow_html=True)
        symbol = st.text_input(_t(language, "symbol_label"), value="000001")
        market = st.selectbox(_t(language, "market_label"), ["sz", "sh", "bj"], index=0)
        start_date = st.date_input(_t(language, "start_date_label"), value=DEFAULT_START)
        end_date = st.date_input(_t(language, "end_date_label"), value=DEFAULT_END)
        max_bi_num = st.number_input(_t(language, "max_bi_num_label"), min_value=10, max_value=200, value=50, step=10)
        min_bars = st.number_input(_t(language, "min_bars_label"), min_value=10, max_value=500, value=60, step=10)
        strict_validation = st.checkbox(_t(language, "strict_validation_label"), value=True)
        st.markdown(_sidebar_section_title(_t(language, "layers_header")), unsafe_allow_html=True)
        visibility = {layer: st.checkbox(_layer_label(layer, language), value=(layer != "segments")) for layer in LAYER_KEYS}
        st.markdown(_sidebar_section_title(_t(language, "display_header")), unsafe_allow_html=True)
        show_legend = st.checkbox(_t(language, "show_legend_label"), value=True)
        unified_hover = st.checkbox(_t(language, "crosshair_link_label"), value=True)
        run = st.button(_t(language, "run_button"), type="primary")
        st.selectbox(
            _t(language, "language_label"),
            list(SUPPORTED_LANGUAGES.keys()),
            format_func=lambda code: SUPPORTED_LANGUAGES[code],
            key="language",
        )

    timeframe = st.session_state.chan_selected_timeframe
    analysis_inputs = {
        "symbol": symbol.strip(),
        "market": market,
        "timeframe": timeframe,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "max_bi_num": int(max_bi_num),
        "min_bars": int(min_bars),
        "strict_validation": bool(strict_validation),
    }
    cached_inputs = st.session_state.get("chan_chart_inputs")
    should_analyze = bool(run) or (
        cached_inputs is not None
        and cached_inputs != analysis_inputs
        and "chan_chart_rows" in st.session_state
        and "chan_chart_result" in st.session_state
    )

    if should_analyze:
        linked_timeframes = _build_linked_timeframes(timeframe)
        rows_by_timeframe = _fetch_rows_for_timeframes(
            symbol=symbol.strip(),
            market=market,
            timeframes=linked_timeframes,
            start_date=start_date,
            end_date=end_date,
        )
        rows = rows_by_timeframe.get(timeframe, [])
        if not rows:
            st.session_state.pop("chan_chart_rows", None)
            st.session_state.pop("chan_chart_result", None)
            st.session_state.pop("chan_chart_multi_result", None)
            st.session_state.pop("chan_chart_rows_by_timeframe", None)
            st.session_state.pop("chan_chart_timeframe", None)
            st.session_state.pop("chan_chart_inputs", None)
            suggestions = _probe_market_suggestions(
                symbol=symbol.strip(),
                selected_market=market,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
            )
            if suggestions:
                suggestion_lines = ", ".join(f"{item['market']} ({item['count']} rows)" for item in suggestions)
                st.warning(_t(language, "no_data_market", symbol=symbol.strip(), market=market, suggestions=suggestion_lines))
            else:
                st.warning(_t(language, "no_data_selected"))
            return

        multi_result = _run_multi_timeframe_analysis(
            rows_by_timeframe=rows_by_timeframe,
            symbol=symbol.strip(),
            market=market,
            base_timeframe=timeframe,
            max_bi_num=int(max_bi_num),
            min_bars=int(min_bars),
            strict_validation=bool(strict_validation),
        )
        result = _extract_primary_analysis(multi_result, timeframe)
        if result is None:
            st.warning(_t(language, "no_multi_timeframe_base", timeframe=_format_timeframe(timeframe, language)))
            return
        st.session_state.chan_chart_rows = rows
        st.session_state.chan_chart_result = result
        st.session_state.chan_chart_multi_result = multi_result
        st.session_state.chan_chart_rows_by_timeframe = {
            key: _ordered_rows(value)
            for key, value in rows_by_timeframe.items()
        }
        st.session_state.chan_chart_timeframe = timeframe
        st.session_state.chan_chart_inputs = analysis_inputs
    elif "chan_chart_rows" in st.session_state and "chan_chart_result" in st.session_state:
        rows = st.session_state.chan_chart_rows
        result = st.session_state.chan_chart_result
        multi_result = st.session_state.get("chan_chart_multi_result")
        rows_by_timeframe = st.session_state.get("chan_chart_rows_by_timeframe", {timeframe: rows})
        timeframe = str(st.session_state.get("chan_chart_timeframe", timeframe))
    else:
        st.info(_t(language, "choose_inputs"))
        return

    chart_rows = _ordered_rows(rows)
    figure = _build_figure(
        rows=chart_rows,
        result_payload=result.to_dict(),
        visibility=visibility,
        timeframe=timeframe,
        language=language,
        x_window=min(DEFAULT_X_WINDOW, max(len(chart_rows), 1)),
        y_zoom=1.0,
        show_legend=show_legend,
        unified_hover=unified_hover,
    )

    row_count = len(chart_rows)
    x_steps = [n for n in [30, 60, 90, 120, 240, 360, 480, row_count] if n <= row_count]
    if row_count not in x_steps:
        x_steps.append(row_count)

    payload = {
        "figure": json.loads(figure.to_json()),
        "rows": chart_rows,
        "chartKey": _build_chart_key(
            symbol=symbol.strip(),
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            rows=chart_rows,
        ),
        "timeframes": [{"value": tf, "label": _format_timeframe(tf, language)} for tf in TIMEFRAME_OPTIONS],
        "activeTimeframe": timeframe,
        "useContinuousBarAxis": _is_minute_timeframe(timeframe),
        "xWindowSteps": x_steps,
        "defaultXWindow": min(DEFAULT_X_WINDOW, max(row_count, 1)),
        "defaultYZoom": 1.0,
        "yZoomStep": Y_ZOOM_STEP,
        "minYZoom": MIN_Y_ZOOM,
        "maxYZoom": MAX_Y_ZOOM,
        "text": {
            "xAxisLabel": _t(language, "x_axis_label"),
            "yAxisLabel": _t(language, "y_axis_label"),
            "zoomIn": _t(language, "zoom_in_label"),
            "zoomOut": _t(language, "zoom_out_label"),
            "xWindowCaption": _t(language, "x_window_caption", count="{count}"),
            "yZoomCaption": _frontend_template(language, "y_zoom_caption"),
            "pan": _t(language, "pan_label"),
            "reset": _t(language, "reset_label"),
            "fullscreen": _t(language, "fullscreen_label"),
        },
    }

    returned_timeframe = chan_chart_widget(payload=payload, key="chan_chart_widget_inst")
    if returned_timeframe and returned_timeframe != st.session_state.chan_selected_timeframe:
        st.session_state.chan_selected_timeframe = returned_timeframe
        st.rerun()

    timeline_payload = _build_signal_timeline_payload(result)
    current_bar_payload = _build_current_bar_signal_payload(timeline_payload)
    multi_timeframe_payload = _build_multi_timeframe_payload(multi_result, rows_by_timeframe=rows_by_timeframe)
    overview_rows = _build_overview_card_rows(
        result,
        chart_rows=chart_rows,
        multi_result=multi_result,
        current_bar_payload=current_bar_payload,
        language=language,
    )

    st.markdown("---")
    tab_struct, tab_summary, tab_multi, tab_warn, tab_timeline, tab_current_bar, tab_debug = st.tabs([
        _t(language, "tab_structure"),
        _t(language, "tab_summary"),
        _t(language, "tab_multi_timeframe"),
        _t(language, "tab_warnings"),
        _t(language, "tab_signal_timeline"),
        _t(language, "tab_current_bar_signals"),
        _t(language, "tab_debug"),
    ])

    with tab_struct:
        if result.structure_alerts:
            for alert in result.structure_alerts:
                st.write(f"- {_format_alert_message(alert.__dict__, language)}")
        else:
            st.write(_t(language, "no_alerts"))

    with tab_summary:
        meta = multi_timeframe_payload["meta"]
        st.caption(
            _t(
                language,
                "overview_summary",
                timeframe=_format_timeframe(timeframe, language),
                level_count=int(meta.get("level_count", 0)),
                timeframes=" | ".join(
                    _format_timeframe(str(level["timeframe"]), language)
                    for level in list(multi_timeframe_payload.get("levels", []) or [])
                )
                or _t(language, "signal_timeline_none"),
            )
        )
        st.dataframe(overview_rows, width="stretch")
        for line in _build_display_summary(result, language):
            st.write(f"- {line}")

    with tab_multi:
        levels = list(multi_timeframe_payload.get("levels", []) or [])
        meta = dict(multi_timeframe_payload.get("meta", {}) or {})
        if levels:
            st.caption(
                _t(
                    language,
                    "multi_timeframe_summary",
                    level_count=int(meta.get("level_count", 0)),
                    higher_count=int(meta.get("higher_count", 0)),
                    lower_count=int(meta.get("lower_count", 0)),
                )
            )
            level_tabs = st.tabs([
                _format_timeframe(str(level["timeframe"]), language)
                for level in levels
            ])
            for level_tab, level_payload in zip(level_tabs, levels):
                with level_tab:
                    level_analysis = level_payload.get("analysis")
                    level_rows = list(level_payload.get("rows", []) or [])
                    st.caption(
                        _t(
                            language,
                            "multi_timeframe_level_summary",
                            role=_t(language, f"multi_timeframe_role_{level_payload.get('role', '')}"),
                            bar_count=int(level_payload.get("bar_count", 0)),
                            warning_count=int(level_payload.get("warning_count", 0)),
                            signal_count=int(level_payload.get("signal_series_count", 0)),
                        )
                    )
                    if level_analysis is not None and level_rows:
                        level_figure = _build_figure(
                            rows=level_rows,
                            result_payload=level_analysis.to_dict(),
                            visibility=visibility,
                            timeframe=str(level_payload.get("timeframe", "")),
                            language=language,
                            x_window=min(DEFAULT_X_WINDOW, max(len(level_rows), 1)),
                            y_zoom=1.0,
                            show_legend=show_legend,
                            unified_hover=unified_hover,
                        )
                        st.plotly_chart(level_figure, width="stretch", key=f"multi_tf_chart_{level_payload.get('timeframe', '')}")
                    st.dataframe(_build_multi_timeframe_card_rows(level_payload, language), width="stretch")
                    if level_analysis is not None:
                        for line in _build_display_summary(level_analysis, language):
                            st.write(f"- {line}")
        else:
            st.write(_t(language, "multi_timeframe_empty"))

    with tab_warn:
        if result.warnings:
            for item in result.warnings:
                st.write(f"- [{_format_severity(item.severity, language)}] {item.warning_code}: {_format_warning_message(item, language)}")
        else:
            st.write(_t(language, "no_warnings"))

    with tab_timeline:
        if timeline_payload["rows"]:
            meta = timeline_payload["meta"]
            st.caption(
                _t(
                    language,
                    "signal_timeline_summary",
                    snapshots=meta["snapshot_count"],
                    events=meta["event_count"],
                    series=meta["series_count"],
                )
            )
            st.dataframe(_build_signal_timeline_table_rows(timeline_payload, language), width="stretch")
            with st.expander(_t(language, "signal_timeline_json_header"), expanded=False):
                st.json(timeline_payload)
        else:
            st.write(_t(language, "no_signal_replay"))

    with tab_current_bar:
        summary_rows = _build_current_bar_signal_summary_rows(current_bar_payload, language)
        signal_rows = _build_current_bar_signal_table_rows(current_bar_payload, language)
        event_rows = _build_current_bar_event_table_rows(current_bar_payload, language)
        if summary_rows:
            meta = current_bar_payload["meta"]
            st.caption(
                _t(
                    language,
                    "current_bar_signal_summary",
                    timestamp=_display_timestamp(str(meta.get("timestamp", ""))),
                    active_count=int(meta.get("active_signal_count", 0)),
                    event_count=int(meta.get("event_count", 0)),
                )
            )
            st.dataframe(summary_rows, width="stretch")
            st.dataframe(signal_rows, width="stretch")
            if event_rows:
                st.dataframe(event_rows, width="stretch")
            else:
                st.write(_t(language, "current_bar_no_events"))
            with st.expander(_t(language, "current_bar_json_header"), expanded=False):
                st.json(current_bar_payload)
        else:
            st.write(_t(language, "no_signal_replay"))

    with tab_debug:
        with st.expander(_t(language, "diagnostics_header"), expanded=False):
            st.json(_build_debug_payload(result, multi_result=multi_result))
        with st.expander(_t(language, "signals_header"), expanded=False):
            if result.signal_series or result.signal_events or result.signal_snapshots:
                st.json(
                    {
                        "signal_series": result.signal_series,
                        "signal_events": result.signal_events,
                        "signal_snapshots": result.signal_snapshots,
                    }
                )
            else:
                st.write(_t(language, "no_signal_replay"))
        with st.expander(_t(language, "candidate_replay_header"), expanded=False):
            if result.candidate_point_events:
                st.json({"candidate_point_events": result.candidate_point_events})
            else:
                st.write(_t(language, "no_candidate_replay"))
        with st.expander(_t(language, "multi_timeframe_header"), expanded=False):
            if multi_result is not None:
                st.json(multi_result.to_dict())
            else:
                st.write(_t(language, "no_multi_timeframe"))
        with st.expander(_t(language, "raw_json_header"), expanded=False):
            st.json(
                {
                    "analysis": result.to_dict(),
                    "multi_timeframe": multi_result.to_dict() if multi_result is not None else None,
                }
            )


if __name__ == "__main__":
    main()
