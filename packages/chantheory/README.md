# chantheory

`chantheory` is the project-owned adapter layer for Stock Pilot Phase 2.

It is not a reimplementation of Chan Theory core logic. Its job is to:

- normalize project OHLCV input,
- pin and probe the underlying `czsc` engine,
- freeze a project-level schema for skills, agents, and apps,
- define plotting, summary, and warning responsibilities,
- degrade safely when the engine or input is not ready.

## P1 Status

Phase 2 P1 freezes the adapter contract and validates a working `czsc` path.

- Engine: `czsc==0.9.63`
- Validation runtime: Python `3.9.6`
- Why this pin: latest `0.10.x` releases on PyPI require Python `>=3.10`
- Import note: load `numpy.typing` before importing `czsc` on Python `3.9`

Observed validation result:

- `czsc==0.9.63` installs successfully in a clean Python `3.9` virtualenv
- the current tracker day-bar payload converts into `czsc.objects.RawBar`
- `CZSC(raw_bars)` runs successfully for A-share sample data after the `numpy.typing` import shim

## Public API

Current public entry points:

- `normalize_ohlcv_rows(...)`
- `normalize_tracker_klines(...)`
- `analyze(...)`
- `analyze_tracker_klines(...)`
- `analyze_normalized(...)`

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

Current repo validation only covers day bars because `china-stock-daily-tracker` persists day K-lines today.

## Frozen Result Schema

P1 freezes the top-level schema even though P2 will fill in the structure mapping.

```json
{
  "symbol": "000001.SZ",
  "timeframe": "day",
  "source": "tencent",
  "engine": "czsc",
  "engine_version": "0.9.63",
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
  "candidate_buy_points": [],
  "candidate_sell_points": [],
  "plot_primitives": [],
  "summary": [],
  "warnings": [],
  "meta": {}
}
```

Responsibilities are split as follows:

- `plot_primitives`: visualization-ready points, lines, boxes, labels, markers
- `summary`: short sentences for skills and agents
- `warnings`: normalization gaps, engine failures, and runtime degradation details

## Current Data Fit

Current `china-stock-daily-tracker` day-bar records already provide:

- `date`
- `open`
- `close`
- `high`
- `low`
- `volume`

Current gaps against ideal `czsc` input metadata:

- no persisted `amount` field, so P1 derives it
- no explicit adjustment flag in normalized output yet
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
