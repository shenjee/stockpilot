from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import Event, Lock, Thread
import time
import unittest

from packages.t0assistant.preferences import PreferenceCapability
from packages.t0assistant.repositories import (
    RepositoryNotFoundError,
    RepositoryPersistenceError,
    RepositoryReadOnlyError,
    SqliteTradeRepository,
    open_app_database,
)
from packages.t0assistant.trading import (
    AllowAllEligibility,
    TradeCommandApi,
    TradeRecord,
    TradeService,
)
from packages.t0assistant.trading.service import InstrumentEligibilityPort


def _draft(**overrides) -> dict:
    base = {
        "trade_scope": "real",
        "symbol": "sh.600584",
        "side": "buy",
        "executed_at": "2026-07-24 10:03:00",
        "price": 38.25,
        "quantity": 200,
        "fee": 5.01,
        "note": "manual fill",
        "fee_plan_id": "shenwan-hongyuan",
    }
    base.update(overrides)
    return base


class _CapturingPublisher:
    """Records every publish_trades_changed call; a fake TradeEventPublisher."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = Lock()

    def publish_trades_changed(self, **payload) -> None:
        with self._lock:
            self.events.append(payload)


class _GatedPublisher(_CapturingPublisher):
    """Publisher that can block inside publish to widen concurrency windows.

    Used to prove revision allocation + snapshot + publish stay ordered even
    when another ThreadingHTTPServer-style handler races the API lock.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.release.set()  # default: do not block

    def publish_trades_changed(self, **payload) -> None:
        self.entered.set()
        self.release.wait(timeout=5)
        super().publish_trades_changed(**payload)


class _ScriptedRepository:
    """In-memory repository whose write methods can be forced to raise.

    Implements the narrow ``_TradeRepository`` Protocol used by ``TradeService``
    so the command API can be exercised without SQLite. Listing reflects the
    in-memory map so published snapshots are observable.
    """

    def __init__(self) -> None:
        self._records: dict[str, TradeRecord] = {}
        self.create_failure: Exception | None = None
        self.update_failure: Exception | None = None
        self.delete_failure: Exception | None = None
        self.list_failure: Exception | None = None

    @property
    def capability(self) -> PreferenceCapability:
        return PreferenceCapability(readable=True, writable=True)

    def create(self, record: TradeRecord) -> TradeRecord:
        if self.create_failure is not None:
            raise self.create_failure
        self._records[record.trade_id] = record
        return record

    def get(self, trade_id: str) -> TradeRecord | None:
        return self._records.get(trade_id)

    def list_all(self) -> tuple[TradeRecord, ...]:
        if self.list_failure is not None:
            raise self.list_failure
        return tuple(self._records.values())

    def list_for_symbol_and_date(self, symbol, trade_date):
        if self.list_failure is not None:
            raise self.list_failure
        trade_date_str = (
            trade_date if isinstance(trade_date, str) else trade_date.isoformat()
        )
        return tuple(
            r
            for r in self._records.values()
            if r.trade.symbol == symbol
            and r.trade.executed_at.date().isoformat() == trade_date_str
        )

    def update(self, record: TradeRecord) -> TradeRecord:
        if self.update_failure is not None:
            raise self.update_failure
        if record.trade_id not in self._records:
            raise RepositoryNotFoundError(f"成交记录不存在：{record.trade_id}")
        self._records[record.trade_id] = record
        return record

    def delete(self, trade_id: str) -> bool:
        if self.delete_failure is not None:
            raise self.delete_failure
        return self._records.pop(trade_id, None) is not None


def _request(command: str, payload: dict, rid: str = "req"):
    return {
        "schema_version": "t0_app_v2",
        "request_id": rid,
        "command": command,
        "session_id": None,
        "payload": payload,
    }


class TradeCommandApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "trades.sqlite3"
        self.publisher = _CapturingPublisher()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    @contextmanager
    def _api(self, *, service_generation: int = 5, repository=None, eligibility=None):
        database = None
        if repository is None:
            database = open_app_database(self.db_path)
            repository = SqliteTradeRepository(database)
        service = TradeService(
            repository, eligibility=eligibility or AllowAllEligibility()
        )
        api = TradeCommandApi(
            service,
            service_generation=service_generation,
            publisher=self.publisher,
        )
        try:
            yield api
        finally:
            if database is not None:
                database.close()

    # -- list_trades ----------------------------------------------------

    def test_list_trades_empty_repository_publishes_empty_scoped_snapshot(self) -> None:
        with self._api() as api:
            result = api.dispatch(
                "list_trades",
                _request(
                    "list_trades",
                    {"trade_scope": "real", "symbol": "sh.600584",
                     "trade_date": "2026-07-24"},
                ),
            )
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["operation_id"])
        self.assertIsNone(result["data"])  # renderer must not read sync data
        self.assertEqual(len(self.publisher.events), 1)
        event = self.publisher.events[0]
        self.assertEqual(event["trade_revision"], 0)
        self.assertEqual(event["trades"], [])
        self.assertEqual(event["symbol"], "sh.600584")
        self.assertEqual(event["trade_date"], "2026-07-24")
        self.assertEqual(event["service_generation"], 5)

    def test_list_trades_publishes_only_request_scope(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.600584", executed_at="2026-07-24 10:03:00")}))
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sz.000001", executed_at="2026-07-25 14:10:00",
                                 side="sell")}))
            self.publisher.events.clear()
            api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertEqual(len(self.publisher.events), 1)
        event = self.publisher.events[0]
        self.assertEqual(event["symbol"], "sh.600584")
        self.assertEqual(event["trade_date"], "2026-07-24")
        trades = event["trades"]
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "sh.600584")

    def test_list_trades_index_publishes_empty_scoped_fact(self) -> None:
        with self._api(eligibility=_ScriptedEligibility("index")) as api:
            result = api.dispatch(
                "list_trades",
                _request(
                    "list_trades",
                    {"trade_scope": "real", "symbol": "sh.000001",
                     "trade_date": "2026-07-24"},
                ),
            )
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.publisher.events), 1)
        event = self.publisher.events[0]
        self.assertEqual(event["symbol"], "sh.000001")
        self.assertEqual(event["trade_date"], "2026-07-24")
        self.assertEqual(event["trades"], [])

    def test_list_trades_rejects_simulated_scope(self) -> None:
        with self._api() as api:
            result = api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "simulated", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "unsupported_trade_scope")
        self.assertEqual(result["error"]["category"], "invalid_request")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(self.publisher.events, [])

    # -- list_trade_history ---------------------------------------------

    def test_list_trade_history_returns_full_snapshot_without_publish(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.600584", executed_at="2026-07-24 10:03:00")}))
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sz.000001", executed_at="2026-07-25 14:10:00",
                                 side="sell")}))
            self.publisher.events.clear()
            result = api.dispatch(
                "list_trade_history",
                _request("list_trade_history", {"trade_scope": "real"}),
            )
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["operation_id"])
        self.assertEqual(result["data"]["trade_revision"], 2)
        symbols = {t["symbol"] for t in result["data"]["trades"]}
        self.assertEqual(symbols, {"sh.600584", "sz.000001"})
        self.assertEqual(self.publisher.events, [])

    def test_list_trade_history_rejects_simulated_scope(self) -> None:
        with self._api() as api:
            result = api.dispatch(
                "list_trade_history",
                _request("list_trade_history", {"trade_scope": "simulated"}),
            )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "unsupported_trade_scope")
        self.assertEqual(self.publisher.events, [])

    # -- create / update / delete --------------------------------------

    def test_create_bumps_revision_and_publishes_scoped_snapshot(self) -> None:
        with self._api() as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertTrue(result["accepted"])
        event = self.publisher.events[-1]
        self.assertEqual(event["trade_revision"], 1)
        self.assertEqual(event["symbol"], "sh.600584")
        self.assertEqual(event["trade_date"], "2026-07-24")
        self.assertEqual(len(event["trades"]), 1)
        trade = event["trades"][0]
        self.assertEqual(trade["trade_scope"], "real")
        self.assertEqual(trade["bucket_start"], "2026-07-24 10:00:00")
        self.assertEqual(trade["fee"], 5.01)

    def test_update_same_scope_publishes_once(self) -> None:
        with self._api() as api:
            create = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
            self.assertTrue(create["accepted"])
            trade_id = self.publisher.events[-1]["trades"][0]["trade_id"]
            self.publisher.events.clear()
            result = api.dispatch("update_trade", _request("update_trade",
                {"trade_id": trade_id,
                 "trade": _draft(price=40.00, fee=9.99, note="edited")}))
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.publisher.events), 1)
        event = self.publisher.events[0]
        trade = event["trades"][0]
        self.assertEqual(trade["trade_id"], trade_id)
        self.assertEqual(trade["price"], 40.0)
        self.assertEqual(trade["fee"], 9.99)
        self.assertEqual(trade["note"], "edited")
        self.assertEqual(event["trade_revision"], 2)
        self.assertEqual(event["symbol"], "sh.600584")
        self.assertEqual(event["trade_date"], "2026-07-24")

    def test_update_cross_scope_publishes_old_then_new(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
            trade_id = self.publisher.events[-1]["trades"][0]["trade_id"]
            self.publisher.events.clear()
            result = api.dispatch("update_trade", _request("update_trade",
                {"trade_id": trade_id,
                 "trade": _draft(
                     symbol="sz.000001",
                     executed_at="2026-07-25 14:10:00",
                     side="sell",
                     price=12.4,
                     quantity=500,
                 )}))
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.publisher.events), 2)
        old_event, new_event = self.publisher.events
        self.assertEqual(old_event["symbol"], "sh.600584")
        self.assertEqual(old_event["trade_date"], "2026-07-24")
        self.assertEqual(old_event["trades"], [])
        self.assertEqual(old_event["trade_revision"], 2)
        self.assertEqual(new_event["symbol"], "sz.000001")
        self.assertEqual(new_event["trade_date"], "2026-07-25")
        self.assertEqual(len(new_event["trades"]), 1)
        self.assertEqual(new_event["trades"][0]["trade_id"], trade_id)
        self.assertEqual(new_event["trade_revision"], 3)

    def test_concurrent_creates_publish_monotonic_revisions(self) -> None:
        """ThreadingHTTPServer-style concurrency must not reorder trade_revision."""

        with self._api() as api:
            def create_one(index: int) -> None:
                result = api.dispatch(
                    "create_trade",
                    _request(
                        "create_trade",
                        {
                            "trade": _draft(
                                executed_at=f"2026-07-24 10:{index:02d}:00",
                                note=f"n{index}",
                            )
                        },
                        rid=f"c-{index}",
                    ),
                )
                self.assertTrue(result["accepted"], result)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(create_one, i) for i in range(12)]
                for future in as_completed(futures):
                    future.result()

        revisions = [event["trade_revision"] for event in self.publisher.events]
        self.assertEqual(revisions, sorted(revisions))
        self.assertEqual(len(revisions), len(set(revisions)))
        self.assertEqual(revisions, list(range(1, 13)))

    def test_cross_scope_update_not_overtaken_by_concurrent_create(self) -> None:
        """A gated publish proves revision/snapshot/publish share one serial order.

        Without holding the API lock through publish, a concurrent create could
        publish revision N+2 before the cross-scope pair finishes N/N+1.
        """

        gated = _GatedPublisher()
        self.publisher = gated
        with self._api() as api:
            api.dispatch(
                "create_trade",
                _request("create_trade", {"trade": _draft()}),
            )
            trade_id = gated.events[-1]["trades"][0]["trade_id"]
            gated.events.clear()
            gated.entered.clear()
            gated.release.clear()

            def cross_scope_update() -> None:
                result = api.dispatch(
                    "update_trade",
                    _request(
                        "update_trade",
                        {
                            "trade_id": trade_id,
                            "trade": _draft(
                                symbol="sz.000001",
                                executed_at="2026-07-25 14:10:00",
                                side="sell",
                            ),
                        },
                        rid="cross",
                    ),
                )
                self.assertTrue(result["accepted"], result)

            updater = Thread(target=cross_scope_update)
            updater.start()
            self.assertTrue(gated.entered.wait(timeout=2))

            # While the first publish of the cross-scope pair is gated, another
            # mutation must block on the API lock rather than publish ahead.
            create_started = Event()
            create_finished = Event()

            def concurrent_create() -> None:
                create_started.set()
                result = api.dispatch(
                    "create_trade",
                    _request(
                        "create_trade",
                        {
                            "trade": _draft(
                                symbol="sh.600000",
                                executed_at="2026-07-24 11:00:00",
                            )
                        },
                        rid="race",
                    ),
                )
                self.assertTrue(result["accepted"], result)
                create_finished.set()

            racer = Thread(target=concurrent_create)
            racer.start()
            self.assertTrue(create_started.wait(timeout=2))
            # Give the racer a chance to contend; it must still be blocked.
            time.sleep(0.05)
            self.assertFalse(create_finished.is_set())
            self.assertEqual(len(gated.events), 0)

            gated.release.set()
            updater.join(timeout=5)
            racer.join(timeout=5)
            self.assertFalse(updater.is_alive())
            self.assertFalse(racer.is_alive())

        revisions = [event["trade_revision"] for event in gated.events]
        self.assertEqual(revisions, [2, 3, 4])
        self.assertEqual(gated.events[0]["symbol"], "sh.600584")
        self.assertEqual(gated.events[0]["trades"], [])
        self.assertEqual(gated.events[1]["symbol"], "sz.000001")
        self.assertEqual(gated.events[2]["symbol"], "sh.600000")

    def test_delete_is_hard_and_publishes_empty_scoped_snapshot(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
            trade_id = self.publisher.events[-1]["trades"][0]["trade_id"]
            result = api.dispatch("delete_trade", _request("delete_trade",
                {"trade_id": trade_id, "trade_scope": "real"}))
        self.assertTrue(result["accepted"])
        event = self.publisher.events[-1]
        self.assertEqual(event["trade_revision"], 2)
        self.assertEqual(event["trades"], [])
        self.assertEqual(event["symbol"], "sh.600584")
        self.assertEqual(event["trade_date"], "2026-07-24")
        with self._api() as api:
            api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertEqual(self.publisher.events[-1]["trades"], [])

    def test_revision_is_monotonic_within_generation(self) -> None:
        with self._api() as api:
            revisions = []
            for _ in range(3):
                api.dispatch("create_trade", _request("create_trade",
                    {"trade": _draft()}))
                revisions.append(self.publisher.events[-1]["trade_revision"])
        self.assertEqual(revisions, [1, 2, 3])
        self.assertEqual(api.trade_revision, 3)

    # -- failure paths: no publish, no bump ----------------------------

    def test_create_simulated_scope_is_unsupported(self) -> None:
        with self._api() as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(trade_scope="simulated")}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "unsupported_trade_scope")
        self.assertEqual(result["error"]["category"], "invalid_request")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(self.publisher.events, [])
        self.assertEqual(api.trade_revision, 0)

    def test_create_persistence_failure_does_not_publish_or_bump(self) -> None:
        repo = _ScriptedRepository()
        repo.create_failure = RepositoryPersistenceError("disk full")
        with self._api(repository=repo) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_persist_failed")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(self.publisher.events, [])
        self.assertEqual(api.trade_revision, 0)

    def test_update_missing_trade_returns_not_found_without_publish(self) -> None:
        with self._api() as api:
            result = api.dispatch("update_trade", _request("update_trade",
                {"trade_id": "nope", "trade": _draft()}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_not_found")
        self.assertEqual(self.publisher.events, [])

    def test_delete_missing_trade_returns_not_found_without_publish(self) -> None:
        with self._api() as api:
            result = api.dispatch("delete_trade", _request("delete_trade",
                {"trade_id": "nope", "trade_scope": "real"}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_not_found")
        self.assertEqual(self.publisher.events, [])

    def test_read_only_repository_maps_to_persistence_error(self) -> None:
        repo = _ScriptedRepository()
        repo.create_failure = RepositoryReadOnlyError("read-only file")
        with self._api(repository=repo) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "repository_read_only")
        self.assertFalse(result["error"]["retryable"])

    def test_failed_list_does_not_publish_empty_fact(self) -> None:
        repo = _ScriptedRepository()
        repo.list_failure = RepositoryPersistenceError("read failed")
        with self._api(repository=repo) as api:
            result = api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertFalse(result["accepted"])
        self.assertEqual(self.publisher.events, [])  # no empty fact published

    def test_unknown_command_is_rejected(self) -> None:
        with self._api() as api:
            result = api.dispatch("not_a_trade_command", _request("not_a_trade_command", {}))
        self.assertFalse(result["accepted"])

    def test_service_generation_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            TradeCommandApi(
                TradeService(
                    _ScriptedRepository(), eligibility=AllowAllEligibility()
                ),
                service_generation=0, publisher=self.publisher)

    def test_no_publisher_does_not_raise(self) -> None:
        # A command API without a publisher (transport not wired) still accepts
        # and persists; it simply publishes nothing.
        with self._api() as api:
            api._publisher = None  # noqa: SLF001 - simulate unwired transport
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertTrue(result["accepted"])


class _ScriptedEligibility(InstrumentEligibilityPort):
    """Fake eligibility port returning a scripted status or raising."""

    def __init__(self, status: str | None, *, raises: bool = False) -> None:
        self._status = status
        self._raises = raises

    def check_eligibility(self, symbol: str) -> str | None:
        if self._raises:
            raise RuntimeError("securities store unreachable")
        return self._status


class TradeEligibilityErrorMappingTests(unittest.TestCase):
    """Issue #163: TradeEligibilityError maps to trade_not_allowed."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "trades.sqlite3"
        self.publisher = _CapturingPublisher()

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    @contextmanager
    def _api(self, eligibility: InstrumentEligibilityPort):
        database = open_app_database(self.db_path)
        repository = SqliteTradeRepository(database)
        service = TradeService(repository, eligibility=eligibility)
        api = TradeCommandApi(
            service,
            service_generation=5,
            publisher=self.publisher,
        )
        yield api

    def test_index_security_maps_to_trade_not_allowed(self) -> None:
        eligibility = _ScriptedEligibility("index")
        with self._api(eligibility) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.000001")}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_not_allowed")
        self.assertEqual(result["error"]["category"], "invalid_request")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(self.publisher.events, [])

    def test_unknown_security_maps_to_trade_not_allowed(self) -> None:
        eligibility = _ScriptedEligibility(None)
        with self._api(eligibility) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.999999")}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_not_allowed")
        self.assertEqual(result["error"]["category"], "invalid_request")
        self.assertFalse(result["error"]["retryable"])

    def test_eligibility_service_failure_maps_to_trade_not_allowed(self) -> None:
        eligibility = _ScriptedEligibility(None, raises=True)
        with self._api(eligibility) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.600584")}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "trade_not_allowed")
        self.assertEqual(result["error"]["category"], "invalid_request")
        self.assertFalse(result["error"]["retryable"])

    def test_tradable_security_is_accepted(self) -> None:
        eligibility = _ScriptedEligibility("tradable")
        with self._api(eligibility) as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.600584")}))
        self.assertTrue(result["accepted"])


if __name__ == "__main__":
    unittest.main()
