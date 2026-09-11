#!/usr/bin/env python3
"""Investigate whether first-bar third_bs not_ready can be preserved on 1.0.1.

Runs against frozen inputs in the current (1.0.1) environment and records:
  - native call_signal return on early prefixes
  - finished_bis / bi_list lengths
  - whether IndexError still occurs
  - adapter-side options and recommendation
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import INPUTS_DIR, dump_json, ensure_artifacts_dir, environment_snapshot, git_sha, load_json, utc_now  # noqa: E402

from packages.chantheory import analyze  # noqa: E402
from packages.chantheory.config import DEFAULT_SIGNALS_CONFIG, get_default_max_bi_num, get_default_parameters  # noqa: E402
from packages.chantheory.engine import load_czsc, run_engine  # noqa: E402
from packages.chantheory.normalize import normalize_ohlcv_rows  # noqa: E402


def _probe_prefix(rows, symbol, timeframe, source, prefix: int) -> dict:
    subset = rows[:prefix]
    normalized = normalize_ohlcv_rows(subset, symbol=symbol, timeframe=timeframe, source=source, strict=True)
    parameters = get_default_parameters()
    parameters["max_bi_num"] = get_default_max_bi_num(timeframe)
    analyzer, _raw = run_engine(normalized, parameters)

    bi_list = list(getattr(analyzer, "bi_list", []) or [])
    finished = list(getattr(analyzer, "finished_bis", []) or [])
    fx_list = list(getattr(analyzer, "fx_list", []) or [])

    call_signal = None
    native_error = None
    native_value = None
    try:
        import czsc._native as native  # noqa: WPS433

        call_signal = native.call_signal
        result = call_signal("cxt_third_bs_V230319", analyzer, {"di": 1})
        if result:
            native_value = getattr(result[0], "value", str(result[0]))
    except Exception as exc:  # noqa: BLE001 - evidence capture
        native_error = f"{type(exc).__name__}: {exc}"

    analysis = analyze(
        rows=subset,
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        parameters=parameters,
        signals_config=list(DEFAULT_SIGNALS_CONFIG),
        strict=True,
    )
    third = next((s for s in analysis.signal_series if s.signal_key == "third_bs"), None)
    point0 = third.points[0] if third and third.points else None
    return {
        "prefix": prefix,
        "bar_count": len(subset),
        "fx_count": len(fx_list),
        "bi_count": len(bi_list),
        "finished_bi_count": len(finished),
        "native_call_error": native_error,
        "native_value": native_value,
        "adapter_point0_status": getattr(point0, "status", None),
        "adapter_point0_value": getattr(point0, "value", None),
        "warnings": [w.warning_code for w in analysis.warnings],
    }


def main() -> int:
    rows = load_json(INPUTS_DIR / "5m_real_600584_548_rows.json")
    probes = [_probe_prefix(rows, "600584.SH", "5m", "tencent", prefix) for prefix in (1, 2, 5, 10, 30, 50)]

    # Can we detect "old not_ready" without heuristics? Native never raises on these prefixes.
    always_returns_value = all(item["native_call_error"] is None and item["native_value"] for item in probes)
    recommendation = {
        "can_preserve_via_native_exception": False,
        "can_preserve_without_heuristic": False,
        "possible_adapter_heuristic": (
            "Map inactive→not_ready when finished_bis below a signal-specific threshold "
            "(e.g. third_bs needs many bis). This invents readiness rules not returned by "
            "1.0.1 Signal objects and risks false not_ready after engine becomes capable "
            "earlier. Not recommended unless product explicitly wants old failure mode."
        ),
        "recommended_action": (
            "Treat as upstream behavior change requiring #172-style user confirmation. "
            "Do not silently invent readiness heuristics. Document sample evidence."
        ),
        "always_returns_inactive_value_on_probed_prefixes": always_returns_value,
    }

    payload = {
        "task": "#176",
        "kind": "first_bar_status_investigation",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment": environment_snapshot(),
        "scenario": "5m_real_600584_548",
        "probes": probes,
        "recommendation": recommendation,
        "czsc_signals_note": "CzscSignals/BarGenerator unused in production; not adapted.",
    }
    out = ensure_artifacts_dir() / "first_bar_status_probe.json"
    dump_json(out, payload)
    print(f"Wrote {out}")
    print("recommendation:", recommendation["recommended_action"])
    for item in probes:
        print(
            f"prefix={item['prefix']} bi={item['bi_count']} finished={item['finished_bi_count']} "
            f"native={item['native_value']!r} status={item['adapter_point0_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
