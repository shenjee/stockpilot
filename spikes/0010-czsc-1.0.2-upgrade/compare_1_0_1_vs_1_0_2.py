#!/usr/bin/env python3
"""Compare czsc 1.0.1 vs 1.0.2 on the three frozen inputs (#205).

Runs both engines in the *current* interpreter's environment is not possible
(one process, one installed czsc), so this script drives two child processes:

1. ``run_frozen.py`` under the 1.0.1 interpreter (formal ``~/.venvs/czsc``).
2. ``run_frozen.py --override-pin`` under the 1.0.2 interpreter
   (``~/.venvs/czsc102-isolated``). The override rebinds every loaded
   ``PINNED_ENGINE_VERSION`` in-process; no file is modified.

Then it compares the stable structure fields and the four default signal
values, and attributes every removed fractal to an adjacent same-mark
duplicate (upstream #347).

Verdict rules (from the issue):

- PASS  : strokes / segments / pivot zones identical; signal values and
          active classification identical; fractals either identical or every
          removal attributable to an adjacent same-mark duplicate.
- STOP  : anything else.

Usage (from repo root, either interpreter):

    python spikes/0010-czsc-1.0.2-upgrade/compare_1_0_1_vs_1_0_2.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPIKE_DIR = REPO_ROOT / "spikes" / "0010-czsc-1.0.2-upgrade"
RUN_FROZEN = SPIKE_DIR / "run_frozen.py"

DEFAULT_REF_PY = Path.home() / ".venvs" / "czsc" / "bin" / "python"
DEFAULT_CAND_PY = Path.home() / ".venvs" / "czsc102-isolated" / "bin" / "python"

SCENARIO_IDS = ("daily_synthetic_120", "5m_real_600584_548", "30m_real_600584")

# Fields that must be identical for each structure kind. ``id`` embeds
# timestamps/prices, so it is covered implicitly, but kept explicit.
STRUCTURE_FIELDS = {
    "fractals": ("id", "timestamp", "price", "fractal_type", "confirmed", "bar_index"),
    "strokes": ("id", "direction", "start_timestamp", "start_price", "end_timestamp", "end_price", "confirmed",
                "start_fractal_id", "end_fractal_id"),
    "segments": ("id", "direction", "start_timestamp", "start_price", "end_timestamp", "end_price", "confirmed", "stroke_ids"),
    "pivot_zones": ("id", "level", "start_timestamp", "end_timestamp", "high", "low", "active", "segment_ids"),
}

# Collections whose *content* must match, not just their length. A count-only
# check would miss a divergence whose id stays put while its type or ratio
# changes, or a candidate point whose price moves.
CONTENT_FIELDS = {
    "divergences": ("id", "divergence_type", "confirmed", "timestamp", "reference_id", "pivot_zone_id", "ratio"),
    "candidate_buy_points": ("id", "point_type", "timestamp", "price", "confirmed", "reference_id", "reason", "meta"),
    "candidate_sell_points": ("id", "point_type", "timestamp", "price", "confirmed", "reference_id", "reason", "meta"),
    "plot_primitives": ("id", "type", "layer", "style", "x", "y", "x1", "x2", "y1", "y2", "text", "color", "meta"),
}

# Collections compared field-by-field; any difference is a hard STOP.
HARD_COLLECTIONS = ("fractals", "strokes", "segments", "pivot_zones", *CONTENT_FIELDS)

SIGNAL_KEYS = ("first_buy", "first_sell", "second_bs", "third_bs")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def project(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [{f: item.get(f) for f in fields} for item in items]


def diff_list(name: str, ref: list, cand: list) -> list[dict]:
    out = []
    if len(ref) != len(cand):
        out.append({"kind": "length", "collection": name, "ref": len(ref), "cand": len(cand)})
    for i, (a, b) in enumerate(zip(ref, cand)):
        if a != b:
            out.append({"kind": "item", "collection": name, "index": i, "ref": a, "cand": b})
    return out


def attribute_removed_fractals(ref: list[dict], cand: list[dict]) -> dict:
    """Attribute fractals present in ``ref`` but missing from ``cand``.

    Upstream #347 drops a candidate fractal when it carries the same mark as
    the adjacent (previous) kept fractal. So a removed fractal ``R`` is
    attributable only when its *immediate predecessor in the ref sequence*
    is itself kept in ``cand`` and has the same ``fractal_type``. Using the
    last kept fractal anywhere before ``R`` would wrongly attribute a run of
    consecutive removals to a fractal several positions back.
    Returns per-removal records plus the residual (unattributed) list.
    """
    cand_ids = {f["id"] for f in cand}
    records = []
    unattributed = []
    removed_count = 0
    for i, r in enumerate(ref):
        if r["id"] in cand_ids:
            continue
        removed_count += 1
        prev = ref[i - 1] if i > 0 else None
        prev_kept = prev is not None and prev["id"] in cand_ids
        same_mark = prev_kept and prev["fractal_type"] == r["fractal_type"]
        rec = {
            "removed": r,
            "immediate_predecessor": prev,
            "predecessor_kept_in_cand": prev_kept,
            "same_mark_as_predecessor": same_mark,
        }
        records.append(rec)
        if not same_mark:
            unattributed.append(rec)
    return {"removed_count": removed_count, "records": records, "unattributed": unattributed}


def compare_signals(ref: dict, cand: dict) -> tuple[list[dict], dict]:
    diffs = []
    stats = {}
    # signal_series: per key, every point's value/active/status must match.
    ref_series = {s["signal_key"]: s for s in ref["signal_series"]}
    cand_series = {s["signal_key"]: s for s in cand["signal_series"]}
    if set(ref_series) != set(cand_series):
        diffs.append({"kind": "signal_keys", "ref": sorted(ref_series), "cand": sorted(cand_series)})
    for key in sorted(set(ref_series) & set(cand_series)):
        a, b = ref_series[key], cand_series[key]
        if a["signal_name"] != b["signal_name"]:
            diffs.append({"kind": "signal_name", "signal_key": key, "ref": a["signal_name"], "cand": b["signal_name"]})
        pa, pb = a["points"], b["points"]
        if len(pa) != len(pb):
            diffs.append({"kind": "signal_points_length", "signal_key": key, "ref": len(pa), "cand": len(pb)})
        changed = 0
        active_changed = 0
        for i, (x, y) in enumerate(zip(pa, pb)):
            if x["value"] != y["value"]:
                changed += 1
                if len(diffs) < 200:
                    diffs.append({"kind": "signal_value", "signal_key": key, "bar_index": x["bar_index"], "timestamp": x["timestamp"],
                                  "ref": x["value"], "cand": y["value"]})
            if x["active"] != y["active"] or x["status"] != y["status"]:
                active_changed += 1
                if len(diffs) < 200:
                    diffs.append({"kind": "signal_active", "signal_key": key, "bar_index": x["bar_index"], "timestamp": x["timestamp"],
                                  "ref": [x["active"], x["status"]], "cand": [y["active"], y["status"]]})
        stats[key] = {"points": len(pa), "value_changed": changed, "active_changed": active_changed,
                      "ref_latest": a["latest_value"], "cand_latest": b["latest_value"]}
    # signal_events: exact match on the event tuple.
    ev_fields = ("id", "signal_key", "bar_index", "timestamp", "value", "active", "status", "event_type")
    diffs.extend(diff_list("signal_events", project(ref["signal_events"], ev_fields), project(cand["signal_events"], ev_fields)))
    # signal_snapshots: values + statuses per bar.
    snap_diff = 0
    for x, y in zip(ref["signal_snapshots"], cand["signal_snapshots"]):
        if x["values"] != y["values"] or x["statuses"] != y["statuses"] or x["active_signals"] != y["active_signals"]:
            snap_diff += 1
            if snap_diff <= 50:
                diffs.append({"kind": "signal_snapshot", "bar_index": x["bar_index"], "timestamp": x["timestamp"],
                              "ref": [x["values"], x["statuses"]], "cand": [y["values"], y["statuses"]]})
    if len(ref["signal_snapshots"]) != len(cand["signal_snapshots"]):
        diffs.append({"kind": "signal_snapshots_length", "ref": len(ref["signal_snapshots"]), "cand": len(cand["signal_snapshots"])})
    stats["_snapshots"] = {"count": len(ref["signal_snapshots"]), "changed": snap_diff}
    return diffs, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref-python", default=str(DEFAULT_REF_PY))
    parser.add_argument("--cand-python", default=str(DEFAULT_CAND_PY))
    parser.add_argument("--ref-out", default=str(SPIKE_DIR / "reference-1.0.1"))
    parser.add_argument("--cand-out", default=str(SPIKE_DIR / "candidate-1.0.2"))
    parser.add_argument("--skip-run", action="store_true", help="Reuse existing output directories")
    args = parser.parse_args()

    if not args.skip_run:
        for py, out, extra in ((args.ref_python, args.ref_out, []), (args.cand_python, args.cand_out, ["--override-pin"])):
            cmd = [py, str(RUN_FROZEN), "--out", out, *extra]
            print(f"$ {' '.join(cmd)}", flush=True)
            subprocess.run(cmd, check=True, cwd=REPO_ROOT)

    report = {"reference_dir": args.ref_out, "candidate_dir": args.cand_out, "scenarios": {}}
    overall = "PASS"
    for sid in SCENARIO_IDS:
        ref = load(Path(args.ref_out) / f"{sid}.json")
        cand = load(Path(args.cand_out) / f"{sid}.json")
        s: dict = {"ref_engine_version": ref["engine_version"], "cand_engine_version": cand["engine_version"]}
        s["counts"] = {k: {"ref": len(ref[k]), "cand": len(cand[k])}
                       for k in ("fractals", "strokes", "segments", "pivot_zones", "divergences", "signal_events",
                                 "signal_snapshots", "candidate_buy_points", "candidate_sell_points", "plot_primitives")}
        s["structure_diffs"] = {}
        for coll, fields in {**STRUCTURE_FIELDS, **CONTENT_FIELDS}.items():
            d = diff_list(coll, project(ref[coll], fields), project(cand[coll], fields))
            if d:
                s["structure_diffs"][coll] = d
        s["fractal_attribution"] = attribute_removed_fractals(ref["fractals"], cand["fractals"])
        # Also detect fractals that exist only in cand (never allowed).
        ref_ids = {f["id"] for f in ref["fractals"]}
        cand_ids = {f["id"] for f in cand["fractals"]}
        s["fractals_only_in_cand"] = sorted(cand_ids - ref_ids)
        s["fractals_only_in_ref"] = sorted(ref_ids - cand_ids)
        sig_diffs, sig_stats = compare_signals(ref, cand)
        s["signal_diffs"] = sig_diffs
        s["signal_stats"] = sig_stats
        s["warnings_ref"] = sorted({w["warning_code"] for w in ref["warnings"]})
        s["warnings_cand"] = sorted({w["warning_code"] for w in cand["warnings"]})
        s["summary_ref"] = ref["summary"]
        s["summary_cand"] = cand["summary"]

        hard = [c for c in HARD_COLLECTIONS if c in s["structure_diffs"]]
        fx_attr = s["fractal_attribution"]
        verdict = "PASS"
        reasons = []
        if hard:
            verdict = "STOP"
            reasons.append(f"structure/content changed: {hard}")
        if s["fractals_only_in_cand"]:
            verdict = "STOP"
            reasons.append("fractals appear in 1.0.2 that are absent in 1.0.1")
        if fx_attr["unattributed"]:
            verdict = "STOP"
            reasons.append(f"{len(fx_attr['unattributed'])} removed fractal(s) not attributable to adjacent same-mark duplicates")
        if sig_diffs:
            verdict = "STOP"
            reasons.append(f"{len(sig_diffs)} signal difference(s)")
        if s["warnings_ref"] != s["warnings_cand"]:
            verdict = "STOP"
            reasons.append(f"warning codes differ: {s['warnings_ref']} vs {s['warnings_cand']}")
        s["verdict"] = verdict
        s["reasons"] = reasons
        if verdict != "PASS":
            overall = "STOP"
        report["scenarios"][sid] = s

    report["overall"] = overall
    out_path = SPIKE_DIR / "comparison-report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Console summary
    print(f"\nOVERALL: {overall}")
    for sid, s in report["scenarios"].items():
        c = s["counts"]
        print(f"\n[{sid}] verdict={s['verdict']}  ref={s['ref_engine_version']} cand={s['cand_engine_version']}")
        print(f"  fractals {c['fractals']['ref']}->{c['fractals']['cand']}  strokes {c['strokes']['ref']}->{c['strokes']['cand']}  "
              f"segments {c['segments']['ref']}->{c['segments']['cand']}  pivots {c['pivot_zones']['ref']}->{c['pivot_zones']['cand']}  "
              f"divergences {c['divergences']['ref']}->{c['divergences']['cand']}")
        print(f"  signal_events {c['signal_events']['ref']}->{c['signal_events']['cand']}  snapshots {c['signal_snapshots']['ref']}->{c['signal_snapshots']['cand']}  "
              f"cand_buy {c['candidate_buy_points']['ref']}->{c['candidate_buy_points']['cand']}  cand_sell {c['candidate_sell_points']['ref']}->{c['candidate_sell_points']['cand']}  "
              f"primitives {c['plot_primitives']['ref']}->{c['plot_primitives']['cand']}")
        fa = s["fractal_attribution"]
        print(f"  removed fractals: {fa['removed_count']}  unattributed: {len(fa['unattributed'])}  only_in_cand: {len(s['fractals_only_in_cand'])}")
        for coll, d in s["structure_diffs"].items():
            print(f"  STRUCT DIFF {coll}: {len(d)} (first: {json.dumps(d[0], ensure_ascii=False)[:300]})")
        print(f"  signal diffs: {len(s['signal_diffs'])}")
        for d in s["signal_diffs"][:5]:
            print(f"    {json.dumps(d, ensure_ascii=False)[:300]}")
        for k, v in s["signal_stats"].items():
            print(f"  signal {k}: {v}")
        for r in s["reasons"]:
            print(f"  REASON: {r}")
    print(f"\nwrote {out_path}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
