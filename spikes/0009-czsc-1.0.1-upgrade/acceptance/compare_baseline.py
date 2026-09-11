#!/usr/bin/env python3
"""Compare czsc 1.0.1 production analyze() against #173 frozen 0.10.12 baselines.

Also checks:
  - plot_primitives vs structure field consistency
  - multi-timeframe aggregation vs independent single-timeframe analyzes
  - first-bar third_bs status evidence (not_ready vs inactive)

Does NOT update formal fixtures. Writes artifacts under acceptance/artifacts/.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import (  # noqa: E402
    INPUTS_DIR,
    OUTPUTS_DIR,
    SCENARIOS,
    count_by_category,
    dump_json,
    ensure_artifacts_dir,
    environment_snapshot,
    git_sha,
    load_json,
    semantic_diff,
    sha256_file,
    signal_active_counts,
    strip_expected_migration_noise,
    structure_counts,
    utc_now,
)

from packages.chantheory import analyze, analyze_multi_timeframe  # noqa: E402
from packages.chantheory.config import DEFAULT_PARAMETERS, DEFAULT_SIGNALS_CONFIG, get_default_max_bi_num  # noqa: E402


def _run_analyze(scenario: dict, rows: list[dict]):
    parameters = dict(DEFAULT_PARAMETERS)
    parameters["max_bi_num"] = get_default_max_bi_num(scenario["timeframe"])
    return analyze(
        rows=rows,
        symbol=scenario["symbol"],
        timeframe=scenario["timeframe"],
        source=scenario["source"],
        parameters=parameters,
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )


def _primitive_structure_checks(payload: dict) -> list[dict]:
    """Verify plot_primitives align with structure fields (not just non-empty)."""
    issues: list[dict] = []
    fractals = {item["id"]: item for item in payload.get("fractals") or []}
    strokes = {item["id"]: item for item in payload.get("strokes") or []}
    segments = {item["id"]: item for item in payload.get("segments") or []}
    pivots = {item["id"]: item for item in payload.get("pivot_zones") or []}
    candidates = {
        item["id"]: item
        for item in list(payload.get("candidate_buy_points") or [])
        + list(payload.get("candidate_sell_points") or [])
    }

    pending = payload.get("meta", {}).get("pending_stroke")
    if pending and isinstance(pending, dict) and pending.get("id"):
        strokes[pending["id"]] = pending

    for primitive in payload.get("plot_primitives") or []:
        meta = primitive.get("meta") or {}
        ref_type = meta.get("reference_type")
        ref_id = meta.get("reference_id")
        layer = primitive.get("layer")
        if ref_type == "fractal" and ref_id:
            target = fractals.get(ref_id)
            if target is None:
                issues.append({"primitive": primitive.get("id"), "issue": "missing_fractal", "ref_id": ref_id})
                continue
            if primitive.get("x") != target.get("timestamp") or primitive.get("y") != target.get("price"):
                issues.append(
                    {
                        "primitive": primitive.get("id"),
                        "issue": "fractal_xy_mismatch",
                        "ref_id": ref_id,
                        "primitive_xy": [primitive.get("x"), primitive.get("y")],
                        "structure_xy": [target.get("timestamp"), target.get("price")],
                    }
                )
        elif ref_type == "stroke" and ref_id:
            target = strokes.get(ref_id)
            if target is None:
                issues.append({"primitive": primitive.get("id"), "issue": "missing_stroke", "ref_id": ref_id})
                continue
            if (
                primitive.get("x1") != target.get("start_timestamp")
                or primitive.get("y1") != target.get("start_price")
                or primitive.get("x2") != target.get("end_timestamp")
                or primitive.get("y2") != target.get("end_price")
            ):
                issues.append(
                    {
                        "primitive": primitive.get("id"),
                        "issue": "stroke_endpoint_mismatch",
                        "ref_id": ref_id,
                    }
                )
            expected_color_up = "#2563EB"
            expected_color_down = "#F97316"
            direction = target.get("direction")
            if direction == "up" and primitive.get("color") != expected_color_up:
                issues.append({"primitive": primitive.get("id"), "issue": "stroke_color_direction", "direction": direction})
            if direction == "down" and primitive.get("color") != expected_color_down:
                issues.append({"primitive": primitive.get("id"), "issue": "stroke_color_direction", "direction": direction})
        elif ref_type == "segment" and ref_id:
            target = segments.get(ref_id)
            if target is None:
                issues.append({"primitive": primitive.get("id"), "issue": "missing_segment", "ref_id": ref_id})
        elif ref_type == "pivot_zone" and ref_id:
            target = pivots.get(ref_id)
            if target is None:
                issues.append({"primitive": primitive.get("id"), "issue": "missing_pivot", "ref_id": ref_id})
            else:
                if primitive.get("y1") not in (target.get("low"), target.get("zd"), target.get("dd")) and primitive.get(
                    "y2"
                ) not in (target.get("high"), target.get("zg"), target.get("gg")):
                    # Soft check: box bounds should reference pivot range fields.
                    if {primitive.get("y1"), primitive.get("y2")} - {
                        target.get("low"),
                        target.get("high"),
                        target.get("zd"),
                        target.get("zg"),
                        target.get("dd"),
                        target.get("gg"),
                    }:
                        issues.append(
                            {
                                "primitive": primitive.get("id"),
                                "issue": "pivot_box_bounds_unrelated",
                                "ref_id": ref_id,
                            }
                        )
        elif layer == "candidate_points" and ref_id:
            if ref_id not in candidates:
                issues.append({"primitive": primitive.get("id"), "issue": "missing_candidate", "ref_id": ref_id})
    return issues


def _first_bar_signal_probe(old: dict, new: dict) -> dict:
    """Collect evidence for first-bar third_bs not_ready → inactive."""
    def _points(payload: dict, key: str) -> list[dict]:
        for series in payload.get("signal_series") or []:
            if series.get("signal_key") == key:
                return list(series.get("points") or [])
        return []

    old_points = _points(old, "third_bs")
    new_points = _points(new, "third_bs")
    old0 = old_points[0] if old_points else None
    new0 = new_points[0] if new_points else None
    old_warn = [w for w in (old.get("warnings") or []) if "SIGNAL_EVALUATION_FAILED" in str(w.get("warning_code") or w)]
    new_warn = [w for w in (new.get("warnings") or []) if "SIGNAL_EVALUATION_FAILED" in str(w.get("warning_code") or w)]
    return {
        "signal_key": "third_bs",
        "old_bar0": old0,
        "new_bar0": new0,
        "old_has_signal_evaluation_failed": bool(old_warn),
        "new_has_signal_evaluation_failed": bool(new_warn),
        "status_changed": (old0 or {}).get("status") != (new0 or {}).get("status"),
        "value_changed": (old0 or {}).get("value") != (new0 or {}).get("value"),
        "investigation_note": (
            "Old czsc.signals.cxt third_bs raised IndexError on insufficient structure "
            "→ adapter status=not_ready + SIGNAL_EVALUATION_FAILED. New Rust dispatcher "
            "returns 其他_任意_任意_0 without exception → inactive. Preserving old口径 "
            "would require inventing structure-readiness heuristics not present in Signal "
            "return values; see probe_first_bar_status.py for adapter-side options."
        ),
    }


def _multi_timeframe_check() -> dict:
    """Fixed multi-timeframe sample: 5m + 30m + synthetic day for 600584 / synthetic."""
    rows_5m = load_json(INPUTS_DIR / "5m_real_600584_548_rows.json")
    rows_30m = load_json(INPUTS_DIR / "30m_600584_sh_rows.json")
    # Multi-timeframe for the same symbol; day uses synthetic only as separate note.
    multi = analyze_multi_timeframe(
        rows_by_timeframe={"5m": rows_5m, "30m": rows_30m},
        symbol="600584.SH",
        base_timeframe="5m",
        source="tencent",
        parameters=None,
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )
    single_5m = analyze(
        rows=rows_5m,
        symbol="600584.SH",
        timeframe="5m",
        source="tencent",
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )
    single_30m = analyze(
        rows=rows_30m,
        symbol="600584.SH",
        timeframe="30m",
        source="tencent",
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )
    by_tf = {level.timeframe: level.analysis for level in multi.levels}
    diffs_5m = semantic_diff(single_5m.to_dict(), by_tf["5m"].to_dict()) if "5m" in by_tf else [{"reason": "missing_5m"}]
    diffs_30m = semantic_diff(single_30m.to_dict(), by_tf["30m"].to_dict()) if "30m" in by_tf else [{"reason": "missing_30m"}]
    # Multi-timeframe result reuses per-level analysis; provenance fields should match.
    # Ignore top-level multi wrapper differences by comparing level payloads only.
    return {
        "symbol": "600584.SH",
        "requested_timeframes": ["5m", "30m"],
        "base_timeframe": "5m",
        "available_timeframes": list(multi.timeframes),
        "roles": multi.meta.get("roles"),
        "bar_count_by_timeframe": multi.meta.get("bar_count_by_timeframe"),
        "level_vs_independent_diff_counts": {
            "5m": len(diffs_5m),
            "30m": len(diffs_30m),
        },
        "level_vs_independent_sample_diffs": {
            "5m": diffs_5m[:5],
            "30m": diffs_30m[:5],
        },
        "warnings": [asdict(w) for w in multi.warnings],
        "pass": len(diffs_5m) == 0 and len(diffs_30m) == 0,
    }


def compare_scenario(scenario: dict) -> dict:
    input_path = INPUTS_DIR / scenario["input_file"]
    baseline_path = OUTPUTS_DIR / scenario["baseline_output"]
    rows = load_json(input_path)
    old = load_json(baseline_path)
    new_result = _run_analyze(scenario, rows)
    new = new_result.to_dict()

    diffs = strip_expected_migration_noise(semantic_diff(old, new))
    categories = count_by_category(diffs)
    behavior_diffs = [
        item
        for item in diffs
        if item.get("category") not in {"migration_provenance"}
    ]
    # Split first-bar third_bs / warning disappearance into confirmation queue
    # (must follow #172: investigate, then user-confirm if unavoidable).
    confirmation_queue: list[dict] = []
    remaining_behavior: list[dict] = []
    for item in behavior_diffs:
        path = str(item.get("path", ""))
        category = str(item.get("category") or "")
        is_first_bar_third = (
            ("third_bs" in path and (".points[0]" in path or path.endswith("[0].status") or path.endswith("[0].value")))
            or path.startswith("$.signal_series[3].points[0]")
            or path.startswith("$.signal_snapshots[0].statuses.third_bs")
            or path.startswith("$.signal_snapshots[0].values.third_bs")
        )
        if is_first_bar_third:
            confirmation_queue.append({**item, "queue_reason": "first_bar_third_bs_status"})
            continue
        if category == "warnings" or path.startswith("$.warnings"):
            confirmation_queue.append({**item, "queue_reason": "warning_set_change"})
            continue
        remaining_behavior.append(item)

    primitive_issues = _primitive_structure_checks(new)
    first_bar = _first_bar_signal_probe(old, new)

    return {
        "scenario_id": scenario["id"],
        "synthetic": scenario["synthetic"],
        "symbol": scenario["symbol"],
        "timeframe": scenario["timeframe"],
        "input_file": scenario["input_file"],
        "input_sha256": sha256_file(input_path),
        "baseline_output": scenario["baseline_output"],
        "baseline_sha256": sha256_file(baseline_path),
        "bar_count": len(rows),
        "old_counts": structure_counts(old),
        "new_counts": structure_counts(new),
        "old_signal_active": signal_active_counts(old),
        "new_signal_active": signal_active_counts(new),
        "diff_total": len(diffs),
        "diff_by_category": categories,
        "behavior_diff_count": len(remaining_behavior),
        "confirmation_queue_count": len(confirmation_queue),
        "behavior_diff_sample": remaining_behavior[:20],
        "confirmation_queue_sample": confirmation_queue[:20],
        "plot_primitive_structure_issues": primitive_issues,
        "plot_primitives_pass": len(primitive_issues) == 0,
        "first_bar_third_bs": first_bar,
        "pass_strict_behavior": len(remaining_behavior) == 0 and len(primitive_issues) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", help="Optional scenario id filter")
    args = parser.parse_args()

    artifacts = ensure_artifacts_dir()
    selected = [s for s in SCENARIOS if not args.scenario or s["id"] in args.scenario]
    env = environment_snapshot()
    scenario_reports = [compare_scenario(scenario) for scenario in selected]
    multi = _multi_timeframe_check()

    report = {
        "task": "#176",
        "kind": "baseline_behavior_compare",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment": env,
        "gate_note": (
            "#174/#175 not formally accepted yet; this run must not close #176, "
            "must not update formal fixtures, and must not enter #177."
        ),
        "scenarios": scenario_reports,
        "multi_timeframe": multi,
        "summary": {
            "scenarios_pass_strict_behavior": all(item["pass_strict_behavior"] for item in scenario_reports),
            "any_confirmation_queue": any(item["confirmation_queue_count"] for item in scenario_reports),
            "multi_timeframe_pass": multi["pass"],
            "plot_primitives_all_pass": all(item["plot_primitives_pass"] for item in scenario_reports),
        },
    }
    out = artifacts / "baseline_compare.json"
    dump_json(out, report)
    print(f"Wrote {out}")
    print(json_summary(report))
    # Non-zero only for unexpected remaining behavior diffs or primitive/MTF failures.
    if not report["summary"]["scenarios_pass_strict_behavior"] or not multi["pass"]:
        return 1
    return 0


def json_summary(report: dict) -> str:
    lines = [
        f"git_sha={report['git_sha']}",
        f"strict_behavior_pass={report['summary']['scenarios_pass_strict_behavior']}",
        f"confirmation_queue={report['summary']['any_confirmation_queue']}",
        f"multi_timeframe_pass={report['summary']['multi_timeframe_pass']}",
        f"plot_primitives_pass={report['summary']['plot_primitives_all_pass']}",
    ]
    for scenario in report["scenarios"]:
        lines.append(
            f"- {scenario['scenario_id']}: behavior={scenario['behavior_diff_count']} "
            f"confirm={scenario['confirmation_queue_count']} "
            f"primitives_ok={scenario['plot_primitives_pass']} "
            f"cats={scenario['diff_by_category']}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
