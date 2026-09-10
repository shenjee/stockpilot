"""Candidate point interpretation layer for chantheory.

This module converts signal evaluations into candidate buy/sell point events
and derives the latest candidate points from signal and structure context.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .schema import (
    CandidatePoint,
    CandidatePointEvent,
    PivotZone,
    Stroke,
)


def build_candidate_point_events(signal_evaluations: Sequence[Mapping[str, Any]]) -> List[CandidatePointEvent]:
    """Convert signal evaluations into candidate point events (triggered / switched / invalidated)."""
    by_signal: Dict[str, List[Mapping[str, Any]]] = {}
    for evaluation in signal_evaluations:
        by_signal.setdefault(str(evaluation["signal_key"]), []).append(evaluation)

    events: List[CandidatePointEvent] = []
    for signal_key, items in by_signal.items():
        previous_mapping: Tuple[str, str] | None = None
        previous_status: str | None = None
        # Track the last *known-good* mapping — the most recent mapping
        # derived from an evaluation whose status is neither "error" nor
        # "not_ready". When a signal recovers from an error/not_ready
        # state, we compare against this (not against the error bar) so
        # that recovery to the same value does not produce a spurious
        # "invalidated" → "triggered" pair (#175 P1 fix).
        last_known_good_mapping: Tuple[str, str] | None = None
        for item in items:
            current_mapping = _map_signal_evaluation_to_candidate_point(item)
            current_status = str(item.get("status") or ("active" if item.get("active") else "inactive"))
            event_type = ""
            point_type = ""

            # Resolve the comparison baseline. When the previous bar was
            # an error/not_ready, use last_known_good_mapping so that
            # recovery to the same value does not produce spurious events.
            if previous_status in ("error", "not_ready"):
                baseline_mapping = last_known_good_mapping
            else:
                baseline_mapping = previous_mapping

            if baseline_mapping is None and current_mapping is not None:
                event_type = "triggered"
                _, point_type = current_mapping
            elif baseline_mapping is not None and current_mapping is None:
                # Only emit "invalidated" when the signal genuinely
                # transitioned to inactive. An error or not_ready status
                # means evaluation failed — the signal did not actually
                # deactivate (#175 P1 fix).
                if current_status in ("error", "not_ready"):
                    event_type = ""
                else:
                    event_type = "invalidated"
                    point_type = baseline_mapping[1]
            elif baseline_mapping is not None and current_mapping is not None:
                _, previous_point_type = baseline_mapping
                _, current_point_type = current_mapping
                if previous_point_type != current_point_type:
                    event_type = "switched"
                    point_type = current_point_type

            if event_type:
                previous_point_type = baseline_mapping[1] if baseline_mapping is not None else ""
                events.append(
                    CandidatePointEvent(
                        id=f"candidate_point_event_{signal_key}_{item['bar_index']}_{event_type}",
                        point_type=point_type,
                        event_type=event_type,
                        timestamp=str(item["timestamp"]),
                        bar_index=int(item["bar_index"]),
                        active=current_mapping is not None,
                        reference_id=str(item["reference_id"]),
                        price=float(item["price"]) if item.get("price") is not None else None,
                        reason=str(item["value"]),
                        meta={
                            "signal_key": signal_key,
                            "signal_name": str(item["signal_name"]),
                            "previous_point_type": previous_point_type,
                            "previous_value": str(baseline_mapping[0]) if baseline_mapping is not None else "",
                            "direction": item.get("direction", ""),
                        },
                    )
                )

            previous_mapping = (str(item["value"]), current_mapping[1]) if current_mapping is not None else None
            previous_status = current_status
            if current_status not in ("error", "not_ready"):
                last_known_good_mapping = previous_mapping

    events.sort(key=lambda item: (item.bar_index, item.point_type, item.event_type))
    return events


def build_candidate_points(
    strokes: Sequence[Stroke],
    pivot_zones: Sequence[PivotZone],
    candidate_point_events: Sequence[CandidatePointEvent],
) -> Tuple[List[CandidatePoint], List[CandidatePoint]]:
    """Derive the latest candidate buy/sell points from signal events and structure context."""
    buy_points: List[CandidatePoint] = []
    sell_points: List[CandidatePoint] = []
    if not strokes:
        return buy_points, sell_points

    last_stroke = strokes[-1]
    for event in candidate_point_events:
        if event.event_type not in ("triggered", "switched"):
            continue

        point = CandidatePoint(
            id=f"{event.point_type}_{event.timestamp}",
            point_type=event.point_type,
            timestamp=event.timestamp,
            price=event.price,
            reference_id=event.reference_id,
            confirmed=False,
            reason=event.reason,
            meta={
                "direction": event.meta.get("direction", ""),
                "signal_scope": "cxt_signal",
                "signal_name": event.meta.get("signal_name", ""),
                "signal_key": event.meta.get("signal_key", ""),
                "signal_value": event.reason,
                "signal_version": str(event.meta.get("signal_name", "")).rsplit("_", 1)[-1]
                if "_" in str(event.meta.get("signal_name", ""))
                else "",
            },
        )
        if "buy" in event.point_type:
            buy_points.append(point)
        else:
            sell_points.append(point)

    if not pivot_zones:
        return buy_points, sell_points

    last_zone = pivot_zones[-1]
    if last_zone.low <= last_stroke.end_price <= last_zone.high:
        if last_stroke.direction not in {"up", "down"}:
            return buy_points, sell_points
        collection = buy_points if last_stroke.direction == "down" else sell_points
        point_type = "structure_buy_candidate" if last_stroke.direction == "down" else "structure_sell_candidate"
        collection.append(
            CandidatePoint(
                id=f"{point_type}_{last_stroke.end_timestamp}",
                point_type=point_type,
                timestamp=last_stroke.end_timestamp,
                price=last_stroke.end_price,
                reference_id=last_zone.id,
                confirmed=False,
                reason="The latest confirmed stroke ends inside the active pivot zone range.",
                meta={
                    "direction": last_stroke.direction,
                    "signal_scope": "structure_candidate_only",
                },
            )
        )

    return buy_points, sell_points


def _map_signal_evaluation_to_candidate_point(evaluation: Mapping[str, Any]) -> Tuple[str, str] | None:
    """Map a signal evaluation to a (side, point_type) tuple, or None if inactive."""
    if not bool(evaluation.get("active")):
        return None

    signal_name = str(evaluation.get("signal_name", ""))
    value = str(evaluation.get("value", ""))
    if signal_name.startswith("cxt_first_buy_") and value.startswith("一买"):
        return ("buy", "first_buy")
    if signal_name.startswith("cxt_first_sell_") and value.startswith("一卖"):
        return ("sell", "first_sell")
    if signal_name.startswith("cxt_second_bs_") and "二买" in value:
        return ("buy", "second_buy")
    if signal_name.startswith("cxt_second_bs_") and "二卖" in value:
        return ("sell", "second_sell")
    if signal_name.startswith("cxt_third_bs_") and "三买" in value:
        return ("buy", "third_buy")
    if signal_name.startswith("cxt_third_bs_") and "三卖" in value:
        return ("sell", "third_sell")
    return None
