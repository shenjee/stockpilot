import math
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from indicators import (  # noqa: E402
    IndicatorInputError,
    calculate_boll,
    calculate_five_minute_indicators,
    calculate_intraday_vwap,
    calculate_macd,
    calculate_moving_average,
    calculate_one_minute_indicators,
)


def make_bars(count: int) -> list[dict]:
    start = datetime(2026, 7, 1, 9, 30)
    return [
        {
            "timestamp": (start + timedelta(minutes=5 * index)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "close": float(index + 1),
            "volume": float((index + 1) * 10),
            "amount": float((index + 1) * 100),
            "closed": True,
        }
        for index in range(count)
    ]


class MovingAverageAndBollTests(unittest.TestCase):
    def test_ma_preserves_length_timestamps_and_null_warmup(self):
        bars = make_bars(10)
        points = calculate_moving_average(bars, 5)

        self.assertEqual(len(points), len(bars))
        self.assertEqual(
            [point["timestamp"] for point in points],
            [bar["timestamp"] for bar in bars],
        )
        self.assertEqual([point["value"] for point in points[:4]], [None] * 4)
        self.assertEqual(points[4]["value"], 3.0)
        self.assertEqual(points[-1]["value"], 8.0)

    def test_boll_uses_population_standard_deviation(self):
        bars = make_bars(20)
        boll = calculate_boll(bars)
        expected_deviation = math.sqrt(33.25)

        self.assertEqual(boll["period"], 20)
        self.assertEqual(boll["stddev"], 2.0)
        self.assertEqual(
            [point["value"] for point in boll["middle"][:19]], [None] * 19
        )
        self.assertAlmostEqual(boll["middle"][-1]["value"], 10.5)
        self.assertAlmostEqual(
            boll["upper"][-1]["value"], 10.5 + 2 * expected_deviation
        )
        self.assertAlmostEqual(
            boll["lower"][-1]["value"], 10.5 - 2 * expected_deviation
        )


class MacdTests(unittest.TestCase):
    def test_macd_warmup_alignment_and_histogram_scale_are_frozen(self):
        bars = make_bars(40)
        macd = calculate_macd(bars)

        self.assertEqual(macd["fast_period"], 12)
        self.assertEqual(macd["slow_period"], 26)
        self.assertEqual(macd["signal_period"], 9)
        for series in ("dif", "dea", "histogram"):
            self.assertEqual(len(macd[series]), len(bars))
        self.assertEqual([point["value"] for point in macd["dif"][:25]], [None] * 25)
        self.assertEqual([point["value"] for point in macd["dea"][:33]], [None] * 33)
        self.assertEqual(
            [point["value"] for point in macd["histogram"][:33]], [None] * 33
        )

        self.assertAlmostEqual(macd["dif"][25]["value"], 5.259225480173228)
        self.assertAlmostEqual(macd["dea"][33]["value"], 5.737527795403894)
        self.assertAlmostEqual(macd["histogram"][33]["value"], 0.5971045543844262)
        self.assertAlmostEqual(
            macd["histogram"][33]["value"],
            2 * (macd["dif"][33]["value"] - macd["dea"][33]["value"]),
        )


class AggregateContractTests(unittest.TestCase):
    def test_five_minute_object_has_frozen_fields_and_aligned_lengths(self):
        bars = make_bars(60)
        result = calculate_five_minute_indicators(bars)

        self.assertEqual(set(result), {"ma", "boll", "volume", "macd"})
        self.assertEqual(
            set(result["ma"]), {"ma5", "ma10", "ma20", "ma30", "ma60"}
        )
        self.assertEqual(
            set(result["boll"]),
            {"period", "stddev", "upper", "middle", "lower"},
        )
        self.assertEqual(set(result["volume"]), {"values", "ma5", "ma10"})
        self.assertEqual(
            set(result["macd"]),
            {
                "fast_period",
                "slow_period",
                "signal_period",
                "dif",
                "dea",
                "histogram",
            },
        )
        series = [
            *result["ma"].values(),
            result["boll"]["upper"],
            result["boll"]["middle"],
            result["boll"]["lower"],
            *result["volume"].values(),
            result["macd"]["dif"],
            result["macd"]["dea"],
            result["macd"]["histogram"],
        ]
        for points in series:
            self.assertEqual(len(points), len(bars))
            self.assertEqual(
                [point["timestamp"] for point in points],
                [bar["timestamp"] for bar in bars],
            )

    def test_one_minute_object_has_frozen_fields(self):
        bars = make_bars(40)
        result = calculate_one_minute_indicators(bars)

        self.assertEqual(set(result), {"vwap", "volume", "macd"})
        self.assertEqual(set(result["volume"]), {"values"})
        self.assertEqual(len(result["vwap"]), len(bars))
        self.assertEqual(len(result["volume"]["values"]), len(bars))
        self.assertEqual(len(result["macd"]["histogram"]), len(bars))

    def test_five_minute_object_rejects_dynamic_or_unspecified_closed_state(self):
        dynamic_bars = make_bars(5)
        dynamic_bars[-1]["closed"] = False
        with self.assertRaisesRegex(IndicatorInputError, "formally closed"):
            calculate_five_minute_indicators(dynamic_bars)

        missing_state_bars = make_bars(5)
        del missing_state_bars[-1]["closed"]
        with self.assertRaisesRegex(IndicatorInputError, "supplied explicitly"):
            calculate_five_minute_indicators(missing_state_bars)


class VwapTests(unittest.TestCase):
    def test_vwap_resets_daily_and_has_explicit_zero_volume_behavior(self):
        bars = [
            {
                "timestamp": "2026-07-23 09:30:00",
                "close": 10,
                "volume": 0,
                "amount": 0,
            },
            {
                "timestamp": "2026-07-23 09:31:00",
                "close": 10,
                "volume": 10,
                "amount": 100,
            },
            {
                "timestamp": "2026-07-23 09:32:00",
                "close": 11,
                "volume": 0,
                "amount": 0,
            },
            {
                "timestamp": "2026-07-23 09:33:00",
                "close": 22,
                "volume": 10,
                "amount": 220,
            },
            {
                "timestamp": "2026-07-24 09:30:00",
                "close": 12,
                "volume": 0,
                "amount": 0,
            },
            {
                "timestamp": "2026-07-24 09:31:00",
                "close": 12,
                "volume": 5,
                "amount": 60,
            },
        ]

        points = calculate_intraday_vwap(bars)

        self.assertEqual(
            [point["value"] for point in points],
            [None, 10.0, 10.0, 16.0, None, 12.0],
        )
        self.assertEqual(
            [point["timestamp"] for point in points],
            [bar["timestamp"] for bar in bars],
        )

    def test_vwap_uses_reported_amount_not_close_approximation(self):
        bars = [
            {
                "timestamp": "2026-07-24 09:30:00",
                "close": 99.0,
                "volume": 10,
                "amount": 120.0,
            }
        ]
        self.assertEqual(calculate_intraday_vwap(bars)[0]["value"], 12.0)

    def test_vwap_rejects_positive_amount_with_zero_volume(self):
        bars = [
            {
                "timestamp": "2026-07-24 09:30:00",
                "close": 10.0,
                "volume": 0,
                "amount": 1.0,
            }
        ]
        with self.assertRaisesRegex(
            IndicatorInputError, "amount must be zero when volume is zero"
        ):
            calculate_intraday_vwap(bars)


class InputValidationTests(unittest.TestCase):
    def test_rejects_unsorted_duplicate_or_invalid_values(self):
        with self.assertRaisesRegex(IndicatorInputError, "strictly increasing"):
            calculate_moving_average(
                [
                    {"timestamp": "2026-07-24 09:31:00", "close": 1},
                    {"timestamp": "2026-07-24 09:30:00", "close": 2},
                ],
                2,
            )
        with self.assertRaisesRegex(IndicatorInputError, "strictly increasing"):
            calculate_moving_average(
                [
                    {"timestamp": "2026-07-24 09:30:00", "close": 1},
                    {"timestamp": "2026-07-24 09:30:00", "close": 2},
                ],
                2,
            )
        with self.assertRaisesRegex(IndicatorInputError, "finite"):
            calculate_moving_average(
                [{"timestamp": "2026-07-24 09:30:00", "close": float("nan")}],
                2,
            )
        with self.assertRaisesRegex(IndicatorInputError, "non-negative"):
            calculate_intraday_vwap(
                [
                    {
                        "timestamp": "2026-07-24 09:30:00",
                        "close": 1,
                        "volume": -1,
                        "amount": 1,
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
