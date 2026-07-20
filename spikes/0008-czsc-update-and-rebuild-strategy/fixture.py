"""Deterministic A-share 5-minute fixture utilities for ADR 0008.

Timestamps represent the *end* of each closed five-minute bar.  The committed
fixture has exactly 500 warm-up bars followed by all 48 bars of one target day.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence


SYMBOL = "600584.SH"
SOURCE = "adr-0008-deterministic"
TIMEFRAME = "5m"
TARGET_DATE = date(2026, 7, 14)
WARM_BAR_COUNT = 500
TARGET_BAR_COUNT = 48
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "a_share_5m_548.json"
SSE_2026_CLOSED_DATES = {
    date(2026, 1, 1), date(2026, 1, 2),
    *{date(2026, 2, day) for day in range(15, 24)},
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    *{date(2026, 5, day) for day in range(1, 6)},
    date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
    date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27),
    *{date(2026, 10, day) for day in range(1, 8)},
}


def session_end_times() -> tuple[time, ...]:
    morning = tuple(time(9 + (35 + 5 * i) // 60, (35 + 5 * i) % 60) for i in range(24))
    afternoon = tuple(time(13 + (5 + 5 * i) // 60, (5 + 5 * i) % 60) for i in range(24))
    return morning + afternoon


def trading_days_ending_at(target: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = target - timedelta(days=1)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def generate_fixture() -> dict[str, object]:
    # Eleven full weekdays provide 528 bars; retaining the newest 500 makes the
    # first warm day a deliberate 20-bar partial-history boundary.
    warm_days = trading_days_ending_at(TARGET_DATE, 11)
    all_points = [(day, slot) for day in warm_days for slot in session_end_times()]
    warm_points = all_points[-WARM_BAR_COUNT:]
    target_points = [(TARGET_DATE, slot) for slot in session_end_times()]

    rows: list[dict[str, object]] = []
    previous_close = 42.0
    for index, (day, slot) in enumerate(warm_points + target_points):
        wave = 0.78 * math.sin(index / 7.0) + 0.34 * math.sin(index / 19.0)
        drift = index * 0.0025
        close = round(42.0 + drift + wave, 3)
        open_price = round(previous_close + 0.08 * math.sin(index / 3.0), 3)
        high = round(max(open_price, close) + 0.11 + 0.02 * (index % 4), 3)
        low = round(min(open_price, close) - 0.10 - 0.015 * (index % 5), 3)
        volume = float(100_000 + (index % 37) * 2_731 + (index % 11) * 997)
        amount = round(volume * ((open_price + close) / 2.0), 2)
        rows.append(
            {
                "timestamp": datetime.combine(day, slot).strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )
        previous_close = close

    return {
        "identity": {
            "symbol": SYMBOL,
            "source": SOURCE,
            "timeframe": TIMEFRAME,
            "timestamp_semantics": "bar_end",
            "warm_bar_count": WARM_BAR_COUNT,
            "target_date": TARGET_DATE.isoformat(),
            "target_bar_count": TARGET_BAR_COUNT,
            "generator": "fixture.py:v1",
        },
        "bars": rows,
    }


def canonical_fixture_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_fixture_bytes(payload)).hexdigest()


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def split_rows(payload: Mapping[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows = [dict(row) for row in payload["bars"]]  # type: ignore[index]
    return rows[:WARM_BAR_COUNT], rows[WARM_BAR_COUNT:]


def validate_fixture(payload: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    identity = payload.get("identity", {})
    rows = list(payload.get("bars", []))
    if identity.get("symbol") != SYMBOL or identity.get("source") != SOURCE or identity.get("timeframe") != TIMEFRAME:
        errors.append("identity must use the fixed symbol/source/timeframe")
    if identity.get("timestamp_semantics") != "bar_end":
        errors.append("timestamp semantics must be bar_end")
    if len(rows) != WARM_BAR_COUNT + TARGET_BAR_COUNT:
        errors.append(f"expected {WARM_BAR_COUNT + TARGET_BAR_COUNT} bars, got {len(rows)}")

    timestamps: list[datetime] = []
    for index, raw in enumerate(rows):
        row = dict(raw)
        try:
            dt = datetime.strptime(str(row["timestamp"]), "%Y-%m-%d %H:%M:%S")
            timestamps.append(dt)
            o, h, low, c = (float(row[key]) for key in ("open", "high", "low", "close"))
            volume, amount = float(row["volume"]), float(row["amount"])
            if h < max(o, c, low) or low > min(o, c, h):
                errors.append(f"invalid OHLC geometry at row {index}")
            if volume < 0 or amount < 0:
                errors.append(f"negative volume/amount at row {index}")
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"invalid row {index}: {exc}")

    if timestamps != sorted(timestamps):
        errors.append("timestamps are not strictly sorted")
    if len(timestamps) != len(set(timestamps)):
        errors.append("timestamps contain duplicates")

    valid_slots = set(session_end_times())
    for dt in timestamps:
        if dt.weekday() >= 5:
            errors.append(f"weekend bar: {dt}")
        if dt.date() in SSE_2026_CLOSED_DATES:
            errors.append(f"SSE holiday bar: {dt}")
        if dt.time() not in valid_slots:
            errors.append(f"non-session or lunch bar: {dt}")

    counts = Counter(dt.date() for dt in timestamps)
    if counts.get(TARGET_DATE) != TARGET_BAR_COUNT:
        errors.append("target day must contain exactly 48 bars")
    warm_counts = {day: count for day, count in counts.items() if day != TARGET_DATE}
    if sorted(warm_counts.values()) != [20] + [48] * 10:
        errors.append(f"warm daily counts must be one partial 20 plus ten full 48: {warm_counts}")

    warm, target = split_rows(payload)
    if len(warm) != 500 or len(target) != 48:
        errors.append("warm/target split is not 500/48")
    return errors


def iter_prefixes(payload: Mapping[str, object]) -> Iterable[tuple[int, list[dict[str, object]]]]:
    warm, target = split_rows(payload)
    yield 0, list(warm)
    for prefix in range(1, len(target) + 1):
        yield prefix, warm + target[:prefix]
