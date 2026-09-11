#!/usr/bin/env python3
"""Engine-level incremental CZSC.update vs full rebuild consistency (#176).

Uses production mapping/signal path via analyze_normalized, while owning the
raw CZSC object only inside this spike (same pattern as spikes/0008).

Does not introduce a product-level incremental API (ADR 0008 remains full rebuild).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import (  # noqa: E402
    INPUTS_DIR,
    SCENARIOS,
    dump_json,
    ensure_artifacts_dir,
    environment_snapshot,
    git_sha,
    load_json,
    semantic_diff,
    utc_now,
)

from packages.chantheory import analyze_normalized, normalize_ohlcv_rows  # noqa: E402
from packages.chantheory.config import (  # noqa: E402
    DEFAULT_SIGNALS_CONFIG,
    get_default_max_bi_num,
    get_default_parameters,
    get_freq_name,
)
from packages.chantheory.engine import load_czsc, parse_dt, run_engine  # noqa: E402


class EngineIncrementalHarness:
    def __init__(self, rows, symbol, timeframe, source, signals_config=None):
        self._rows = [dict(row) for row in rows]
        self._symbol = symbol
        self._timeframe = timeframe
        self._source = source
        self._signals_config = signals_config if signals_config is not None else list(DEFAULT_SIGNALS_CONFIG)
        normalized = self._normalize(self._rows)
        parameters = get_default_parameters()
        parameters["max_bi_num"] = get_default_max_bi_num(timeframe)
        self._parameters = parameters
        self._analyzer, self._raw_bars = run_engine(normalized, parameters)
        self._RawBar, self._Freq, _ = load_czsc()

    def _normalize(self, rows):
        return normalize_ohlcv_rows(
            rows,
            symbol=self._symbol,
            timeframe=self._timeframe,
            source=self._source,
            strict=True,
        )

    def advance(self, row):
        self._rows.append(dict(row))
        normalized = self._normalize(self._rows)
        bar = normalized.bars[-1]
        raw_bar = self._RawBar(
            symbol=bar.symbol,
            id=bar.bar_index,
            dt=parse_dt(bar.timestamp),
            freq=getattr(self._Freq, get_freq_name(bar.timeframe)),
            open=bar.open,
            close=bar.close,
            high=bar.high,
            low=bar.low,
            vol=bar.volume,
            amount=bar.amount,
        )
        self._analyzer.update(raw_bar)
        self._raw_bars.append(raw_bar)
        return self.result(normalized)

    def result(self, normalized=None):
        normalized = normalized or self._normalize(self._rows)
        # Isolate signal-replay mutation from analyzer-owned RawBar cache.
        freq = getattr(self._Freq, get_freq_name(normalized.timeframe))
        signal_bars = [
            self._RawBar(
                symbol=bar.symbol,
                id=bar.bar_index,
                dt=parse_dt(bar.timestamp),
                freq=freq,
                open=bar.open,
                close=bar.close,
                high=bar.high,
                low=bar.low,
                vol=bar.volume,
                amount=bar.amount,
            )
            for bar in normalized.bars
        ]
        with patch("packages.chantheory.adapters._run_engine", return_value=(self._analyzer, signal_bars)):
            return normalized, analyze_normalized(
                normalized,
                parameters=dict(self._parameters),
                signals_config=self._signals_config,
            )


def full_rebuild(rows, symbol, timeframe, source, signals_config=None):
    normalized = normalize_ohlcv_rows(
        rows,
        symbol=symbol,
        timeframe=timeframe,
        source=source,
        strict=True,
    )
    parameters = get_default_parameters()
    parameters["max_bi_num"] = get_default_max_bi_num(timeframe)
    result = analyze_normalized(
        normalized,
        parameters=parameters,
        signals_config=signals_config if signals_config is not None else list(DEFAULT_SIGNALS_CONFIG),
    )
    return normalized, result


def _compare_payloads(left, right, left_norm, right_norm):
    left_payload = left.to_dict()
    right_payload = right.to_dict()
    left_payload["normalized_bars"] = [bar.__dict__ if hasattr(bar, "__dict__") else bar for bar in left_norm.bars]
    right_payload["normalized_bars"] = [bar.__dict__ if hasattr(bar, "__dict__") else bar for bar in right_norm.bars]
    # Drop volatile meta timestamps if any; keep engine_version etc.
    return semantic_diff(left_payload, right_payload)


def run_scenario(scenario: dict, warm_count: int, max_steps: int | None) -> dict:
    rows = load_json(INPUTS_DIR / scenario["input_file"])
    if warm_count >= len(rows):
        raise ValueError(f"{scenario['id']}: warm_count={warm_count} >= bar_count={len(rows)}")
    warm = rows[:warm_count]
    target = rows[warm_count:]
    if max_steps is not None:
        target = target[:max_steps]

    harness = EngineIncrementalHarness(
        warm,
        symbol=scenario["symbol"],
        timeframe=scenario["timeframe"],
        source=scenario["source"],
    )
    failures: list[dict] = []
    checked = 0
    for step, row in enumerate(target, 1):
        prefix_rows = warm + target[:step]
        inc_norm, inc_result = harness.advance(row)
        reb_norm, reb_result = full_rebuild(
            prefix_rows,
            symbol=scenario["symbol"],
            timeframe=scenario["timeframe"],
            source=scenario["source"],
        )
        diffs = semantic_diff(reb_result.to_dict(), inc_result.to_dict())
        # Normalized bars must also match timestamps/length.
        if len(reb_norm.bars) != len(inc_norm.bars):
            diffs.append(
                {
                    "path": "$.normalized.bars",
                    "left": len(reb_norm.bars),
                    "right": len(inc_norm.bars),
                    "reason": "length",
                }
            )
        checked += 1
        if diffs:
            failures.append(
                {
                    "prefix": warm_count + step,
                    "step": step,
                    "diff_count": len(diffs),
                    "sample": diffs[:5],
                }
            )
            # Keep going to collect all failing prefixes unless huge.
            if len(failures) >= 20:
                break

    return {
        "scenario_id": scenario["id"],
        "symbol": scenario["symbol"],
        "timeframe": scenario["timeframe"],
        "bar_count": len(rows),
        "warm_count": warm_count,
        "steps_checked": checked,
        "failure_count": len(failures),
        "failures": failures,
        "pass": len(failures) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--warm-count", type=int, default=None, help="Warm prefix before incremental steps")
    parser.add_argument("--max-steps", type=int, default=None, help="Limit incremental steps (smoke)")
    parser.add_argument("--smoke", action="store_true", help="Short run: 5m 20 steps, day 20 steps")
    args = parser.parse_args()

    selected = [s for s in SCENARIOS if not args.scenario or s["id"] in args.scenario]
    reports = []
    for scenario in selected:
        if args.smoke:
            warm = 80 if scenario["timeframe"] == "day" else 100
            max_steps = 20
        elif args.warm_count is not None:
            warm = args.warm_count
            max_steps = args.max_steps
        else:
            # Full default: leave enough warm structure, check all remaining prefixes.
            warm = min(100, max(30, len(load_json(INPUTS_DIR / scenario["input_file"])) // 5))
            max_steps = args.max_steps
        # Scenario-specific defaults for complete #176 evidence when not overridden.
        if args.warm_count is None and not args.smoke:
            if scenario["id"] == "daily_synthetic_120":
                warm, max_steps = 40, None
            elif scenario["id"] == "5m_real_600584_548":
                warm, max_steps = 500, None
            elif scenario["id"] == "30m_real_600584":
                warm, max_steps = 1200, 136
        report = run_scenario(scenario, warm_count=warm, max_steps=max_steps)
        reports.append(report)
        per = ensure_artifacts_dir() / f"incremental_vs_rebuild_{scenario['id']}.json"
        dump_json(per, report)
        print(f"Wrote {per}")

    payload = {
        "task": "#176",
        "kind": "incremental_vs_rebuild",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
        "environment": environment_snapshot(),
        "product_note": (
            "Product remains ADR 0008 full rebuild. This checks underlying "
            "CZSC.update vs rebuild equivalence only; CzscSignals/BarGenerator unused."
        ),
        "scenarios": reports,
        "summary": {
            "all_pass": all(item["pass"] for item in reports),
            "failure_scenarios": [item["scenario_id"] for item in reports if not item["pass"]],
        },
    }
    out = ensure_artifacts_dir() / "incremental_vs_rebuild.json"
    dump_json(out, payload)
    print(f"Wrote {out}")
    for item in reports:
        print(
            f"- {item['scenario_id']}: pass={item['pass']} "
            f"steps={item['steps_checked']} failures={item['failure_count']}"
        )
    return 0 if payload["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
