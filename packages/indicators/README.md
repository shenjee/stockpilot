# StockPilot Indicators

`packages/indicators/` provides reusable calculations over the standardized
StockPilot bar dictionaries. It has no provider, storage, UI, Live Session, or
Replay Session dependencies, so both runtime modes can call the same functions.

Public calculations:

- `calculate_moving_average`: full-window simple moving average.
- `calculate_boll`: BOLL 20 with population standard deviation and 2σ bands.
- `calculate_macd`: recursive EMA MACD 12/26/9 with a
  `2 * (DIF - DEA)` histogram.
- `calculate_volume_indicators`: raw volume plus VOL MA5/10.
- `calculate_intraday_vwap`: daily cumulative reported amount divided by daily
  cumulative volume.
- `calculate_five_minute_indicators` and
  `calculate_one_minute_indicators`: objects aligned with the frozen T+0 logical
  schema.

All series preserve every input timestamp. A value without sufficient warm-up
history is `None` (`null` after JSON serialization). Callers must calculate on
the complete available history and apply viewport slicing only afterwards.
The five-minute aggregate requires every input Bar to carry `closed: true`;
dynamic five-minute Bars are rejected and cannot enter formal indicators.

VWAP resets when the timestamp trade date changes. It is `None` while the
day's cumulative volume is zero; after the first positive-volume bar, a
zero-volume and zero-amount bar retains the current cumulative VWAP. A Bar with
zero volume but positive amount is rejected as inconsistent market data. The
calculation requires reported `amount` and never approximates it from a close
price.
