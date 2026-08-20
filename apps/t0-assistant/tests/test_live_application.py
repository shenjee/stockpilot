from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend.event_publisher import EventPublisher  # noqa: E402
from backend.live_application import (  # noqa: E402
    LiveApplicationApi,
    LiveSessionFactory,
)
from packages.marketdata.services.market_context_service import (  # noqa: E402
    MarketContextService,
)
from packages.t0assistant.preferences import PreferenceService, PreferenceValues  # noqa: E402
from packages.t0assistant.repositories import (  # noqa: E402
    SqlitePreferenceRepository,
    open_app_database,
)
from packages.t0assistant.runtime import (  # noqa: E402
    AppMode,
    LiveRefreshKind,
    PipelineMarketInput,
)
from packages.marketdata.t0_schema import InstrumentIdentity, InstrumentType  # noqa: E402
from packages.t0assistant.runtime.live_session import PreparedLiveWarmup  # noqa: E402


def _bar(timestamp: str, price: float) -> dict:
    return {
        "timestamp": timestamp,
        "open": price,
        "high": price + 0.1,
        "low": price - 0.1,
        "close": price,
        "volume": 1000.0,
        "amount": price * 1000.0,
        "closed": True,
    }


def _chan(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "timeframe": "5m",
        "source": "fixture",
        "engine": "czsc",
        "engine_version": "0.10.12",
        "parameters": {},
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
        "meta": {},
    }


class _MarketInput:
    def __init__(self, target_time: datetime, value: PipelineMarketInput) -> None:
        self._target_time = target_time
        self._value = value

    def read(self, target_time: datetime) -> PipelineMarketInput:
        if target_time != self._target_time:
            raise AssertionError(target_time)
        return self._value


class _DeterministicLiveInput:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.requests = []
        self.refresh_requests = []
        self.refresh_outcomes = {"quote": [], "1m": [], "5m": []}
        self.context = MarketContextService(["2026-07-23", "2026-07-24"])

    def prepare(self, spec, *, minimum_preheat_5m, target_trade_date=None):
        self.requests.append((spec, minimum_preheat_5m))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        target = datetime(2026, 7, 24, 9, 31)
        market_input = PipelineMarketInput(
            symbol=spec.symbol,
            trade_date=date(2026, 7, 24),
            previous_close=10.0,
            preheat_5m_bars=[
                _bar("2026-07-23 14:55:00", 10.0),
                _bar("2026-07-23 15:00:00", 10.1),
            ],
            bars_1m=[_bar("2026-07-24 09:31:00", 10.2)],
            official_5m_bars=[],
            daily_bars_history=[],
            quote_snapshots=[],
        )
        return PreparedLiveWarmup(
            market_session=self.context.require_session("2026-07-24", "sh"),
            target_time=target,
            observed_now=target,
            market_candidate_trade_date=date(2026, 7, 24),
            market_input_port=_MarketInput(target, market_input),
            calendar_status="available",
            market_phase="morning",
        )

    def queue_refresh(self, branch: str, *outcomes: object) -> None:
        self.refresh_outcomes[branch].extend(outcomes)

    def load_refresh_bars(self, spec, *, timeframe, trade_date):
        self.refresh_requests.append((timeframe, str(trade_date)))
        return self._refresh_value(
            timeframe,
            (
                [_bar("2026-07-24 09:31:00", 10.2)]
                if timeframe == "1m"
                else []
            ),
        )

    def load_refresh_quotes(self, spec, *, trade_date):
        self.refresh_requests.append(("quote", str(trade_date)))
        return self._refresh_value("quote", [])

    def _refresh_value(self, branch, default):
        queued = self.refresh_outcomes[branch]
        value = queued.pop(0) if queued else default
        if isinstance(value, BaseException):
            raise value
        return value


class LiveApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = open_app_database(Path(self.tempdir.name) / "app.sqlite")
        self.preferences = PreferenceService(
            SqlitePreferenceRepository(self.database)
        )
        self.publisher = EventPublisher(service_generation=7)
        self.events = self.publisher.subscribe()

    def tearDown(self) -> None:
        self.publisher.unsubscribe(self.events)
        self.database.close()
        self.tempdir.cleanup()

    def _app(
        self,
        input_port: _DeterministicLiveInput,
        *,
        restore_on_startup: bool = False,
        resolve_security=None,
    ) -> LiveApplicationApi:
        factory = LiveSessionFactory(
            input_port,
            analyzer=lambda bars, symbol: _chan(symbol),
            auto_poll=False,
        )
        self.factory = factory
        if resolve_security is None:
            resolve_security = lambda symbol: InstrumentIdentity(
                symbol=symbol,
                code=symbol[3:],
                market=symbol[:2],
                name="测试证券",
                instrument_type=InstrumentType.STOCK,
            )
        return LiveApplicationApi(
            service_generation=7,
            session_factory=factory,
            preference_service=self.preferences,
            event_publisher=self.publisher,
            resolve_security=resolve_security,
            restore_on_startup=restore_on_startup,
        )

    def test_first_run_is_empty_then_selection_publishes_complete_snapshot(self) -> None:
        input_port = _DeterministicLiveInput()
        app = self._app(input_port)
        self.assertIsNone(app.coordinator.snapshot.current_symbol)

        response = app.select_security(
            request_id="select-1",
            symbol="sh.600000",
        )
        event = self.events.get(timeout=1)

        self.assertTrue(response["accepted"])
        self.assertEqual(response["data"]["session_id"], event["session_id"])
        self.assertEqual(event["event_type"], "workbench_snapshot")
        self.assertEqual(event["payload"]["session"]["symbol"], "sh.600000")
        self.assertEqual(input_port.requests[0][1], 500)
        self.assertEqual(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol,
            "sh.600000",
        )

    def test_identity_lookup_does_not_select_or_persist(self) -> None:
        input_port = _DeterministicLiveInput()
        app = self._app(input_port)

        response = app.resolve_security_identity(
            request_id="resolve-1",
            symbol="sh.600000",
        )

        self.assertTrue(response["accepted"])
        self.assertEqual(response["data"]["security"]["symbol"], "sh.600000")
        self.assertIsNone(app.coordinator.snapshot.current_symbol)
        self.assertEqual(input_port.requests, [])
        self.assertIsNone(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol
        )

    def test_missing_coordinator_session_returns_structured_service_errors(self) -> None:
        app = self._app(_DeterministicLiveInput())
        app._coordinator = SimpleNamespace(
            snapshot=SimpleNamespace(live_session=None),
            select_symbol=lambda symbol, instrument=None: SimpleNamespace(live_session=None),
        )

        selected = app.select_security(
            request_id="select-missing",
            symbol="sh.600000",
        )

        self.assertFalse(selected["accepted"])
        self.assertEqual(selected["error"]["error_code"], "service_unavailable")

        app._coordinator = SimpleNamespace(
            snapshot=SimpleNamespace(
                live_session=SimpleNamespace(session_id="live-missing")
            ),
            retry_live=lambda: SimpleNamespace(live_session=None),
        )
        retried = app.retry_live(
            request_id="retry-missing",
            session_id="live-missing",
        )
        self.assertFalse(retried["accepted"])
        self.assertEqual(retried["error"]["error_code"], "service_unavailable")

    def test_startup_restores_last_symbol_and_repeated_selection_republishes(self) -> None:
        self.preferences.save(
            PreferenceValues(last_symbol="sh.600000")
        )
        app = self._app(_DeterministicLiveInput(), restore_on_startup=True)
        first = self.events.get(timeout=1)
        self.assertEqual(app.coordinator.snapshot.current_symbol, "sh.600000")
        self.assertEqual(first["event_type"], "workbench_snapshot")

        response = app.select_security(
            request_id="select-restored",
            symbol="sh.600000",
        )
        republished = self.events.get(timeout=1)
        self.assertEqual(
            republished["session_id"],
            response["data"]["session_id"],
        )
        self.assertEqual(republished["payload"], first["payload"])

    def test_get_preferences_returns_restored_security_via_exact_lookup(self) -> None:
        self.preferences.save(
            PreferenceValues(last_symbol="sz.300113")
        )
        lookups: list[str] = []

        def resolve(symbol: str):
            lookups.append(symbol)
            return InstrumentIdentity(
                symbol=symbol,
                code="300113",
                market="sz",
                name="顺网科技",
                instrument_type=InstrumentType.STOCK,
            )

        app = self._app(
            _DeterministicLiveInput(),
            restore_on_startup=False,
            resolve_security=resolve,
        )
        response = app.get_preferences(request_id="prefs-1")
        data = response["data"]

        self.assertTrue(response["accepted"])
        self.assertEqual(lookups, ["sz.300113"])
        self.assertEqual(data["restored_security"]["name"], "顺网科技")
        self.assertEqual(data["startup_restore"]["status"], "restored")
        self.assertIsNotNone(data["startup_restore"]["session_id"])

    def test_save_preferences_preserves_last_symbol_when_layout_patch_is_null(
        self,
    ) -> None:
        app = self._app(_DeterministicLiveInput())
        app.select_security(request_id="select-1", symbol="sh.600519")
        self.events.get(timeout=1)

        saved = app.save_preferences(
            request_id="save-layout",
            preferences={
                "last_symbol": None,
                "layout": {"chart_split": "50_50", "show_intraday": False},
                "layers": PreferenceValues().layers.to_dict(),
            },
        )

        self.assertTrue(saved["accepted"])
        self.assertEqual(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol,
            "sh.600519",
        )
        self.assertEqual(
            saved["data"]["preferences"]["layout"]["chart_split"],
            "50_50",
        )

    def test_save_preferences_ignores_stale_non_null_last_symbol(
        self,
    ) -> None:
        app = self._app(_DeterministicLiveInput())
        app.select_security(request_id="select-1", symbol="sh.600519")
        self.events.get(timeout=1)

        saved = app.save_preferences(
            request_id="save-layout",
            preferences={
                "last_symbol": "sh.600000",
                "layout": {"chart_split": "50_50", "show_intraday": False},
                "layers": PreferenceValues().layers.to_dict(),
            },
        )

        self.assertTrue(saved["accepted"])
        self.assertEqual(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol,
            "sh.600519",
        )

    def test_concurrent_layout_save_does_not_revert_newly_selected_symbol(
        self,
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        app = self._app(_DeterministicLiveInput())
        app.select_security(request_id="initial", symbol="sh.600000")
        self.events.get(timeout=1)
        layout_prefs = {
            "last_symbol": "sh.600000",
            "layout": {"chart_split": "50_50", "show_intraday": False},
            "layers": PreferenceValues().layers.to_dict(),
        }
        barrier = Barrier(2)

        def save_layout() -> None:
            barrier.wait()
            for index in range(20):
                app.save_preferences(
                    request_id=f"layout-{index}",
                    preferences=layout_prefs,
                )

        def select_new() -> None:
            barrier.wait()
            app.select_security(request_id="select-new", symbol="sz.300113")

        with ThreadPoolExecutor(max_workers=2) as executor:
            layout_future = executor.submit(save_layout)
            select_future = executor.submit(select_new)
            layout_future.result()
            select_future.result()

        self.assertEqual(
            self.preferences.restore_for_startup().snapshot.preferences.last_symbol,
            "sz.300113",
        )

    def test_select_security_reports_preference_warning_when_storage_is_read_only(
        self,
    ) -> None:
        db_path = Path(self.tempdir.name) / "readonly-preferences.sqlite"
        with open_app_database(db_path) as writable:
            PreferenceService(SqlitePreferenceRepository(writable)).save(
                PreferenceValues(last_symbol="sh.600000")
            )
        with open_app_database(db_path, force_read_only=True) as read_only:
            read_only_prefs = PreferenceService(
                SqlitePreferenceRepository(read_only)
            )
            app = LiveApplicationApi(
                service_generation=7,
                session_factory=LiveSessionFactory(
                    _DeterministicLiveInput(),
                    analyzer=lambda bars, symbol: _chan(symbol),
                    auto_poll=False,
                ),
                preference_service=read_only_prefs,
                event_publisher=self.publisher,
                resolve_security=lambda symbol: InstrumentIdentity(
                    symbol=symbol,
                    code=symbol[3:],
                    market=symbol[:2],
                    name="测试证券",
                    instrument_type=InstrumentType.STOCK,
                ),
                restore_on_startup=False,
            )
            response = app.select_security(
                request_id="select-readonly",
                symbol="sh.600519",
            )

        self.assertTrue(response["accepted"])
        warning = response["data"]["preference_warning"]
        self.assertEqual(warning["affected_capability"], "preferences")
        self.assertFalse(warning["retryable"])

    def test_save_last_symbol_retries_failed_selection_persistence(self) -> None:
        db_path = Path(self.tempdir.name) / "save-last-symbol-retry.sqlite"
        with open_app_database(db_path) as writable:
            initial_prefs = PreferenceService(SqlitePreferenceRepository(writable))
            initial_prefs.save(PreferenceValues(last_symbol="sh.600000"))
        with open_app_database(db_path, force_read_only=True) as read_only:
            read_only_prefs = PreferenceService(SqlitePreferenceRepository(read_only))
            app = LiveApplicationApi(
                service_generation=7,
                session_factory=LiveSessionFactory(
                    _DeterministicLiveInput(),
                    analyzer=lambda bars, symbol: _chan(symbol),
                    auto_poll=False,
                ),
                preference_service=read_only_prefs,
                event_publisher=self.publisher,
                restore_on_startup=False,
            )
            response = app.save_last_symbol(
                request_id="retry-save",
                symbol="sh.600519",
            )

        self.assertFalse(response["accepted"])
        self.assertEqual(response["error"]["affected_capability"], "preferences")

        with open_app_database(db_path) as writable:
            writable_prefs = PreferenceService(SqlitePreferenceRepository(writable))
            app = LiveApplicationApi(
                service_generation=7,
                session_factory=LiveSessionFactory(
                    _DeterministicLiveInput(),
                    analyzer=lambda bars, symbol: _chan(symbol),
                    auto_poll=False,
                ),
                preference_service=writable_prefs,
                event_publisher=self.publisher,
                restore_on_startup=False,
            )
            response = app.save_last_symbol(
                request_id="retry-success",
                symbol="sh.600519",
            )
            self.assertTrue(response["accepted"])
            self.assertEqual(
                writable_prefs.restore_for_startup().snapshot.preferences.last_symbol,
                "sh.600519",
            )

    def test_startup_restore_round_trip_survives_service_restart(self) -> None:
        self.preferences.save(
            PreferenceValues(last_symbol="sh.510300")
        )
        first = self._app(
            _DeterministicLiveInput(),
            restore_on_startup=True,
            resolve_security=lambda symbol: InstrumentIdentity(
                symbol=symbol,
                code="510300",
                market="sh",
                name="沪深300ETF",
                instrument_type=InstrumentType.ETF,
            ),
        )
        first_event = self.events.get(timeout=1)
        first_session = first.coordinator.snapshot.live_session
        self.assertEqual(first_event["event_type"], "workbench_snapshot")
        self.assertIsNotNone(first_session)

        prefs = first.get_preferences(request_id="prefs-restart")
        self.assertEqual(prefs["data"]["startup_restore"]["status"], "already_active")
        self.assertEqual(
            prefs["data"]["restored_security"]["name"],
            "沪深300ETF",
        )

        second = self._app(
            _DeterministicLiveInput(),
            restore_on_startup=True,
            resolve_security=lambda symbol: InstrumentIdentity(
                symbol=symbol,
                code="510300",
                market="sh",
                name="沪深300ETF",
                instrument_type=InstrumentType.ETF,
            ),
        )
        second_event = self.events.get(timeout=1)
        self.assertEqual(second_event["event_type"], "workbench_snapshot")
        self.assertEqual(
            second.coordinator.snapshot.current_symbol,
            "sh.510300",
        )

    def test_initial_failure_publishes_revision_zero_without_a_baseline(self) -> None:
        app = self._app(
            _DeterministicLiveInput([RuntimeError("initial provider failure")])
        )

        selected = app.select_security(
            request_id="select-failing",
            symbol="sh.600000",
        )
        failure = self.events.get(timeout=1)

        self.assertTrue(selected["accepted"])
        self.assertEqual(failure["event_type"], "operation_failed")
        self.assertEqual(failure["revision"], 0)
        self.assertEqual(failure["session_id"], selected["data"]["session_id"])
        self.assertFalse(app.store.has_snapshot)

    def test_switch_retires_old_session_and_live_remains_active_in_replay_mode(self) -> None:
        input_port = _DeterministicLiveInput()
        app = self._app(input_port)
        first = app.select_security(request_id="first", symbol="sh.600000")
        self.events.get(timeout=1)
        old_runtime = self.factory.latest_session
        assert old_runtime is not None
        old_runtime.wait_for_completion(1)
        old_id = first["data"]["session_id"]

        second = app.select_security(request_id="second", symbol="sz.000001")
        second_event = self.events.get(timeout=1)
        current = app.coordinator.snapshot.live_session

        self.assertNotEqual(second["data"]["session_id"], old_id)
        self.assertEqual(second_event["payload"]["session"]["symbol"], "sz.000001")
        self.assertFalse(
            app.coordinator.accepts_result(
                session_type="live",
                session_id=old_id,
                generation=1,
            )
        )
        self.assertTrue(old_runtime.retired)
        self.assertTrue(old_runtime.refresh_scheduler.retired)
        app.coordinator.set_mode(AppMode.REPLAY)
        self.assertEqual(app.coordinator.snapshot.live_session, current)

        input_port.queue_refresh(
            "1m",
            [
                _bar("2026-07-24 09:31:00", 10.2),
                _bar("2026-07-24 09:32:00", 10.3),
            ],
        )
        current_runtime = self.factory.latest_session
        assert current_runtime is not None
        current_runtime.wait_for_completion(1)
        current_runtime.refresh_scheduler.retry(
            LiveRefreshKind.ONE_MINUTE,
            datetime(2026, 7, 24, 9, 32),
        )
        refresh_event = self.events.get(timeout=1)
        self.assertEqual(refresh_event["event_type"], "market_update")
        self.assertEqual(refresh_event["payload"]["target"], "bars_1m")

    def test_failed_rebuild_keeps_last_snapshot_and_manual_retry_recovers_cleanly(self) -> None:
        input_port = _DeterministicLiveInput(
            [object(), RuntimeError("injected provider failure"), object()]
        )
        app = self._app(input_port)
        selected = app.select_security(request_id="select", symbol="sh.600000")
        baseline = self.events.get(timeout=1)
        old_revision = baseline["revision"]

        failed_retry = app.retry_live(
            request_id="retry-1",
            session_id=selected["data"]["session_id"],
        )
        failure = self.events.get(timeout=1)

        self.assertTrue(failed_retry["accepted"])
        self.assertEqual(failure["event_type"], "operation_failed")
        self.assertEqual(failure["revision"], 0)
        self.assertEqual(
            failure["session_id"],
            failed_retry["data"]["session_id"],
        )
        self.assertTrue(app.store.has_snapshot)
        self.assertEqual(
            app.store.current_session,
            (baseline["session_id"], 1),
        )
        self.assertEqual(app.store.current_revision, old_revision)

        recovered = app.retry_live(
            request_id="retry-2",
            session_id=failed_retry["data"]["session_id"],
        )
        recovered_event = self.events.get(timeout=1)
        self.assertTrue(recovered["accepted"])
        self.assertEqual(recovered_event["event_type"], "workbench_snapshot")
        self.assertNotEqual(recovered_event["session_id"], baseline["session_id"])

    def test_refresh_failure_preserves_projection_and_other_branches_advance(self) -> None:
        input_port = _DeterministicLiveInput()
        input_port.queue_refresh(
            "quote",
            RuntimeError("quote unavailable"),
        )
        input_port.queue_refresh(
            "1m",
            [
                _bar("2026-07-24 09:31:00", 10.2),
                _bar("2026-07-24 09:32:00", 10.3),
            ],
        )
        input_port.queue_refresh(
            "5m",
            [_bar("2026-07-24 09:35:00", 10.4)],
        )
        app = self._app(input_port)
        selected = app.select_security(request_id="select", symbol="sh.600000")
        baseline = self.events.get(timeout=1)
        runtime = self.factory.latest_session
        assert runtime is not None
        runtime.wait_for_completion(1)

        states = runtime.run_refresh_due(datetime(2026, 7, 24, 9, 35))
        emitted = []
        deadline = datetime.now().timestamp() + 1.0
        while datetime.now().timestamp() < deadline:
            try:
                emitted.append(self.events.get(timeout=0.05))
            except Exception:
                if emitted:
                    break

        self.assertIsNotNone(states[LiveRefreshKind.QUOTE].last_failure)
        self.assertEqual(
            states[LiveRefreshKind.ONE_MINUTE].latest_data_time,
            datetime(2026, 7, 24, 9, 32),
        )
        self.assertEqual(
            states[LiveRefreshKind.OFFICIAL_FIVE_MINUTE].latest_data_time,
            datetime(2026, 7, 24, 9, 35),
        )
        failure = next(e for e in emitted if e["event_type"] == "operation_failed")
        self.assertEqual(failure["revision"], baseline["revision"] + 1)
        snapshot = app.get_live_snapshot(
            request_id="snapshot",
            session_id=selected["data"]["session_id"],
        )["data"]
        self.assertEqual(snapshot["session"]["revision"], max(e["revision"] for e in emitted))
        self.assertEqual(snapshot["market"]["bars_1m"][-1]["timestamp"], "2026-07-24 09:32:00")

        recovered = app.retry_live(
            request_id="retry-refresh",
            session_id=selected["data"]["session_id"],
        )
        replacement = self.events.get(timeout=1)
        self.assertTrue(recovered["accepted"])
        self.assertEqual(replacement["event_type"], "workbench_snapshot")
        self.assertTrue(runtime.retired)
        self.assertTrue(runtime.refresh_scheduler.retired)

    def test_no_new_official_five_minute_is_successful_noop(self) -> None:
        input_port = _DeterministicLiveInput()
        app = self._app(input_port)
        app.select_security(request_id="select", symbol="sh.600000")
        self.events.get(timeout=1)
        runtime = self.factory.latest_session
        assert runtime is not None
        runtime.wait_for_completion(1)
        before = app.store.current_revision

        state = runtime.refresh_scheduler.retry(
            LiveRefreshKind.OFFICIAL_FIVE_MINUTE,
            datetime(2026, 7, 24, 9, 32),
        )

        self.assertIsNone(state.last_failure)
        self.assertEqual(app.store.current_revision, before)

if __name__ == "__main__":
    unittest.main()
