import unittest
from collections.abc import Mapping

from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime import (
    DynamicFiveMinuteAggregator,
    RuntimeMarketDataError,
    build_dynamic_daily_bar,
    project_market_at,
    project_quote_at,
)


def bar(
    timestamp,
    open_price,
    high,
    low,
    close,
    volume,
    amount,
    *,
    closed=True,
):
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def quote(timestamp, latest_price, *, volume_ratio=1.2):
    return {
        "timestamp": timestamp,
        "latest_price": latest_price,
        "change_percent": (latest_price - 10) * 10,
        "open": 10.0,
        "high": max(10.0, latest_price),
        "low": min(10.0, latest_price),
        "previous_close": 10.0,
        "volume": 1_000,
        "amount": 10_000,
        "volume_ratio": volume_ratio,
        "order_imbalance": None,
        "turnover_rate": 0.5,
    }


class TimestampOnlyFutureRow(Mapping):
    """A future input that fails if projection reads any market value."""

    def __init__(self, timestamp):
        self.timestamp = timestamp

    def __getitem__(self, key):
        if key == "timestamp":
            return self.timestamp
        raise AssertionError(f"future field was read: {key}")

    def __iter__(self):
        return iter(("timestamp",))

    def __len__(self):
        return 1

    def get(self, key, default=None):
        if key == "timestamp":
            return self.timestamp
        if key == "date":
            return default
        raise AssertionError(f"future field was read: {key}")


class DynamicFiveMinuteAggregatorTests(unittest.TestCase):
    def setUp(self):
        calendar = MarketContextService(["2026-07-24"])
        self.session = calendar.require_session("2026-07-24", "sh")
        self.aggregator = DynamicFiveMinuteAggregator(self.session)

    def test_one_minute_updates_display_only_dynamic_bar(self):
        self.aggregator.update_one_minute(
            bar("2026-07-24 09:31:00", 10.0, 10.2, 9.9, 10.1, 100, 1_000)
        )
        dynamic = self.aggregator.update_one_minute(
            bar("2026-07-24 09:32:00", 10.1, 10.4, 10.0, 10.3, 250, 2_550)
        )

        self.assertEqual(
            dynamic,
            bar(
                "2026-07-24 09:35:00",
                10.0,
                10.4,
                9.9,
                10.3,
                350,
                3_550,
                closed=False,
            ),
        )
        self.assertEqual(self.aggregator.display_bars, (dynamic,))
        self.assertEqual(self.aggregator.analysis_bars, ())

    def test_official_bar_replaces_dynamic_and_is_only_analysis_input(self):
        self.aggregator.update_one_minute(
            bar("2026-07-24 09:31:00", 10.0, 10.2, 9.9, 10.1, 100, 1_000)
        )
        official = bar(
            "2026-07-24 09:35:00", 10.0, 10.5, 9.8, 10.4, 900, 9_200
        )

        self.assertEqual(self.aggregator.accept_official(official), official)
        self.assertEqual(self.aggregator.dynamic_bars, ())
        self.assertEqual(self.aggregator.display_bars, (official,))
        self.assertEqual(self.aggregator.analysis_bars, (official,))

        # A late 1m revision cannot overwrite the authoritative official bar.
        affected = self.aggregator.update_one_minute(
            bar("2026-07-24 09:34:00", 99, 99, 99, 99, 1, 99)
        )
        self.assertEqual(affected, official)
        self.assertEqual(self.aggregator.display_bars, (official,))
        self.assertEqual(self.aggregator._one_minute, {})

    def test_boundary_minutes_close_the_current_five_minute_bucket(self):
        morning = self.aggregator.update_one_minute(
            bar("2026-07-24 09:35:00", 10, 10, 10, 10, 1, 10)
        )
        afternoon = self.aggregator.update_one_minute(
            bar("2026-07-24 13:05:00", 11, 11, 11, 11, 2, 22)
        )

        self.assertEqual(morning["timestamp"], "2026-07-24 09:35:00")
        self.assertEqual(afternoon["timestamp"], "2026-07-24 13:05:00")

    def test_only_current_dynamic_bar_survives_delayed_official_input(self):
        self.aggregator.update_one_minute(
            bar("2026-07-24 09:35:00", 10, 10, 10, 10, 1, 10)
        )
        current = self.aggregator.update_one_minute(
            bar("2026-07-24 09:36:00", 11, 11, 11, 11, 2, 22)
        )

        self.assertEqual(self.aggregator.dynamic_bars, (current,))
        self.assertEqual(current["timestamp"], "2026-07-24 09:40:00")

        with self.assertRaisesRegex(RuntimeMarketDataError, "expired 5m bucket"):
            self.aggregator.update_one_minute(
                bar("2026-07-24 09:34:00", 9, 9, 9, 9, 1, 9)
            )
        self.assertEqual(self.aggregator.dynamic_bars, (current,))

        delayed_official = bar(
            "2026-07-24 09:35:00", 10, 10.2, 9.9, 10.1, 5, 51
        )
        self.aggregator.accept_official(delayed_official)
        self.assertEqual(
            self.aggregator.display_bars,
            (delayed_official, current),
        )
        self.assertEqual(self.aggregator.analysis_bars, (delayed_official,))

    def test_lunch_is_skipped_without_placeholder_bars(self):
        self.aggregator.update_one_minute(
            bar("2026-07-24 11:30:00", 10, 10, 10, 10, 1, 10)
        )
        morning = self.aggregator.accept_official(
            bar("2026-07-24 11:30:00", 10, 10, 10, 10, 1, 10)
        )
        afternoon = self.aggregator.update_one_minute(
            bar("2026-07-24 13:01:00", 11, 11, 11, 11, 2, 22)
        )

        self.assertEqual(morning["timestamp"], "2026-07-24 11:30:00")
        self.assertEqual(afternoon["timestamp"], "2026-07-24 13:05:00")
        self.assertEqual(
            [item["timestamp"] for item in self.aggregator.display_bars],
            ["2026-07-24 11:30:00", "2026-07-24 13:05:00"],
        )

    def test_invalid_dynamic_and_out_of_session_inputs_are_rejected(self):
        with self.assertRaisesRegex(RuntimeMarketDataError, "closed"):
            self.aggregator.update_one_minute(
                bar(
                    "2026-07-24 09:31:00",
                    10,
                    10,
                    10,
                    10,
                    1,
                    10,
                    closed=False,
                )
            )
        with self.assertRaisesRegex(RuntimeMarketDataError, "trading periods"):
            self.aggregator.update_one_minute(
                bar("2026-07-24 12:01:00", 10, 10, 10, 10, 1, 10)
            )
        with self.assertRaisesRegex(RuntimeMarketDataError, "close boundary"):
            self.aggregator.accept_official(
                bar("2026-07-24 09:34:00", 10, 10, 10, 10, 1, 10)
            )


class TargetTimeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.bars = [
            bar("2026-07-24 09:31:00", 10.0, 10.2, 9.9, 10.1, 100, 1_000),
            bar("2026-07-24 09:32:00", 10.1, 10.5, 10.0, 10.4, 200, 2_050),
            bar("2026-07-24 09:33:00", 10.4, 10.8, 10.3, 10.7, 300, 3_150),
        ]

    def test_dynamic_daily_bar_uses_only_occurred_one_minute_prefix(self):
        daily = build_dynamic_daily_bar(
            [*self.bars, TimestampOnlyFutureRow("2026-07-24 14:59:00")],
            trade_date="2026-07-24",
            target_time="2026-07-24 09:32:00",
        )

        self.assertEqual(
            daily,
            bar(
                "2026-07-24",
                10.0,
                10.5,
                9.9,
                10.4,
                300,
                3_050,
                closed=False,
            ),
        )

    def test_replay_quote_derives_core_and_keeps_missing_fields_null(self):
        projected = project_quote_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:32:00",
            previous_close=9.8,
        )

        self.assertEqual(projected["timestamp"], "2026-07-24 09:32:00")
        self.assertEqual(projected["latest_price"], 10.4)
        self.assertAlmostEqual(projected["change_percent"], (10.4 - 9.8) / 9.8 * 100)
        self.assertEqual(projected["volume"], 300)
        self.assertEqual(projected["amount"], 3_050)
        self.assertIsNone(projected["volume_ratio"])
        self.assertIsNone(projected["order_imbalance"])
        self.assertIsNone(projected["turnover_rate"])

    def test_future_quote_snapshot_is_not_read_or_used(self):
        projected = project_quote_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:32:00",
            previous_close=10.0,
            quote_snapshots=[TimestampOnlyFutureRow("2026-07-24 15:00:00")],
        )

        self.assertEqual(projected["latest_price"], 10.4)
        self.assertIsNone(projected["volume_ratio"])
        self.assertIsNone(projected["turnover_rate"])

    def test_eligible_snapshot_fields_are_used_without_overriding_newer_bars(self):
        projected = project_quote_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:32:00",
            previous_close=None,
            quote_snapshots=[quote("2026-07-24 09:31:30", 10.15, volume_ratio=1.8)],
        )

        self.assertEqual(projected["timestamp"], "2026-07-24 09:32:00")
        self.assertEqual(projected["latest_price"], 10.4)
        self.assertEqual(projected["previous_close"], 10.0)
        self.assertEqual(projected["volume_ratio"], 1.8)
        self.assertEqual(projected["turnover_rate"], 0.5)

    def test_fresher_eligible_snapshot_is_authoritative(self):
        snapshot = quote("2026-07-24 09:32:30", 10.45, volume_ratio=2.0)
        projected = project_quote_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:32:30",
            previous_close=10.0,
            quote_snapshots=[snapshot],
        )
        self.assertEqual(projected, snapshot)

    def test_previous_close_is_validated_even_when_snapshot_is_authoritative(self):
        with self.assertRaisesRegex(RuntimeMarketDataError, "previous_close"):
            project_quote_at(
                self.bars,
                trade_date="2026-07-24",
                target_time="2026-07-24 09:32:30",
                previous_close=-1,
                quote_snapshots=[quote("2026-07-24 09:32:30", 10.45)],
            )

    def test_combined_projection_uses_one_target_prefix(self):
        projection = project_market_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:31:00",
            previous_close=10.0,
        ).to_dict()

        self.assertEqual(projection["daily_bar"]["close"], 10.1)
        self.assertEqual(projection["quote"]["latest_price"], 10.1)
        self.assertEqual(projection["quote"]["timestamp"], "2026-07-24 09:31:00")

    def test_no_eligible_market_input_returns_null_projection(self):
        projection = project_market_at(
            self.bars,
            trade_date="2026-07-24",
            target_time="2026-07-24 09:30:00",
            previous_close=None,
        )
        self.assertIsNone(projection.daily_bar)
        self.assertIsNone(projection.quote)


if __name__ == "__main__":
    unittest.main()
