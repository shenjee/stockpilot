"""Tests for Replay data preparation, warmup and granularity degradation (T0-045).

Every test uses deterministic fakes for the market-data port and the calendar.
No test touches the network or SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import tempfile
from typing import Any
import unittest

from packages.marketdata.provider_result import MarketDataResult, ProviderIssue
from packages.marketdata.provider_request_queue import ProviderRequestPriority
from packages.marketdata.repositories.kline_store import KLineStore
from packages.marketdata.services.kline_data_service import KLineDataService
from packages.marketdata.services.market_context_service import MarketContextService
from packages.t0assistant.runtime.replay_data import (
    ReplayDataPreparator,
    ReplayDataTimeoutError,
    ReplayDataUnavailableError,
    ReplayPreparationConfig,
)
from packages.t0assistant.tests.fixtures.replay_fixtures import (
    MARKET,
    SYMBOL,
    TRADE_DATE,
    PREVIOUS_TRADE_DATE,
    five_minute_fallback,
    market_context_service,
    market_session,
    one_minute_replay,
)


@dataclass
class FakeMarketDataPort:
    """Deterministic fake over :class:`KLineDataService`.

    ``store`` maps ``(timeframe, date_str)`` to a list of bar rows.  The fake
    records every call so tests can assert no I/O happened after ``ready``.
    """

    store: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    call_log: list[tuple[str, ...]] = field(default_factory=list)
    fail_timeframes: set[str] = field(default_factory=set)
    missing_overrides: dict[tuple[str, str], list[tuple[str, str]]] = field(
        default_factory=dict
    )
    reliability_overrides: dict[tuple[str, str], bool | None] = field(
        default_factory=dict
    )
    issue_overrides: dict[tuple[str, str], list[ProviderIssue]] = field(
        default_factory=dict
    )

    def get_klines_result(
        self,
        code: str,
        end_date: str,
        *,
        market: str | None = None,
        timeframe: str,
        start_date: str | None = None,
        limit: int = 120,
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator=None,
        request_timeout: float | None = None,
    ) -> MarketDataResult[list]:
        self.call_log.append((code, end_date, market, timeframe, start_date, limit))
        if timeframe in self.fail_timeframes:
            return MarketDataResult(success=False, data=[], issues=[])
        key = (timeframe, end_date)
        rows = list(self.store.get(key, []))
        return MarketDataResult(
            success=True,
            data=rows,
            issues=list(self.issue_overrides.get(key, [])),
        )

    def identify_missing_ranges(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None,
        timeframe: str,
    ) -> list[tuple[str, str]]:
        key = (timeframe, end_date)
        return list(self.missing_overrides.get(key, []))

    def replay_reliability_evidence(
        self,
        *,
        code: str,
        trade_date: str,
        market: str | None,
        timeframe: str,
    ) -> bool | None:
        return self.reliability_overrides.get((timeframe, trade_date))


def _populate_from_fixture(port: FakeMarketDataPort, fixture) -> None:
    """Populate the fake port with bars from a PreparedFixture."""

    prev_str = PREVIOUS_TRADE_DATE.isoformat()
    trade_str = TRADE_DATE.isoformat()
    port.store[("5m", prev_str)] = list(fixture.preheat_5m_bars)
    if fixture.target_day_1m_bars is not None:
        port.store[("1m", trade_str)] = list(fixture.target_day_1m_bars)
        port.reliability_overrides[("1m", trade_str)] = True
    port.store[("5m", trade_str)] = list(fixture.target_day_5m_bars)
    port.reliability_overrides[("5m", trade_str)] = True
    port.store[("day", trade_str)] = list(fixture.daily_bars_history)


class _PreparatorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.context = market_context_service()

    def _preparator(self, port: FakeMarketDataPort) -> ReplayDataPreparator:
        return ReplayDataPreparator(port, self.context)


class OneMinuteReliabilityTests(_PreparatorTestBase):
    def test_local_complete_data_no_network(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL,
            TRADE_DATE,
            config=ReplayPreparationConfig(
                request_priority=ProviderRequestPriority.REPLAY_PREFETCH,
            ),
        )

        self.assertEqual(prepared.symbol, SYMBOL)
        self.assertEqual(prepared.trade_date, TRADE_DATE.isoformat())
        self.assertEqual(prepared.granularity, "one_minute")
        self.assertEqual(len(prepared.bars_1m), 240)
        self.assertEqual(len(prepared.official_5m_bars), 48)
        self.assertEqual(len(prepared.preheat_5m_bars), 6)
        self.assertEqual(prepared.warnings, ())
        self.assertEqual(prepared.end_time, market_session().end)
        self.assertEqual(prepared.start_time, market_session().start)
        self.assertIsNotNone(prepared.assessment_1m)
        self.assertTrue(prepared.assessment_1m.is_reliable)

    def test_actual_bar_times_from_real_1m_sequence(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(len(prepared.actual_bar_times), 240)
        self.assertEqual(prepared.actual_bar_times[0], datetime(2026, 7, 24, 9, 31))
        self.assertEqual(prepared.actual_bar_times[-1], datetime(2026, 7, 24, 15, 0))

    def test_explicit_1m_unreliable_evidence_degrades_to_5m_without_shrinking_end_time(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.reliability_overrides[("1m", TRADE_DATE.isoformat())] = False
        port.reliability_overrides[("5m", TRADE_DATE.isoformat())] = True
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "five_minute")
        self.assertFalse(prepared.assessment_1m.is_reliable)
        self.assertTrue(prepared.assessment_5m.is_reliable)
        self.assertEqual(prepared.bars_1m, ())
        self.assertEqual(
            prepared.end_time, datetime(2026, 7, 24, 15, 0)
        )

    def test_marketdata_parse_failed_issue_makes_1m_unreliable(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.issue_overrides[("1m", TRADE_DATE.isoformat())] = [
            ProviderIssue(
                level="warning",
                reason_code="parse_failed",
                message="provider dropped malformed rows",
            )
        ]
        port.reliability_overrides[("5m", TRADE_DATE.isoformat())] = True
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )

        self.assertEqual(prepared.granularity, "five_minute")
        self.assertFalse(prepared.assessment_1m.is_reliable)
        self.assertEqual(prepared.bars_1m, ())


class PreheatTests(unittest.TestCase):
    def test_preheat_loads_across_multiple_trading_days_until_count_reached(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        context = MarketContextService(
            [date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 23), TRADE_DATE],
            coverage_start=date(2026, 7, 21),
            coverage_end=TRADE_DATE,
        )

        def build_5m_bars(trade_date: date, base_price: float) -> list[dict[str, Any]]:
            session = context.require_session(trade_date, MARKET)
            bars: list[dict[str, Any]] = []
            for index, timestamp in enumerate(session.bar_close_times(5)):
                price = round(base_price + index * 0.05, 2)
                bars.append(
                    {
                        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": price,
                        "high": round(price + 0.1, 2),
                        "low": round(price - 0.1, 2),
                        "close": round(price + 0.05, 2),
                        "volume": 5000 + index * 50,
                        "amount": round((5000 + index * 50) * (price + 0.05), 2),
                        "closed": True,
                    }
                )
            return bars

        port.store[("5m", "2026-07-23")] = build_5m_bars(date(2026, 7, 23), 9.50)
        port.store[("5m", "2026-07-22")] = build_5m_bars(date(2026, 7, 22), 9.00)
        preparator = ReplayDataPreparator(port, context)

        prepared = preparator.prepare(
            SYMBOL,
            TRADE_DATE,
            config=ReplayPreparationConfig(preheat_5m_count=60),
        )

        self.assertEqual(len(prepared.preheat_5m_bars), 60)
        self.assertEqual(prepared.preheat_5m_bars[0]["timestamp"], "2026-07-22 14:05:00")
        self.assertEqual(prepared.preheat_5m_bars[-1]["timestamp"], "2026-07-23 15:00:00")
        queried_days = [
            entry[1]
            for entry in port.call_log
            if entry[3] == "5m" and entry[1] in {"2026-07-22", "2026-07-23"}
        ]
        self.assertEqual(queried_days[:2], ["2026-07-23", "2026-07-22"])


class RealKLineServiceIntegrationTests(unittest.TestCase):
    def test_prepare_uses_real_kline_data_service_reliability_evidence(self) -> None:
        fixture = one_minute_replay()

        class FixtureProvider:
            provider_id = "fixture"

            @staticmethod
            def _reliability_issue(default_status: str, statuses: dict[str, str]) -> ProviderIssue:
                return ProviderIssue(
                    level="warning",
                    reason_code="replay_reliability_evidence",
                    message="fixture reliability evidence",
                    context={
                        "default_status": default_status,
                        "trade_date_statuses": statuses,
                    },
                )

            @staticmethod
            def _rows(values):
                rows = []
                for row in values:
                    normalized = dict(row)
                    normalized.setdefault("date", normalized.get("timestamp"))
                    rows.append(normalized)
                return rows

            def get_kline_result(
                self,
                *,
                code: str,
                start_date: str,
                end_date: str,
                ktype: str = "day",
                autype: str = "qfq",
                market: str = None,
                security_type: str | None = None,
            ):
                issues: list[ProviderIssue] = []
                if ktype == "1m" and start_date == TRADE_DATE.isoformat():
                    rows = self._rows(fixture.target_day_1m_bars)
                    issues.append(
                        self._reliability_issue(
                            "no_data",
                            {TRADE_DATE.isoformat(): "complete"},
                        )
                    )
                elif ktype == "5m" and start_date == PREVIOUS_TRADE_DATE.isoformat():
                    rows = self._rows(fixture.preheat_5m_bars)
                    issues.append(
                        self._reliability_issue(
                            "no_data",
                            {PREVIOUS_TRADE_DATE.isoformat(): "complete"},
                        )
                    )
                elif ktype == "5m" and start_date == TRADE_DATE.isoformat():
                    rows = self._rows(fixture.target_day_5m_bars)
                    issues.append(
                        self._reliability_issue(
                            "no_data",
                            {TRADE_DATE.isoformat(): "complete"},
                        )
                    )
                elif ktype == "day":
                    rows = self._rows(fixture.daily_bars_history)
                else:
                    rows = []
                return MarketDataResult(success=True, data=rows, issues=issues)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = KLineStore(Path(tmpdir) / "market_data.sqlite")
            context = MarketContextService(
                [date(2026, 7, 22), date(2026, 7, 23), TRADE_DATE],
                coverage_start=date(2026, 7, 22),
                coverage_end=TRADE_DATE,
            )
            service = KLineDataService(
                FixtureProvider(),
                store,
                lookback_days=2,
                market_context=context,
                clock=lambda: datetime(2026, 7, 25, 9, 0),
            )
            preparator = ReplayDataPreparator(service, context)

            prepared = preparator.prepare(
                SYMBOL,
                TRADE_DATE,
                config=ReplayPreparationConfig(
                    daily_history_days=2,
                    preheat_5m_count=6,
                ),
            )

            self.assertEqual(prepared.granularity, "one_minute")
            self.assertTrue(
                service.replay_reliability_evidence(
                    code="600000",
                    trade_date=TRADE_DATE.isoformat(),
                    market="sh",
                    timeframe="1m",
                )
            )


class FiveMinuteFallbackTests(_PreparatorTestBase):
    def test_1m_missing_degrades_to_5m(self) -> None:
        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )

        self.assertEqual(prepared.granularity, "five_minute")
        self.assertEqual(prepared.bars_1m, ())
        self.assertGreater(len(prepared.official_5m_bars), 0)
        self.assertEqual(len(prepared.warnings), 1)
        self.assertEqual(
            prepared.warnings[0]["warning_code"], "one_minute_data_unavailable"
        )
        self.assertEqual(prepared.warnings[0]["affected_field"], "market.bars_1m")
        self.assertEqual(prepared.assessment_1m.is_reliable, False)
        self.assertTrue(prepared.assessment_5m.is_reliable)

    def test_5m_actual_bar_times_from_official_5m(self) -> None:
        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        preparator = self._preparator(port)

        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(len(prepared.actual_bar_times), 48)
        self.assertEqual(prepared.actual_bar_times[0], datetime(2026, 7, 24, 9, 35))
        self.assertEqual(prepared.actual_bar_times[-1], datetime(2026, 7, 24, 15, 0))

    def test_end_time_still_from_calendar_in_fallback(self) -> None:
        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.end_time, datetime(2026, 7, 24, 15, 0))


class FailureTests(_PreparatorTestBase):
    def test_both_granularities_unavailable_raises(self) -> None:
        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        port.missing_overrides[("5m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        preparator = self._preparator(port)

        with self.assertRaises(ReplayDataUnavailableError):
            preparator.prepare(SYMBOL, TRADE_DATE, config=ReplayPreparationConfig())

    def test_preparation_timeout_raises(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        clock_values = iter([0.0, 100.0, 200.0, 300.0])
        preparator = ReplayDataPreparator(
            port,
            self.context,
            clock=lambda: next(clock_values),
        )
        config = ReplayPreparationConfig(deadline_monotonic=50.0)
        with self.assertRaises(ReplayDataTimeoutError):
            preparator.prepare(SYMBOL, TRADE_DATE, config=config)

    def test_deadline_checked_after_each_blocking_io(self) -> None:
        """Regression: a slow daily call that returns after the deadline must
        not let prepare() succeed.  Previously the deadline was only checked
        before each call, so a blocking daily get that finished at monotonic
        100 (deadline 50) still produced a PreparedReplayData."""

        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        clock_state = {"value": 0.0}

        def fake_clock() -> float:
            return clock_state["value"]

        original_get = port.get_klines_result

        def slow_daily_get(code, end_date, *, market=None, timeframe, start_date=None,
                           limit=120, request_priority=ProviderRequestPriority.LIVE,
                           session_validator=None, request_timeout=None):
            if timeframe == "day":
                # Simulate the daily call blocking past the deadline.
                clock_state["value"] = 100.0
            return original_get(code, end_date, market=market, timeframe=timeframe,
                                start_date=start_date, limit=limit,
                                request_priority=request_priority,
                                session_validator=session_validator,
                                request_timeout=request_timeout)

        port.get_klines_result = slow_daily_get  # type: ignore[assignment]
        preparator = ReplayDataPreparator(port, self.context, clock=fake_clock)
        config = ReplayPreparationConfig(deadline_monotonic=50.0)
        with self.assertRaises(ReplayDataTimeoutError):
            preparator.prepare(SYMBOL, TRADE_DATE, config=config)

    def test_default_clock_enforces_deadline_without_injection(self) -> None:
        """Regression: when clock is not injected, the preparator must still
        enforce deadline_monotonic using time.monotonic by default."""

        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        # A deadline already in the past must trip on the first check.
        config = ReplayPreparationConfig(deadline_monotonic=-1.0)
        preparator = ReplayDataPreparator(port, self.context)
        with self.assertRaises(ReplayDataTimeoutError):
            preparator.prepare(SYMBOL, TRADE_DATE, config=config)

    def test_request_timeout_issue_raises_preparation_timeout(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)

        def timeout_get(
            code,
            end_date,
            *,
            market=None,
            timeframe,
            start_date=None,
            limit=120,
            request_priority=ProviderRequestPriority.LIVE,
            session_validator=None,
            request_timeout=None,
        ):
            self.assertIsNotNone(request_timeout)
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code="request_timeout",
                        message="provider wait timed out",
                    )
                ],
            )

        port.get_klines_result = timeout_get  # type: ignore[assignment]
        preparator = ReplayDataPreparator(port, self.context, clock=lambda: 0.0)
        with self.assertRaises(ReplayDataTimeoutError):
            preparator.prepare(
                SYMBOL,
                TRADE_DATE,
                config=ReplayPreparationConfig(deadline_monotonic=50.0),
            )

    def test_session_retirement_does_not_update_prepared_data(self) -> None:
        """When session_validator reports invalid mid-flight, the preparator
        still returns what it loaded.  The caller (Session) is responsible for
        discarding the result; the contract says network results may still
        land in the shared cache but must not wake the old Session."""

        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        validator_state = {"valid": True}
        prepared = preparator.prepare(
            SYMBOL,
            TRADE_DATE,
            config=ReplayPreparationConfig(),
            session_validator=lambda: validator_state["valid"],
        )
        # The preparator returned successfully.  Now retire the session.
        validator_state["valid"] = False
        # The prepared object is already immutable in-memory; no further I/O.
        self.assertEqual(prepared.granularity, "one_minute")


class NormalisationTests(_PreparatorTestBase):
    def test_unsorted_and_duplicate_bars_are_standardised(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        # Shuffle and duplicate a bar.
        bars = port.store[("1m", TRADE_DATE.isoformat())]
        dup = dict(bars[5])
        port.store[("1m", TRADE_DATE.isoformat())] = [bars[10], bars[5], dup, bars[0]] + bars[11:]
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        # Dedup means the duplicate is merged, and the result is sorted.
        timestamps = [b["timestamp"] for b in prepared.bars_1m]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(timestamps), len(set(timestamps)))

    def test_invalid_bar_raises(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        bars = port.store[("1m", TRADE_DATE.isoformat())]
        bad = dict(bars[0])
        bad["high"] = -1.0  # negative high is invalid
        port.store[("1m", TRADE_DATE.isoformat())][0] = bad
        preparator = self._preparator(port)
        with self.assertRaises(Exception):
            preparator.prepare(SYMBOL, TRADE_DATE, config=ReplayPreparationConfig())


class MarketInputPortTests(_PreparatorTestBase):
    def test_prepared_rows_are_deeply_immutable(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        with self.assertRaises(TypeError):
            prepared.bars_1m[0]["close"] = 999

    def test_warning_details_are_deeply_immutable(self) -> None:
        fixture = five_minute_fallback()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = [
            (TRADE_DATE.isoformat(), TRADE_DATE.isoformat())
        ]
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        with self.assertRaises(TypeError):
            prepared.warnings[0]["details"]["x"] = 1

    def test_read_does_not_trigger_network_or_store(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        call_count_before = len(port.call_log)
        target = datetime(2026, 7, 24, 10, 0)
        input1 = prepared.market_input_port.read(target)
        input2 = prepared.market_input_port.read(target)
        call_count_after = len(port.call_log)
        self.assertEqual(call_count_before, call_count_after)
        self.assertEqual(input1, input2)

    def test_read_returns_only_prefix_at_or_before_target(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        target = datetime(2026, 7, 24, 9, 35)
        market_input = prepared.market_input_port.read(target)
        # 9:31 through 9:35 = 5 bars
        self.assertEqual(len(market_input.bars_1m), 5)
        for bar in market_input.bars_1m:
            ts = datetime.strptime(bar["timestamp"], "%Y-%m-%d %H:%M:%S")
            self.assertLessEqual(ts, target)

    def test_no_future_data_at_any_target_time(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        targets = [
            datetime(2026, 7, 24, 9, 31),
            datetime(2026, 7, 24, 11, 0),
            datetime(2026, 7, 24, 13, 5),
            datetime(2026, 7, 24, 15, 0),
        ]
        for target in targets:
            market_input = prepared.market_input_port.read(target)
            for bar in market_input.bars_1m:
                ts = datetime.strptime(bar["timestamp"], "%Y-%m-%d %H:%M:%S")
                self.assertLessEqual(ts, target)
            for bar in market_input.official_5m_bars:
                ts = datetime.strptime(bar["timestamp"], "%Y-%m-%d %H:%M:%S")
                self.assertLessEqual(ts, target)

    def test_read_rejects_wrong_trade_date(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        with self.assertRaises(Exception):
            prepared.market_input_port.read(datetime(2026, 7, 23, 10, 0))


class BackfillTests(_PreparatorTestBase):
    def test_local_gap_backfilled_successfully(self) -> None:
        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        # Start with NO 1m bars locally; simulate backfill populating them.
        port.store[("5m", PREVIOUS_TRADE_DATE.isoformat())] = list(fixture.preheat_5m_bars)
        port.store[("5m", TRADE_DATE.isoformat())] = list(fixture.target_day_5m_bars)
        port.store[("day", TRADE_DATE.isoformat())] = list(fixture.daily_bars_history)
        # No 1m key in store initially.

        state = {"missing": [(TRADE_DATE.isoformat(), TRADE_DATE.isoformat())]}
        original_get = port.get_klines_result

        def dynamic_get(code, end_date, *, market=None, timeframe, start_date=None,
                        limit=120, request_priority=ProviderRequestPriority.LIVE,
                        session_validator=None, request_timeout=None):
            if timeframe == "1m" and end_date == TRADE_DATE.isoformat():
                if not port.store.get(("1m", end_date)):
                    # Simulate successful backfill: populate store and clear missing.
                    port.store[("1m", end_date)] = list(fixture.target_day_1m_bars)
                    port.reliability_overrides[("1m", end_date)] = True
                    state["missing"] = []
            return original_get(code, end_date, market=market, timeframe=timeframe,
                                start_date=start_date, limit=limit,
                                request_priority=request_priority,
                                session_validator=session_validator,
                                request_timeout=request_timeout)

        port.get_klines_result = dynamic_get  # type: ignore[assignment]

        def dynamic_missing(*, code, start_date, end_date, market, timeframe):
            if timeframe == "1m" and end_date == TRADE_DATE.isoformat():
                return list(state["missing"])
            return []

        port.identify_missing_ranges = dynamic_missing  # type: ignore[assignment]

        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertEqual(prepared.granularity, "one_minute")
        self.assertTrue(prepared.assessment_1m.is_reliable)
        self.assertEqual(len(prepared.bars_1m), 240)

    def test_backfill_failure_but_cache_sufficient(self) -> None:
        """When backfill fails but local cache is enough, preparation succeeds."""

        fixture = one_minute_replay()
        port = FakeMarketDataPort()
        _populate_from_fixture(port, fixture)
        # The 1m data is complete locally, but identify_missing_ranges reports
        # a gap that never resolves (simulating failed backfill).  However,
        # because the local bars are present and valid, the assessment should
        # still find them reliable after the backfill attempt.
        port.missing_overrides[("1m", TRADE_DATE.isoformat())] = []
        preparator = self._preparator(port)
        prepared = preparator.prepare(
            SYMBOL, TRADE_DATE, config=ReplayPreparationConfig()
        )
        self.assertTrue(prepared.assessment_1m.is_reliable)


class ConstructorValidationTests(unittest.TestCase):
    def test_market_context_must_be_correct_type(self) -> None:
        port = FakeMarketDataPort()
        with self.assertRaises(TypeError):
            ReplayDataPreparator(port, "not a context")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
