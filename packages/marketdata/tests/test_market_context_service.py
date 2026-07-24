import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from marketdata.services.market_context_service import (  # noqa: E402
    MarketContextError,
    MarketContextService,
    NonTradingDayError,
)


class MarketContextServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        # 2024 National Day closure and the following weekend are deliberately
        # absent.  The authoritative calendar resumes on Tuesday 2024-10-08.
        self.service = MarketContextService(
            ["2024-09-30", "2024-10-08", "2024-10-09"]
        )

    def test_calendar_skips_weekend_and_exchange_holiday(self):
        self.assertTrue(self.service.is_trading_day("2024-09-30", "sh"))
        self.assertFalse(self.service.is_trading_day("2024-10-01", "sh"))
        self.assertFalse(self.service.is_trading_day("2024-10-05", "sz"))
        self.assertEqual(
            self.service.next_trading_day("2024-09-30", "sh"),
            date(2024, 10, 8),
        )
        self.assertEqual(
            self.service.previous_trading_day("2024-10-08", "sz"),
            date(2024, 9, 30),
        )
        self.assertEqual(
            self.service.trading_days_between(
                "2024-09-30",
                "2024-10-09",
                "sh",
            ),
            (date(2024, 9, 30), date(2024, 10, 8), date(2024, 10, 9)),
        )

    def test_closed_date_has_no_session_and_required_session_fails(self):
        self.assertIsNone(self.service.session_on("2024-10-01", "sh"))
        with self.assertRaisesRegex(NonTradingDayError, "not a sh trading day"):
            self.service.require_session("2024-10-01", "sh")

    def test_date_outside_authoritative_coverage_is_not_called_a_holiday(self):
        with self.assertRaisesRegex(MarketContextError, "outside calendar coverage"):
            self.service.is_trading_day("2024-10-10", "sh")
        with self.assertRaisesRegex(MarketContextError, "outside calendar coverage"):
            self.service.require_session("2024-09-29", "sz")

    def test_explicit_coverage_can_include_closed_dates_at_the_edges(self):
        service = MarketContextService(
            ["2024-09-30", "2024-10-08"],
            coverage_start="2024-09-28",
            coverage_end="2024-10-10",
        )
        self.assertFalse(service.is_trading_day("2024-09-29", "sh"))
        self.assertFalse(service.is_trading_day("2024-10-10", "sh"))

    def test_normal_shanghai_and_shenzhen_end_boundary_is_1500(self):
        for market in ("sh", "sz"):
            session = self.service.require_session("2024-09-30", market)
            self.assertEqual(session.start, datetime(2024, 9, 30, 9, 30))
            self.assertEqual(session.end, datetime(2024, 9, 30, 15, 0))
            self.assertEqual(
                session.to_dict()["end_time"],
                "2024-09-30 15:00:00",
            )

    def test_session_phase_crosses_lunch_without_treating_it_as_trading(self):
        session = self.service.require_session("2024-09-30", "sh")
        self.assertEqual(session.phase_at("2024-09-30 09:29:59"), "pre_open")
        self.assertEqual(session.phase_at("2024-09-30 11:30:00"), "morning")
        self.assertEqual(session.phase_at("2024-09-30 11:30:01"), "lunch_break")
        self.assertEqual(session.phase_at("2024-09-30 12:59:59"), "lunch_break")
        self.assertEqual(session.phase_at("2024-09-30 13:00:00"), "afternoon")
        self.assertEqual(session.phase_at("2024-09-30 15:00:01"), "closed")

    def test_nominal_bar_boundaries_exclude_lunch_placeholders(self):
        session = self.service.require_session("2024-09-30", "sz")
        one_minute = session.bar_close_times(1)
        five_minute = session.bar_close_times(5)

        self.assertEqual(len(one_minute), 240)
        self.assertEqual(one_minute[0], datetime(2024, 9, 30, 9, 31))
        self.assertEqual(one_minute[119], datetime(2024, 9, 30, 11, 30))
        self.assertEqual(one_minute[120], datetime(2024, 9, 30, 13, 1))
        self.assertEqual(one_minute[-1], datetime(2024, 9, 30, 15, 0))

        self.assertEqual(len(five_minute), 48)
        self.assertEqual(five_minute[0], datetime(2024, 9, 30, 9, 35))
        self.assertEqual(five_minute[23], datetime(2024, 9, 30, 11, 30))
        self.assertEqual(five_minute[24], datetime(2024, 9, 30, 13, 5))
        self.assertEqual(five_minute[-1], datetime(2024, 9, 30, 15, 0))

    def test_replay_advances_only_to_actual_bars_across_lunch_and_suspension(self):
        session = self.service.require_session("2024-09-30", "sh")
        actual = [
            "2024-09-30 11:29:00",
            "2024-09-30 11:30:00",
            "2024-09-30 13:01:00",
            # A long security-specific halt/data gap is represented by absence.
            "2024-09-30 14:47:00",
        ]

        self.assertEqual(
            session.next_actual_bar_time("2024-09-30 11:30:00", actual),
            datetime(2024, 9, 30, 13, 1),
        )
        self.assertEqual(
            session.next_actual_bar_time("2024-09-30 13:01:00", actual),
            datetime(2024, 9, 30, 14, 47),
        )
        self.assertIsNone(
            session.next_actual_bar_time("2024-09-30 14:47:00", actual)
        )
        # Missing tail data never changes the exchange-defined close boundary.
        self.assertEqual(session.end, datetime(2024, 9, 30, 15, 0))

    def test_initial_replay_cursor_does_not_skip_actual_0930_bar(self):
        session = self.service.require_session("2024-09-30", "sh")
        actual = [
            "2024-09-30 09:30:00",
            "2024-09-30 09:31:00",
        ]

        self.assertEqual(
            session.next_actual_bar_time(
                "2024-09-30 09:30:00",
                actual,
                current_time_consumed=False,
            ),
            datetime(2024, 9, 30, 9, 30),
        )
        self.assertEqual(
            session.next_actual_bar_time(
                "2024-09-30 09:30:00",
                actual,
                current_time_consumed=True,
            ),
            datetime(2024, 9, 30, 9, 31),
        )

    def test_invalid_market_interval_and_out_of_session_bar_are_rejected(self):
        with self.assertRaisesRegex(MarketContextError, "sh and sz"):
            self.service.is_trading_day("2024-09-30", "bj")
        session = self.service.require_session("2024-09-30", "sh")
        with self.assertRaisesRegex(MarketContextError, "1 or 5"):
            session.bar_close_times(15)
        with self.assertRaisesRegex(MarketContextError, "trading period"):
            session.next_actual_bar_time(
                "2024-09-30 11:30:00",
                ["2024-09-30 12:00:00"],
            )

    def test_invalid_calendar_coverage_and_weekend_open_day_are_rejected(self):
        with self.assertRaisesRegex(MarketContextError, "cannot include weekends"):
            MarketContextService(["2024-10-05"])
        with self.assertRaisesRegex(MarketContextError, "must not exceed"):
            MarketContextService(
                ["2024-09-30"],
                coverage_start="2024-10-01",
                coverage_end="2024-09-29",
            )
        with self.assertRaisesRegex(MarketContextError, "inside the calendar coverage"):
            MarketContextService(
                ["2024-09-30"],
                coverage_start="2024-10-01",
                coverage_end="2024-10-02",
            )


if __name__ == "__main__":
    unittest.main()
