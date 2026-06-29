from __future__ import annotations

from typing import Dict, List

from ui_text import _display_timestamp, _format_timeframe, _t


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
        not_ready_signals = list(item.get("not_ready_signals", []) or [])
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
                _t(language, "signal_timeline_col_not_ready"): (
                    " | ".join(
                        f"{sig['signal_name']}({_t(language, 'signal_status_' + sig['status'])})"
                        for sig in not_ready_signals
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
            _t(
                language,
                "current_bar_signal_col_status",
            ): _t(
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


def _format_pivot_zone_count(result: object) -> str:
    meta = getattr(result, "meta", {}) or {}
    mapping = meta.get("mapping", {}) if isinstance(meta, dict) else {}
    fallback_total = len(list(getattr(result, "pivot_zones", []) or []))
    total = int(mapping.get("pivot_zone_count", fallback_total))
    stroke_count = int(mapping.get("stroke_pivot_zone_count", total))
    segment_count = int(mapping.get("segment_pivot_zone_count", 0))
    return f"{total} (stroke: {stroke_count}, segment: {segment_count})"


def _build_overview_card_rows(
    result: object,
    chart_rows: List[Dict[str, object]],
    current_bar_payload: Dict[str, object],
    language: str,
) -> List[Dict[str, object]]:
    meta = dict(current_bar_payload.get("meta", {}) or {})
    latest_row = dict(chart_rows[-1]) if chart_rows else {}
    candidate_count = len(list(getattr(result, "candidate_buy_points", []) or [])) + len(
        list(getattr(result, "candidate_sell_points", []) or [])
    )
    return [
        {
            _t(language, "overview_col_field"): _t(language, "overview_field_timeframe"),
            _t(language, "overview_col_value"): str(_format_timeframe(str(getattr(result, "timeframe", "")), language)),
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
            _t(language, "overview_col_value"): _format_pivot_zone_count(result),
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
