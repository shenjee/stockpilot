from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from packages.t0assistant.preferences import PreferenceCapability
from packages.t0assistant.repositories import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
    RepositoryReadOnlyError,
    SqliteTradeRepository,
    open_app_database,
)
from packages.t0assistant.trading import (
    TradeDraft,
    TradeMarker,
    TradeMarkerProjection,
    TradeRecord,
    TradeService,
    TradeValidationError,
)


def _draft(**overrides) -> dict:
    base = {
        "trade_scope": "real",
        "symbol": "sh.600584",
        "side": "buy",
        "executed_at": "2026-07-24 10:03:47",
        "price": 38.25,
        "quantity": 200,
        "fee": 5.01,
        "note": "manual fill",
        "fee_plan_id": "shenwan-hongyuan",
    }
    base.update(overrides)
    return base


class _FailingCreateRepository:
    """Minimal port impl whose ``create`` always fails, with no memory."""

    def __init__(self) -> None:
        self.attempted: list[TradeRecord] = []

    @property
    def capability(self) -> PreferenceCapability:
        return PreferenceCapability(readable=True, writable=True)

    def create(self, record: TradeRecord) -> TradeRecord:
        self.attempted.append(record)
        raise RepositoryPersistenceError("simulated write failure")

    def get(self, trade_id: str) -> TradeRecord | None:
        return None

    def list_all(self) -> tuple[TradeRecord, ...]:
        return ()

    def list_for_symbol_and_date(self, symbol, trade_date) -> tuple[TradeRecord, ...]:
        return ()

    def update(self, record: TradeRecord) -> TradeRecord:
        raise RepositoryNotFoundError("not found")

    def delete(self, trade_id: str) -> bool:
        return False


class TradeServiceCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0-assistant.sqlite3"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    @contextmanager
    def _service(self, *, id_factory=None):
        with open_app_database(self.db_path) as database:
            yield TradeService(
                SqliteTradeRepository(database), id_factory=id_factory
            )

    # -- create --------------------------------------------------------

    def test_create_from_mapping_persists_and_returns_record(self) -> None:
        with self._service() as service:
            record = service.create_trade(_draft())
        self.assertIsInstance(record, TradeRecord)
        self.assertTrue(record.trade_id)
        self.assertEqual(record.trade.symbol, "sh.600584")
        self.assertEqual(record.trade.quantity, 200)

    def test_create_from_trade_draft(self) -> None:
        with self._service() as service:
            record = service.create_trade(TradeDraft.from_mapping(_draft()))
        self.assertEqual(record.trade.price, Decimal("38.25"))

    def test_create_rejects_simulated_scope(self) -> None:
        with self._service() as service:
            with self.assertRaises(TradeValidationError) as ctx:
                service.create_trade(_draft(trade_scope="simulated"))
        self.assertEqual(ctx.exception.field, "trade_scope")

    def test_create_uses_injected_id_factory(self) -> None:
        with self._service(id_factory=lambda: "fixed-id") as service:
            record = service.create_trade(_draft())
            self.assertEqual(record.trade_id, "fixed-id")
            self.assertEqual(service.get_trade("fixed-id").trade_id, "fixed-id")

    # -- fact-after-repo-success --------------------------------------

    def test_failed_create_does_not_become_a_fact(self) -> None:
        """A duplicate-id create fails and the second trade is never cached."""

        with self._service(id_factory=lambda: "dup-id") as service:
            first = service.create_trade(_draft())
            self.assertEqual(first.trade_id, "dup-id")
            with self.assertRaises(RepositoryConflictError):
                service.create_trade(_draft(side="sell"))
            trades = service.list_all_trades()

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].trade_id, "dup-id")
        self.assertEqual(trades[0].trade.side.value, "buy")

    def test_failed_create_leaves_no_memory_success(self) -> None:
        fake = _FailingCreateRepository()
        service = TradeService(fake)
        with self.assertRaises(RepositoryPersistenceError):
            service.create_trade(_draft())

        self.assertEqual(len(fake.attempted), 1)
        self.assertIsInstance(fake.attempted[0], TradeRecord)
        # The service keeps no in-memory cache, so nothing is surfaced.
        self.assertEqual(service.list_all_trades(), ())
        self.assertIsNone(service.get_trade(fake.attempted[0].trade_id))

    # -- update --------------------------------------------------------

    def test_update_preserves_identity_and_persists_provided_fee(self) -> None:
        with self._service(id_factory=lambda: "trade-1") as service:
            service.create_trade(_draft(fee=5.01))
            updated = service.update_trade(
                "trade-1", _draft(fee=7.50, quantity=300, side="sell")
            )
            self.assertEqual(updated.trade_id, "trade-1")
            self.assertEqual(updated.trade.fee, Decimal("7.50"))
            self.assertEqual(updated.trade.quantity, 300)
            self.assertEqual(updated.trade.side.value, "sell")
            self.assertEqual(service.get_trade("trade-1").trade.fee, Decimal("7.50"))

    def test_update_persists_null_fee_without_recomputing(self) -> None:
        with self._service(id_factory=lambda: "trade-1") as service:
            service.create_trade(_draft(fee=5.01))
            updated = service.update_trade("trade-1", _draft(fee=None))
            self.assertIsNone(updated.trade.fee)
            self.assertIsNone(service.get_trade("trade-1").trade.fee)

    def test_update_missing_trade_raises_not_found(self) -> None:
        with self._service() as service:
            with self.assertRaises(RepositoryNotFoundError):
                service.update_trade("missing", _draft())

    def test_update_rejects_simulated_scope(self) -> None:
        with self._service(id_factory=lambda: "trade-1") as service:
            service.create_trade(_draft())
            with self.assertRaises(TradeValidationError):
                service.update_trade("trade-1", _draft(trade_scope="simulated"))

    # -- delete --------------------------------------------------------

    def test_delete_is_permanent_and_recreate_is_allowed(self) -> None:
        with self._service(id_factory=lambda: "perm-1") as service:
            service.create_trade(_draft())
            self.assertTrue(service.delete_trade("perm-1"))
            self.assertIsNone(service.get_trade("perm-1"))
            # Hard delete leaves no tombstone: the same id can be reused.
            recreated = service.create_trade(_draft(side="sell"))
            self.assertEqual(recreated.trade_id, "perm-1")
            self.assertEqual(service.get_trade("perm-1").trade.side.value, "sell")

    def test_delete_missing_returns_false(self) -> None:
        with self._service() as service:
            self.assertFalse(service.delete_trade("missing"))

    def test_blank_trade_id_is_rejected(self) -> None:
        with self._service() as service:
            for bad in ("", "   "):
                with self.assertRaises(TradeValidationError):
                    service.delete_trade(bad)
                with self.assertRaises(TradeValidationError):
                    service.update_trade(bad, _draft())

    # -- list / get ----------------------------------------------------

    def test_list_trades_filters_by_symbol_and_date(self) -> None:
        with self._service() as service:
            service.create_trade(
                _draft(symbol="sh.600584", executed_at="2026-07-24 10:03:00")
            )
            service.create_trade(
                _draft(symbol="sh.600584", executed_at="2026-07-24 14:10:00")
            )
            service.create_trade(
                _draft(symbol="sz.000001", executed_at="2026-07-24 10:03:00")
            )
            service.create_trade(
                _draft(symbol="sh.600584", executed_at="2026-07-25 10:03:00")
            )

            same_day = service.list_trades("sh.600584", date(2026, 7, 24))
            self.assertEqual(len(same_day), 2)
            for record in same_day:
                self.assertEqual(record.trade.symbol, "sh.600584")
                self.assertEqual(record.trade.executed_at.date(), date(2026, 7, 24))

            all_trades = service.list_all_trades()
            self.assertEqual(len(all_trades), 4)

    def test_get_trade_returns_none_when_missing(self) -> None:
        with self._service() as service:
            self.assertIsNone(service.get_trade("missing"))

    # -- read-only -----------------------------------------------------

    def test_read_only_repository_rejects_mutations_but_allows_reads(self) -> None:
        with open_app_database(self.db_path) as database:
            TradeService(SqliteTradeRepository(database)).create_trade(_draft())

        with open_app_database(self.db_path, force_read_only=True) as database:
            service = TradeService(SqliteTradeRepository(database))
            self.assertFalse(service.capability.writable)
            with self.assertRaises(RepositoryReadOnlyError):
                service.create_trade(_draft())
            with self.assertRaises(RepositoryReadOnlyError):
                service.update_trade("anything", _draft())
            with self.assertRaises(RepositoryReadOnlyError):
                service.delete_trade("anything")
            # Reading is still allowed.
            self.assertGreaterEqual(len(service.list_all_trades()), 1)


class TradeServiceMarkerProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0-assistant.sqlite3"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_markers_for_symbol_and_date_projects_persisted_trades(self) -> None:
        with open_app_database(self.db_path) as database:
            service = TradeService(SqliteTradeRepository(database))
            service.create_trade(
                _draft(side="buy", quantity=200, executed_at="2026-07-24 10:03:00")
            )
            service.create_trade(
                _draft(side="sell", quantity=300, executed_at="2026-07-24 10:04:00")
            )

            markers = service.markers_for("sh.600584", date(2026, 7, 24))

        self.assertEqual(len(markers), 2)
        self.assertTrue(all(isinstance(m, TradeMarker) for m in markers))
        # Both fall in the 10:00 bucket; buy sorts before sell.
        self.assertEqual(markers[0].label, "B2")
        self.assertEqual(markers[1].label, "S3")
        self.assertEqual(markers[0].bucket_start, markers[1].bucket_start)

    def test_project_markers_accepts_records_mappings_and_sequence(self) -> None:
        with open_app_database(self.db_path) as database:
            service = TradeService(SqliteTradeRepository(database))
            record = service.create_trade(_draft(quantity=200))

            from_record = service.project_markers(record)
            from_mapping = service.project_markers(record.to_dict())
            from_sequence = service.project_markers([record, record.to_dict()])

        self.assertEqual(len(from_record), 1)
        self.assertEqual(from_record[0].label, "B2")
        self.assertEqual(len(from_mapping), 1)
        self.assertEqual(from_mapping[0].trade_id, record.trade_id)
        self.assertEqual(len(from_sequence), 2)

    def test_project_markers_uses_injected_projection_port(self) -> None:
        class _CountingProjector:
            def __init__(self) -> None:
                self.calls = 0

            def project(self, trades):
                self.calls += 1
                return ()

        counting: TradeMarkerProjection = _CountingProjector()  # type: ignore[assignment]
        with open_app_database(self.db_path) as database:
            service = TradeService(
                SqliteTradeRepository(database), marker_projection=counting
            )
            service.create_trade(_draft())
            service.markers_for("sh.600584", date(2026, 7, 24))

        self.assertEqual(counting.calls, 1)

    def test_project_markers_rejects_non_trade_input(self) -> None:
        with open_app_database(self.db_path) as database:
            service = TradeService(SqliteTradeRepository(database))
            with self.assertRaises(TradeValidationError):
                service.project_markers([object()])  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
