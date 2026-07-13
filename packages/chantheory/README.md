# chantheory

`chantheory` is the project-owned adapter layer for Stock Pilot Phase 2.

It is not a reimplementation of Chan Theory core logic. Its job is to:

- normalize project OHLCV input,
- pin and probe the underlying `czsc` engine,
- freeze a project-level schema for skills, agents, and apps,
- define plotting, summary, and warning responsibilities,
- degrade safely when the engine or input is not ready.

## Status

Phase 2 P1 freezes the adapter contract and validates a working `czsc` path.

- Engine: `czsc==0.10.12`
- Validation runtime: Python `3.14.5`
- Why this pin: `0.10.12` is the installed and validated baseline for the current project runtime
- Import note: load `numpy.typing` before importing `czsc` so rs_czsc-dependent imports initialize consistently

Observed validation result:

- `czsc==0.10.12` installs successfully in the validated project virtualenv
- the current tracker day-bar payload converts into `czsc.objects.RawBar`
- `CZSC(raw_bars)` runs successfully for A-share sample data after the `numpy.typing` import shim

Phase 2 P2 adds:

- project-level mapping for `fractals`, `strokes`, `segments`, and `pivot_zones`
- project-level `divergences` mapping based on weaker same-direction stroke extensions around pivot zones
- stable `plot_primitives` for markers, lines, boxes, labels, and structure-only candidate points
- short summary and warning generation for unstable tail strokes and insufficient bars
- a Streamlit debug app under `apps/chan-viewer/`
- deterministic JSON and row fixtures under `packages/chantheory/tests/fixtures/`

## Public API

Current public entry points:

- `normalize_ohlcv_rows(...)`
- `normalize_tracker_klines(...)`
- `analyze(..., signals_config=None)`
- `analyze_multi_timeframe(..., base_timeframe, signals_config=None)`
- `analyze_tracker_klines(..., signals_config=None)`
- `analyze_multi_timeframe_tracker_klines(..., base_timeframe, signals_config=None)`
- `analyze_normalized(..., signals_config=None)`

All public fields use `snake_case`.

## Standard OHLCV Input

Phase 2 standardizes project input into ascending OHLCV bars with a stable symbol and timeframe.

Required source fields:

- timestamp field: one of `timestamp`, `datetime`, `dt`, `date`, `trade_date`
- price fields: `open`, `high`, `low`, `close`
- volume field: `volume` or `vol`

Optional source fields:

- `amount` or `turnover`

Normalized internal bar:

```json
{
  "symbol": "000001.SZ",
  "timeframe": "day",
  "timestamp": "2025-04-30",
  "open": 12.31,
  "high": 12.58,
  "low": 12.11,
  "close": 12.44,
  "volume": 1823400.0,
  "amount": 22683096.0,
  "source": "tencent",
  "bar_index": 77,
  "meta": {
    "amount_derived": true
  }
}
```

Normalization rules:

- sort bars by timestamp ascending
- keep the last row when timestamps are duplicated
- preserve source bars without project-side inclusion removal
- derive `amount` with `close * volume` when the source omits it
- reject invalid price geometry in strict mode

## Timeframe Mapping

Project timeframe to `czsc` mapping:

| Project | `czsc` |
| --- | --- |
| `1m` | `F1` |
| `5m` | `F5` |
| `15m` | `F15` |
| `30m` | `F30` |
| `60m` | `F60` |
| `day` | `D` |
| `week` | `W` |
| `month` | `M` |

Current repo validation only covers day bars because `china-stock-analysis` persists day K-lines today.

## Result Schema

P1 freezes the top-level schema and P2 fills the stable structure mapping.

```json
{
  "symbol": "000001.SZ",
  "timeframe": "day",
  "source": "tencent",
  "engine": "czsc",
  "engine_version": "0.10.12",
  "parameters": {
    "max_bi_num": 50,
    "min_bars": 60,
    "strict_validation": true,
    "derive_amount_from_close_volume": true
  },
  "fractals": [],
  "strokes": [],
  "segments": [],
  "pivot_zones": [],
  "divergences": [],
  "structure_alerts": [],
  "signal_series": [],
  "signal_events": [],
  "signal_snapshots": [],
  "candidate_point_events": [],
  "candidate_buy_points": [],
  "candidate_sell_points": [],
  "plot_primitives": [],
  "summary": [],
  "warnings": [],
  "meta": {}
}
```

The `max_bi_num` parameter defaults to 50 for day/week/month timeframes and 500 for minute timeframes (1m/5m/15m/30m/60m). Explicit parameters override these defaults.

Multi-timeframe analysis uses a project-owned grouped container instead of leaking `czsc` trader objects:

```json
{
  "symbol": "000001.SZ",
  "source": "tencent",
  "engine": "czsc",
  "engine_version": "0.10.12",
  "base_timeframe": "day",
  "timeframes": ["day", "week", "month"],
  "levels": [
    {
      "timeframe": "day",
      "role": "base",
      "bar_count": 120,
      "analysis": {}
    }
  ],
  "summary": [],
  "warnings": [],
  "meta": {
    "higher_timeframes": ["week", "month"],
    "lower_timeframes": [],
    "roles": {"day": "base", "week": "higher", "month": "higher"}
  }
}
```

Responsibilities are split as follows:

- `signal_series`: per-signal replay points across finished strokes / bars
- `signal_events`: signal lifecycle transitions such as triggered, switched, and invalidated
- `signal_snapshots`: per-bar signal values for hover, replay, and debug surfaces
- `candidate_point_events`: buy/sell replay events derived from project candidate-point mapping
- `levels`: ordered per-timeframe `AnalysisResult` payloads with explicit `base` / `higher` / `lower` roles
- `plot_primitives`: visualization-ready points, lines, boxes, labels, markers
- `summary`: short sentences for skills and agents
- `warnings`: normalization gaps, engine failures, and runtime degradation details

Current P2 mapping notes:

- `fractals`: mapped from `CZSC.fx_list`
- `strokes`: mapped from `CZSC.finished_bis`
- `segments`: derived from same-timeframe finished strokes with a project-side rule requiring odd stroke counts, initial three-stroke overlap, opposite endpoint progression, connected endpoints, and next opposite segment confirmation
- `pivot_zones`: derived from `czsc.utils.sig.get_zs_seq` on finished strokes
- `divergences`: mapped from same-direction stroke extensions that push beyond a pivot zone on weaker stroke magnitude after a retracement
- `signal_series` / `signal_events` / `signal_snapshots`: project-owned signal schema built from `signals_config`
- `candidate_point_events`: project replay of candidate-point trigger, switch, and invalidate history
- `candidate_buy_points` / `candidate_sell_points`: latest project interpretation results derived from signal replay or structure-only context; they are not trading instructions

`signals_config` accepts either:

- `None`: use the project default `cxt_*` buy/sell signal set
- a list of strings: treat each string as a function name under `czsc.signals.cxt`
- a list of mappings: each item may provide `module`, `name`, `key`, `kwargs`, and optional `enabled`

Multi-timeframe notes:

- `analyze_multi_timeframe(...)` expects a mapping like `{timeframe: rows}`
- `base_timeframe` identifies the chart timeframe that downstream apps should render first
- higher / lower timeframe roles are derived from the project timeframe ordering instead of `czsc.BarGenerator` / `CzscTrader`

## Plot Contract

`plot_primitives` currently supports:

- `marker` for fractals
- `line` for strokes and segments
- `box` for pivot zones
- `label` for structure alerts

Layer order remains:

1. `candles`
2. `fractals`
3. `strokes`
4. `segments`
5. `pivot_zones`
6. `candidate_points`
7. `divergences`
8. `alerts`

Style rules:

- top fractals: red triangle markers
- bottom fractals: green triangle markers
- up strokes: blue solid lines
- down strokes: orange solid lines
- segments: purple dashed lines
- active pivot zones: amber filled boxes
- divergences: green or red text markers at divergence endpoints
- candidate points: green or red diamond markers
- alerts: teal or red labels depending on severity

## Current Data Fit

Current `china-stock-analysis` day-bar records already provide:

- `date`
- `open`
- `close`
- `high`
- `low`
- `volume`

Current gaps against ideal `czsc` input metadata:

- no persisted `amount` field, so P1 derives it
- no explicit adjustment flag in normalized output yet
- no formal trading-calendar or suspension handling contract yet
- no repo-level minute bar persistence yet

## Error And Degradation Rules

- strict normalization raises `NormalizationError`
- non-strict normalization should prefer warnings over hard failure
- engine import or runtime failure returns the frozen schema with empty structure arrays
- skills and report generators should consume `summary` and `warnings`, not raw `czsc` objects

## Example

```python
from chantheory import analyze_tracker_klines

rows = [
    {"date": "2025-04-28", "open": 12.1, "close": 12.2, "high": 12.4, "low": 12.0, "volume": 1000},
    {"date": "2025-04-29", "open": 12.2, "close": 12.4, "high": 12.5, "low": 12.1, "volume": 1200},
]

result = analyze_tracker_klines(rows=rows, code="000001", market="sz")
payload = result.to_dict()
```

## Fixtures

Committed P2 fixtures:

- `packages/chantheory/tests/fixtures/p2_sample_rows.json`
- `packages/chantheory/tests/fixtures/p2_sample_result.json`
- `apps/chan-viewer/sample_data/000001_sz_day_rows.json`
