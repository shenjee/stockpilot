from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
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
    TradeCommandApi,
    TradeRecord,
    TradeService,
)
from packages.t0assistant.trading.api import TradeEventPublisher


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

    def publish_trades_changed(self, **payload) -> None:
        self.events.append(payload)


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
        return tuple(r for r in self._records.values() if r.trade.symbol == symbol)

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
    def _api(self, *, service_generation: int = 5, repository=None):
        database = None
        if repository is None:
            database = open_app_database(self.db_path)
            repository = SqliteTradeRepository(database)
        service = TradeService(repository)
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

    def test_list_trades_empty_repository_publishes_empty_snapshot(self) -> None:
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
        self.assertEqual(event["service_generation"], 5)

    def test_list_trades_publishes_full_snapshot_across_symbols_and_dates(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sh.600584", executed_at="2026-07-24 10:03:00")}))
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(symbol="sz.000001", executed_at="2026-07-25 14:10:00",
                                 side="sell")}))
            self.publisher.events.clear()
            # list_trades asks for one symbol/date but must publish EVERY trade.
            api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "real", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertEqual(len(self.publisher.events), 1)
        trades = self.publisher.events[0]["trades"]
        symbols = {t["symbol"] for t in trades}
        self.assertEqual(symbols, {"sh.600584", "sz.000001"})

    def test_list_trades_rejects_simulated_scope(self) -> None:
        with self._api() as api:
            result = api.dispatch("list_trades", _request("list_trades",
                {"trade_scope": "simulated", "symbol": "sh.600584",
                 "trade_date": "2026-07-24"}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "invalid_trade_request")
        self.assertEqual(self.publisher.events, [])

    # -- create / update / delete --------------------------------------

    def test_create_bumps_revision_and_publishes_snapshot(self) -> None:
        with self._api() as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertTrue(result["accepted"])
        self.assertEqual(self.publisher.events[-1]["trade_revision"], 1)
        self.assertEqual(len(self.publisher.events[-1]["trades"]), 1)
        trade = self.publisher.events[-1]["trades"][0]
        self.assertEqual(trade["trade_scope"], "real")
        self.assertEqual(trade["bucket_start"], "2026-07-24 10:00:00")
        self.assertEqual(trade["fee"], 5.01)

    def test_update_preserves_trade_id_and_confirmed_fee(self) -> None:
        with self._api() as api:
            create = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
            self.assertTrue(create["accepted"])
            trade_id = self.publisher.events[-1]["trades"][0]["trade_id"]
            result = api.dispatch("update_trade", _request("update_trade",
                {"trade_id": trade_id,
                 "trade": _draft(price=40.00, fee=9.99, note="edited")}))
        self.assertTrue(result["accepted"])
        trade = self.publisher.events[-1]["trades"][0]
        self.assertEqual(trade["trade_id"], trade_id)  # identity preserved
        self.assertEqual(trade["price"], 40.0)
        self.assertEqual(trade["fee"], 9.99)  # confirmed fee, not recomputed
        self.assertEqual(trade["note"], "edited")
        self.assertEqual(self.publisher.events[-1]["trade_revision"], 2)

    def test_delete_is_hard_and_publishes_empty_snapshot(self) -> None:
        with self._api() as api:
            api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
            trade_id = self.publisher.events[-1]["trades"][0]["trade_id"]
            result = api.dispatch("delete_trade", _request("delete_trade",
                {"trade_id": trade_id, "trade_scope": "real"}))
        self.assertTrue(result["accepted"])
        self.assertEqual(self.publisher.events[-1]["trade_revision"], 2)
        self.assertEqual(self.publisher.events[-1]["trades"], [])
        # The trade is truly gone: a fresh list publishes an empty snapshot.
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

    def test_create_validation_failure_publishes_and_bumps_nothing(self) -> None:
        with self._api() as api:
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft(trade_scope="simulated")}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error"]["error_code"], "invalid_trade_request")
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
            TradeCommandApi(TradeService(_ScriptedRepository()),
                            service_generation=0, publisher=self.publisher)

    def test_no_publisher_does_not_raise(self) -> None:
        # A command API without a publisher (transport not wired) still accepts
        # and persists; it simply publishes nothing.
        with self._api() as api:
            api._publisher = None  # noqa: SLF001 - simulate unwired transport
            result = api.dispatch("create_trade", _request("create_trade",
                {"trade": _draft()}))
        self.assertTrue(result["accepted"])


if __name__ == "__main__":
    unittest.main()
