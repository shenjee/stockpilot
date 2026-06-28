from __future__ import annotations

from typing import Dict


def _build_debug_payload(result: object) -> Dict[str, object]:
    return {
        "engine_probe": result.meta.get("engine_probe", {}),
        "mapping": result.meta.get("mapping", {}),
        "rendering": {"count_plot_primitives": len(result.plot_primitives)},
        "engine_assumptions": result.meta.get("engine_assumptions", {}),
        "signals": result.meta.get("signals", {}),
    }


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
                "not_ready_signals": [],
                "events": [],
            },
        )
        values = dict(getattr(snapshot, "values", {}) or {})
        active_signals = dict(getattr(snapshot, "active_signals", {}) or {})
        statuses = dict(getattr(snapshot, "statuses", {}) or {})
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
        row["not_ready_signals"] = [
            {
                "signal_key": signal_key,
                "signal_name": signal_names.get(signal_key, signal_key),
                "status": status,
            }
            for signal_key, status in sorted(statuses.items())
            if status in ("not_ready", "error")
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
                "not_ready_signals": [],
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
