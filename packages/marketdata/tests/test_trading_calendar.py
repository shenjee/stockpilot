"""Unit tests for the read-only TradingCalendar (issue #133).

These tests exercise the calendar against the bundled 2026 JSON files and
cover the acceptance range from the #133 spec:

* sh/sz normal weekdays are trading days.
* sh/sz Spring Festival and National Day weekdays are closed.
* Saturdays and Sundays are closed.
* HK full-day holidays are closed.
* HK half-day dates are trading days but only carry the morning session.
* sh and hk can disagree on the same calendar day.
* previous/next trading day span a long holiday.
* A missing market or missing year file raises CalendarUnavailableError.
* The calendar can feed the existing MarketContextService (#133 section 3).
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from packages.marketdata.trading_calendar import (  # noqa: E402
    CalendarUnavailableError,
    TradingCalendar,
)
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)


class TradingCalendarCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = TradingCalendar()

    # -- spec examples -----------------------------------------------------

    def test_hk_lunar_new_year_eve_is_half_day_trading(self) -> None:
        # Feb 16 2026 (Mon) is the HK LNY eve: half-day, therefore a trading day.
        self.assertTrue(self.calendar.is_trading_day("2026-02-16", "hk"))
        self.assertEqual(
            int(self.calendar.is_trading_day("2026-02-16", "hk")),
            1,
        )

    def test_hk_lunar_new_year_day_is_closed(self) -> None:
        # Feb 17 2026 (Tue) is a full HK closure.
        self.assertFalse(self.calendar.is_trading_day("2026-02-17", "hk"))
        self.assertEqual(
            int(self.calendar.is_trading_day("2026-02-17", "hk")),
            0,
        )

    def test_sh_normal_weekday_sessions(self) -> None:
        # Aug 4 2026 (Tue) is a normal A-share trading day.
        self.assertEqual(
            self.calendar.sessions_on("2026-08-04", "sh"),
            ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))),
        )

    def test_hk_normal_weekday_sessions(self) -> None:
        self.assertEqual(
            self.calendar.sessions_on("2026-08-04", "hk"),
            ((time(9, 30), time(12, 0)), (time(13, 0), time(16, 0))),
        )

    def test_hk_christmas_eve_is_half_day_session(self) -> None:
        self.assertEqual(
            self.calendar.sessions_on("2026-12-24", "hk"),
            ((time(9, 30), time(12, 0)),),
        )

    def test_hk_christmas_day_is_closed_with_empty_sessions(self) -> None:
        self.assertFalse(self.calendar.is_trading_day("2026-12-25", "hk"))
        self.assertEqual(self.calendar.sessions_on("2026-12-25", "hk"), ())

    # -- sh/sz normal / holiday / weekend ----------------------------------

    def test_sh_sz_normal_weekday_is_trading_day(self) -> None:
        for market in ("sh", "sz"):
            self.assertTrue(self.calendar.is_trading_day("2026-08-04", market))

    def test_sh_sz_spring_festival_weekdays_are_closed(self) -> None:
        # Feb 16-23 2026 (Mon-Mon) is the A-share Spring Festival closure.
        for market in ("sh", "sz"):
            for day in ("2026-02-16", "2026-02-17", "2026-02-20", "2026-02-23"):
                self.assertFalse(
                    self.calendar.is_trading_day(day, market),
                    f"{market} {day} should be closed for Spring Festival",
                )

    def test_sh_sz_new_year_bridge_is_closed(self) -> None:
        # Jan 2 2026 (Fri) is a New Year bridge holiday in the A-share calendar.
        for market in ("sh", "sz"):
            self.assertFalse(self.calendar.is_trading_day("2026-01-02", market))

    def test_sh_sz_national_day_weekdays_are_closed(self) -> None:
        # Oct 1-7 2026 weekdays are the National Day / Mid-Autumn closure.
        # Oct 8 (Thu) resumes trading in the authoritative 2026 calendar.
        for market in ("sh", "sz"):
            self.assertFalse(self.calendar.is_trading_day("2026-10-01", market))
            self.assertFalse(self.calendar.is_trading_day("2026-10-07", market))
            self.assertTrue(self.calendar.is_trading_day("2026-10-08", market))

    def test_weekends_are_closed_for_all_markets(self) -> None:
        # Aug 8/9 2026 is a Saturday/Sunday.
        for market in ("sh", "sz", "hk"):
            self.assertFalse(self.calendar.is_trading_day("2026-08-08", market))
            self.assertFalse(self.calendar.is_trading_day("2026-08-09", market))
            self.assertEqual(self.calendar.sessions_on("2026-08-08", market), ())

    # -- hk full-day closure and same-day divergence -----------------------

    def test_hk_sarl_establishment_day_closed_while_sh_open(self) -> None:
        # Jul 1 2026 (Wed): HK closed (SAR Establishment Day), sh open.
        self.assertFalse(self.calendar.is_trading_day("2026-07-01", "hk"))
        self.assertTrue(self.calendar.is_trading_day("2026-07-01", "sh"))
        self.assertEqual(self.calendar.sessions_on("2026-07-01", "hk"), ())

    # -- previous / next trading day across long holidays ------------------

    def test_next_trading_day_spans_national_day_holiday(self) -> None:
        # Sep 30 2026 (Wed) -> next open day is Oct 8 (Thu), skipping Oct 1-7.
        self.assertEqual(
            self.calendar.next_trading_day("2026-09-30", "sh"),
            date(2026, 10, 8),
        )

    def test_previous_trading_day_spans_national_day_holiday(self) -> None:
        # Oct 8 (Thu) resumes trading; its previous open day is Sep 30 (Wed).
        self.assertEqual(
            self.calendar.previous_trading_day("2026-10-08", "sh"),
            date(2026, 9, 30),
        )

    def test_previous_trading_day_spans_spring_festival(self) -> None:
        # Feb 24 2026 (Tue) resumes after Spring Festival (Feb 16-23 closed);
        # its previous open day is Feb 13 (Fri).
        self.assertEqual(
            self.calendar.previous_trading_day("2026-02-24", "sh"),
            date(2026, 2, 13),
        )

    def test_previous_trading_day_excludes_given_trading_day(self) -> None:
        self.assertEqual(
            self.calendar.previous_trading_day("2026-08-04", "sh"),
            date(2026, 8, 3),
        )

    def test_next_trading_day_excludes_given_trading_day(self) -> None:
        self.assertEqual(
            self.calendar.next_trading_day("2026-08-04", "sh"),
            date(2026, 8, 5),
        )

    # -- trading_days_between ----------------------------------------------

    def test_trading_days_between_full_year_sh_count(self) -> None:
        # 2026 has 261 weekdays minus 19 A-share holidays = 242 trading days.
        days = self.calendar.trading_days_between(
            "2026-01-01",
            "2026-12-31",
            "sh",
        )
        self.assertEqual(len(days), 242)
        # Jan 1 (Thu) and Jan 2 (Fri) are both holidays; first open day is Jan 5 (Mon).
        self.assertEqual(days[0], date(2026, 1, 5))
        self.assertEqual(days[-1], date(2026, 12, 31))

    def test_trading_days_between_full_year_hk_count(self) -> None:
        # 261 weekdays minus 14 HK holidays = 247 trading days (half-days count).
        days = self.calendar.trading_days_between(
            "2026-01-01",
            "2026-12-31",
            "hk",
        )
        self.assertEqual(len(days), 247)

    def test_trading_days_between_rejects_inverted_range(self) -> None:
        with self.assertRaises(CalendarUnavailableError):
            self.calendar.trading_days_between("2026-08-05", "2026-08-04", "sh")

    # -- error handling ----------------------------------------------------

    def test_missing_year_file_raises(self) -> None:
        with self.assertRaisesRegex(CalendarUnavailableError, "year 2027"):
            self.calendar.is_trading_day("2027-01-04", "sh")

    def test_missing_year_file_in_next_trading_day_raises(self) -> None:
        # Walking forward from end of 2026 needs 2027 data, which is absent.
        with self.assertRaises(CalendarUnavailableError):
            self.calendar.next_trading_day("2026-12-31", "sh")

    def test_unsupported_market_raises(self) -> None:
        with self.assertRaisesRegex(CalendarUnavailableError, "unsupported market"):
            self.calendar.is_trading_day("2026-08-04", "bj")

    def test_date_object_and_string_are_equivalent(self) -> None:
        self.assertEqual(
            self.calendar.is_trading_day(date(2026, 8, 4), "sh"),
            self.calendar.is_trading_day("2026-08-04", "sh"),
        )

    def test_market_code_is_case_insensitive(self) -> None:
        self.assertTrue(self.calendar.is_trading_day("2026-08-04", "SH"))
        self.assertTrue(self.calendar.is_trading_day("2026-08-04", "Hk"))

    def test_sessions_on_holiday_returns_empty(self) -> None:
        # Jan 1 2026 is a holiday for all three markets.
        for market in ("sh", "sz", "hk"):
            self.assertEqual(self.calendar.sessions_on("2026-01-01", market), ())

    def test_half_day_date_is_trading_day_with_morning_only(self) -> None:
        self.assertTrue(self.calendar.is_trading_day("2026-12-24", "hk"))
        self.assertEqual(
            self.calendar.sessions_on("2026-12-24", "hk"),
            ((time(9, 30), time(12, 0)),),
        )

    def test_hk_new_year_eve_is_half_day(self) -> None:
        # Dec 31 2026 is an HK half-day session.
        self.assertTrue(self.calendar.is_trading_day("2026-12-31", "hk"))
        self.assertEqual(
            self.calendar.sessions_on("2026-12-31", "hk"),
            ((time(9, 30), time(12, 0)),),
        )

    def test_available_years_lists_shipped_json(self) -> None:
        for market in ("sh", "sz", "hk"):
            years = self.calendar.available_years(market)
            self.assertIn(2026, years)
            self.assertEqual(years, tuple(sorted(years)))

    def test_available_years_unsupported_market_raises(self) -> None:
        with self.assertRaisesRegex(CalendarUnavailableError, "unsupported market"):
            self.calendar.available_years("bj")


class TradingCalendarMarketContextCompatibilityTests(unittest.TestCase):
    """Section 3 of the #133 spec: feed MarketContextService from the calendar."""

    def test_build_market_context_service_from_calendar(self) -> None:
        calendar = TradingCalendar()
        trading_days = calendar.trading_days_between(
            "2026-01-01",
            "2026-12-31",
            "sh",
        )
        context = MarketContextService(
            trading_days=trading_days,
            coverage_start="2026-01-01",
            coverage_end="2026-12-31",
        )
        # A normal weekday is open.
        self.assertTrue(context.is_trading_day("2026-08-04", "sh"))
        # Spring Festival weekday is closed (not just "unknown").
        self.assertFalse(context.is_trading_day("2026-02-17", "sh"))
        # National Day closure is closed.
        self.assertFalse(context.is_trading_day("2026-10-01", "sh"))
        # National Day resumes on Oct 8 (Thu).
        self.assertTrue(context.is_trading_day("2026-10-08", "sh"))
        # Weekend is closed.
        self.assertFalse(context.is_trading_day("2026-08-08", "sh"))
        # Session boundaries match the A-share template.
        session = context.require_session("2026-08-04", "sh")
        self.assertEqual(session.start.strftime("%H:%M"), "09:30")
        self.assertEqual(session.end.strftime("%H:%M"), "15:00")


class TradingCalendarValidationTests(unittest.TestCase):
    """Yearly JSON validation: reject malformed market/year/overlap (#133)."""

    def _make_calendar_dir(
        self,
        *,
        market: str = "sh",
        year: int = 2026,
        file_market: str | None = None,
        file_year: int | None = None,
        closed_dates: list[str] | None = None,
        half_day_dates: list[str] | None = None,
        sessions: dict | None = None,
    ) -> Path:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))

        cal = tmp / "calendars"
        cal.mkdir()
        # Always write a valid market_sessions.json.
        sessions_data = sessions or {
            "sh": {
                "timezone": "Asia/Shanghai",
                "regular_sessions": [["09:30", "11:30"], ["13:00", "15:00"]],
            },
            "sz": {
                "timezone": "Asia/Shanghai",
                "regular_sessions": [["09:30", "11:30"], ["13:00", "15:00"]],
            },
            "hk": {
                "timezone": "Asia/Hong_Kong",
                "regular_sessions": [["09:30", "12:00"], ["13:00", "16:00"]],
                "half_day_sessions": [["09:30", "12:00"]],
            },
        }
        (cal / "market_sessions.json").write_text(
            json.dumps(sessions_data), encoding="utf-8"
        )

        market_dir = cal / market
        market_dir.mkdir()
        payload = {
            "market": file_market if file_market is not None else market,
            "year": file_year if file_year is not None else year,
            "closed_dates": closed_dates if closed_dates is not None else [],
        }
        if half_day_dates is not None:
            payload["half_day_dates"] = half_day_dates
        (market_dir / f"{year}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return cal

    def test_wrong_market_header_rejected(self) -> None:
        cal = self._make_calendar_dir(market="sh", file_market="sz")
        calendar = TradingCalendar(calendars_dir=cal)
        with self.assertRaisesRegex(CalendarUnavailableError, "declares market"):
            calendar.is_trading_day("2026-08-04", "sh")

    def test_wrong_year_header_rejected(self) -> None:
        cal = self._make_calendar_dir(year=2026, file_year=2027)
        calendar = TradingCalendar(calendars_dir=cal)
        with self.assertRaisesRegex(CalendarUnavailableError, "declares year"):
            calendar.is_trading_day("2026-08-04", "sh")

    def test_date_from_wrong_year_in_closed_dates_rejected(self) -> None:
        cal = self._make_calendar_dir(
            closed_dates=["2027-01-01"],  # belongs to 2027, not 2026
        )
        calendar = TradingCalendar(calendars_dir=cal)
        with self.assertRaisesRegex(CalendarUnavailableError, "does not belong to year"):
            calendar.is_trading_day("2026-08-04", "sh")

    def test_closed_and_half_day_overlap_rejected(self) -> None:
        cal = self._make_calendar_dir(
            market="hk",
            closed_dates=["2026-12-24"],
            half_day_dates=["2026-12-24"],
        )
        calendar = TradingCalendar(calendars_dir=cal)
        with self.assertRaisesRegex(CalendarUnavailableError, "both closed_dates and half_day_dates"):
            calendar.is_trading_day("2026-12-24", "hk")

    def test_half_day_dates_without_half_day_sessions_rejected(self) -> None:
        # sh has no half_day_sessions in the template, but the JSON declares one.
        cal = self._make_calendar_dir(
            market="sh",
            half_day_dates=["2026-12-31"],
            sessions={
                "sh": {
                    "timezone": "Asia/Shanghai",
                    "regular_sessions": [["09:30", "11:30"], ["13:00", "15:00"]],
                    # No half_day_sessions key.
                },
            },
        )
        calendar = TradingCalendar(calendars_dir=cal)
        with self.assertRaisesRegex(CalendarUnavailableError, "defines no half_day_sessions"):
            calendar.is_trading_day("2026-12-31", "sh")


class WheelPackagingTests(unittest.TestCase):
    """Calendar JSON must be packaged into the built wheel (#133 P1)."""

    def test_calendar_json_globs_in_package_data(self) -> None:
        """Verify pyproject.toml declares calendar JSON in package-data."""
        pyproject = ROOT / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        # The package-data section for packages.marketdata must include
        # calendar JSON globs so wheels ship the holiday files.
        self.assertIn('"packages.marketdata"', text)
        self.assertIn("calendars/market_sessions.json", text)
        self.assertIn("calendars/sh/*.json", text)
        self.assertIn("calendars/sz/*.json", text)
        self.assertIn("calendars/hk/*.json", text)

    def test_bundled_calendar_files_exist(self) -> None:
        """Verify shipped calendar JSON files are present on disk."""
        cal_dir = ROOT / "packages" / "marketdata" / "calendars"
        self.assertTrue((cal_dir / "market_sessions.json").is_file())
        for market in ("sh", "sz", "hk"):
            market_dir = cal_dir / market
            json_files = list(market_dir.glob("*.json"))
            self.assertTrue(
                json_files,
                f"no calendar JSON files found for market {market!r}",
            )


if __name__ == "__main__":
    unittest.main()
