from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.t0assistant.repositories import (
    AppDatabaseCompatibilityError,
    AppDatabaseUnavailableError,
    FeePlanRecord,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
    RepositoryReadOnlyError,
    SCHEMA_VERSION,
    SqliteFeePlanRepository,
    SqliteTradeRepository,
    TransferFeeSide,
    open_app_database,
)
from packages.t0assistant.repositories.app_database import connect
from packages.t0assistant.trading import TradeDraft, TradeRecord


class TradingRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0-assistant.sqlite3"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    @staticmethod
    def _trade(
        trade_id: str = "trade-1",
        *,
        symbol: str = "sh.600584",
        executed_at: str = "2026-07-24 10:03:47",
        side: str = "buy",
        fee: Decimal | None = Decimal("5.01"),
        fee_plan_id: str | None = "fee-plan-1",
    ) -> TradeRecord:
        return TradeRecord(
            trade_id,
            TradeDraft.from_mapping(
                {
                    "trade_scope": "real",
                    "symbol": symbol,
                    "side": side,
                    "executed_at": executed_at,
                    "price": Decimal("38.2500"),
                    "quantity": 200,
                    "fee": fee,
                    "note": "manual fill",
                    "fee_plan_id": fee_plan_id,
                }
            ),
        )

    @staticmethod
    def _plan(
        fee_plan_id: str = "fee-plan-1", *, name: str = "申万宏源"
    ) -> FeePlanRecord:
        return FeePlanRecord(
            fee_plan_id=fee_plan_id,
            name=name,
            a_share_commission_rate=Decimal("0.0003"),
            a_share_min_commission=Decimal("5"),
            etf_commission_rate=Decimal("0.0002"),
            etf_min_commission=Decimal("5"),
            stamp_duty_rate=Decimal("0.0005"),
            stamp_duty_sell_only=True,
            transfer_fee_rate=Decimal("0.00001"),
            transfer_fee_side=TransferFeeSide.BOTH,
            transfer_fee_enabled=True,
        )

    def test_version_one_database_migrates_in_place_and_preserves_preferences(
        self,
    ) -> None:
        with open_app_database(self.db_path) as database:
            database.connection.execute(
                """
                UPDATE preferences
                SET last_symbol = 'sh.600584'
                WHERE singleton_id = 1
                """
            )
            database.connection.execute(
                "UPDATE app_schema SET schema_version = 1 WHERE singleton_id = 1"
            )
            database.connection.execute("DROP TABLE trades")
            database.connection.execute("DROP TABLE fee_plans")
            database.connection.commit()

        with open_app_database(self.db_path) as migrated:
            schema_version = migrated.connection.execute(
                "SELECT schema_version FROM app_schema WHERE singleton_id = 1"
            ).fetchone()[0]
            last_symbol = migrated.connection.execute(
                "SELECT last_symbol FROM preferences WHERE singleton_id = 1"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in migrated.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(schema_version, SCHEMA_VERSION)
        self.assertEqual(last_symbol, "sh.600584")
        self.assertTrue({"preferences", "trades", "fee_plans"} <= tables)

    def test_version_two_database_migrates_in_place_and_preserves_trades_plans_and_preferences(
        self,
    ) -> None:
        with open_app_database(self.db_path) as database:
            database.connection.execute(
                """
                UPDATE preferences
                SET last_symbol = 'sh.600584', chart_split = '50_50'
                WHERE singleton_id = 1
                """
            )
            database.connection.commit()
            plans = SqliteFeePlanRepository(database)
            trades = SqliteTradeRepository(database)
            plan = self._plan()
            trade = self._trade()
            plans.create(plan)
            trades.create(trade)
            database.connection.execute(
                "UPDATE app_schema SET schema_version = 2 WHERE singleton_id = 1"
            )
            database.connection.execute("DROP TABLE fee_plan_meta")
            database.connection.commit()

        with open_app_database(self.db_path) as migrated:
            schema_version = migrated.connection.execute(
                "SELECT schema_version FROM app_schema WHERE singleton_id = 1"
            ).fetchone()[0]
            last_symbol = migrated.connection.execute(
                "SELECT last_symbol FROM preferences WHERE singleton_id = 1"
            ).fetchone()[0]
            chart_split = migrated.connection.execute(
                "SELECT chart_split FROM preferences WHERE singleton_id = 1"
            ).fetchone()[0]
            migrated_plans = SqliteFeePlanRepository(migrated)
            migrated_trades = SqliteTradeRepository(migrated)

            self.assertEqual(schema_version, SCHEMA_VERSION)
            self.assertEqual(last_symbol, "sh.600584")
            self.assertEqual(chart_split, "50_50")
            self.assertEqual(migrated_plans.list_all(), (plan,))
            self.assertEqual(migrated_trades.list_all(), (trade,))

    def test_private_schema_has_business_audit_fields_without_market_lineage(
        self,
    ) -> None:
        with open_app_database(self.db_path) as database:
            columns = {}
            for table in ("trades", "fee_plans"):
                columns[table] = {
                    row[1]
                    for row in database.connection.execute(
                        f"PRAGMA table_info({table})"
                    )
                }

        self.assertTrue({"created_at", "updated_at"} <= columns["trades"])
        self.assertTrue({"created_at", "updated_at"} <= columns["fee_plans"])
        for table_columns in columns.values():
            self.assertNotIn("fetch_run_id", table_columns)
            self.assertNotIn("source", table_columns)
            self.assertNotIn("source_updated_at", table_columns)

    def test_migration_is_atomic_when_existing_table_is_incompatible(self) -> None:
        with open_app_database(self.db_path) as database:
            database.connection.execute(
                "UPDATE app_schema SET schema_version = 1 WHERE singleton_id = 1"
            )
            database.connection.execute("DROP TABLE trades")
            database.connection.execute("DROP TABLE fee_plans")
            database.connection.execute(
                "CREATE TABLE trades(trade_id TEXT PRIMARY KEY)"
            )
            database.connection.commit()

        with self.assertRaises(AppDatabaseCompatibilityError):
            open_app_database(self.db_path)

        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT schema_version FROM app_schema WHERE singleton_id = 1"
                ).fetchone()[0],
                1,
            )
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'fee_plans'
                    """
                ).fetchone()
            )
        finally:
            connection.close()

    def test_trade_crud_queries_and_permanent_delete(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqliteTradeRepository(database)
            first = self._trade()
            other_day = self._trade(
                "trade-2",
                executed_at="2026-07-25 09:35:00",
                side="sell",
            )
            other_symbol = self._trade(
                "trade-3",
                symbol="sz.000001",
                executed_at="2026-07-24 09:31:00",
            )

            self.assertEqual(repository.create(first), first)
            repository.create(other_day)
            repository.create(other_symbol)
            self.assertEqual(repository.get(first.trade_id), first)
            self.assertEqual(
                repository.list_for_symbol_and_date(
                    "sh.600584", date(2026, 7, 24)
                ),
                (first,),
            )

            updated = TradeRecord(
                first.trade_id,
                replace(first.trade, fee=Decimal("6.25"), note="confirmed"),
            )
            self.assertEqual(repository.update(updated), updated)
            self.assertEqual(repository.get(first.trade_id), updated)
            self.assertTrue(repository.delete(first.trade_id))
            self.assertFalse(repository.delete(first.trade_id))
            self.assertIsNone(repository.get(first.trade_id))

    def test_optional_fee_and_empty_note_round_trip(self) -> None:
        trade = TradeRecord(
            "trade-without-fee",
            replace(self._trade().trade, fee=None, fee_plan_id=None, note=""),
        )
        with open_app_database(self.db_path) as database:
            repository = SqliteTradeRepository(database)
            repository.create(trade)
            self.assertEqual(repository.get(trade.trade_id), trade)

    def test_duplicate_identities_raise_conflict_without_replacing_records(
        self,
    ) -> None:
        trade = self._trade()
        plan = self._plan()
        with open_app_database(self.db_path) as database:
            trades = SqliteTradeRepository(database)
            plans = SqliteFeePlanRepository(database)
            trades.create(trade)
            plans.create(plan)

            with self.assertRaises(RepositoryConflictError) as trade_conflict:
                trades.create(
                    TradeRecord(
                        trade.trade_id,
                        replace(trade.trade, note="must not replace"),
                    )
                )
            with self.assertRaises(RepositoryConflictError) as plan_conflict:
                plans.create(replace(plan, name="must not replace"))

            self.assertEqual(trade_conflict.exception.entity, "trade")
            self.assertEqual(trade_conflict.exception.conflict_id, trade.trade_id)
            self.assertEqual(plan_conflict.exception.entity, "fee_plan")
            self.assertEqual(
                plan_conflict.exception.conflict_id, plan.fee_plan_id
            )
            self.assertEqual(trades.get(trade.trade_id), trade)
            self.assertEqual(plans.get(plan.fee_plan_id), plan)

    def test_simulated_trade_is_never_persisted(self) -> None:
        simulated = TradeRecord(
            "sim-1",
            replace(self._trade().trade, trade_scope="simulated"),
        )
        with open_app_database(self.db_path) as database:
            repository = SqliteTradeRepository(database)
            with self.assertRaisesRegex(ValueError, "only real trades"):
                repository.create(simulated)
            self.assertEqual(repository.list_all(), ())

    def test_fee_plan_crud_and_deletion_do_not_rewrite_historical_trade(self) -> None:
        with open_app_database(self.db_path) as database:
            plans = SqliteFeePlanRepository(database)
            trades = SqliteTradeRepository(database)
            plan = self._plan()
            trade = self._trade()

            self.assertEqual(plans.create(plan), plan)
            trades.create(trade)
            updated = replace(
                plan,
                name="申万宏源（更新）",
                a_share_commission_rate=Decimal("0.00025"),
            )
            self.assertEqual(plans.update(updated), updated)
            self.assertEqual(plans.list_all(), (updated,))
            self.assertEqual(trades.get(trade.trade_id), trade)

            self.assertTrue(plans.delete(plan.fee_plan_id))
            self.assertFalse(plans.delete(plan.fee_plan_id))
            self.assertIsNone(plans.get(plan.fee_plan_id))
            self.assertEqual(trades.get(trade.trade_id), trade)

    def test_default_plan_initialization_is_atomic_and_retryable_after_failure(
        self,
    ) -> None:
        plan = self._plan("default-plan")
        with open_app_database(self.db_path) as database:
            plans = SqliteFeePlanRepository(database)
            database.connection.execute(
                """
                CREATE TRIGGER reject_fee_plan_meta_write
                BEFORE INSERT ON fee_plan_meta
                BEGIN
                    SELECT RAISE(ABORT, 'injected meta write failure');
                END
                """
            )
            database.connection.commit()

            with self.assertRaisesRegex(
                RepositoryPersistenceError, "未确认持久化"
            ):
                plans.initialize_default_plan(plan)

            self.assertEqual(plans.list_all(), ())
            meta_row = database.connection.execute(
                "SELECT default_plan_initialized FROM fee_plan_meta WHERE singleton_id = 1"
            ).fetchone()
            self.assertTrue(
                meta_row is None or not meta_row["default_plan_initialized"]
            )

            database.connection.execute(
                "DROP TRIGGER reject_fee_plan_meta_write"
            )
            database.connection.commit()

            self.assertEqual(plans.initialize_default_plan(plan), plan)
            self.assertEqual(plans.list_all(), (plan,))
            self.assertEqual(
                plans.initialize_default_plan(plan),
                plan,
            )

    def test_fee_plan_rejects_invalid_persistent_values(self) -> None:
        invalid_changes = (
            ("fee_plan_id", ""),
            ("name", "   "),
            ("a_share_commission_rate", Decimal("-0.0001")),
            ("a_share_min_commission", Decimal("-1")),
            ("etf_commission_rate", float("inf")),
            ("etf_min_commission", None),
            ("stamp_duty_rate", Decimal("-0.0001")),
            ("stamp_duty_sell_only", 1),
            ("transfer_fee_rate", Decimal("-0.0001")),
            ("transfer_fee_side", "neither"),
            ("transfer_fee_enabled", 0),
        )
        plan = self._plan()
        for field_name, value in invalid_changes:
            with self.subTest(field=field_name), self.assertRaises(ValueError):
                replace(plan, **{field_name: value})

    def test_missing_updates_fail_without_creating_records(self) -> None:
        with open_app_database(self.db_path) as database:
            trades = SqliteTradeRepository(database)
            plans = SqliteFeePlanRepository(database)
            with self.assertRaises(RepositoryNotFoundError):
                trades.update(self._trade())
            with self.assertRaises(RepositoryNotFoundError):
                plans.update(self._plan())
            self.assertEqual(trades.list_all(), ())
            self.assertEqual(plans.list_all(), ())

    def test_failed_trade_write_rolls_back_without_partial_change(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqliteTradeRepository(database)
            original = self._trade()
            repository.create(original)
            database.connection.execute(
                """
                CREATE TRIGGER reject_trade_update
                BEFORE UPDATE ON trades
                BEGIN
                    SELECT RAISE(ABORT, 'injected write failure');
                END
                """
            )
            changed = TradeRecord(
                original.trade_id,
                replace(original.trade, note="must roll back"),
            )

            with self.assertRaisesRegex(
                RepositoryPersistenceError, "未确认持久化"
            ):
                repository.update(changed)
            self.assertEqual(repository.get(original.trade_id), original)

    def test_audit_fields_exist_and_created_at_is_stable_on_update(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqliteTradeRepository(database)
            original = self._trade()
            repository.create(original)
            before = database.connection.execute(
                """
                SELECT created_at, updated_at
                FROM trades WHERE trade_id = ?
                """,
                (original.trade_id,),
            ).fetchone()
            repository.update(
                TradeRecord(
                    original.trade_id,
                    replace(original.trade, note="updated"),
                )
            )
            after = database.connection.execute(
                """
                SELECT created_at, updated_at
                FROM trades WHERE trade_id = ?
                """,
                (original.trade_id,),
            ).fetchone()

            self.assertTrue(before["created_at"])
            self.assertTrue(before["updated_at"])
            self.assertEqual(after["created_at"], before["created_at"])
            self.assertTrue(after["updated_at"])

    def test_read_only_database_allows_reads_and_rejects_all_mutations(self) -> None:
        trade = self._trade()
        plan = self._plan()
        with open_app_database(self.db_path) as writable:
            SqliteFeePlanRepository(writable).create(plan)
            SqliteTradeRepository(writable).create(trade)

        with open_app_database(self.db_path, force_read_only=True) as read_only:
            trades = SqliteTradeRepository(read_only)
            plans = SqliteFeePlanRepository(read_only)
            self.assertEqual(trades.get(trade.trade_id), trade)
            self.assertEqual(plans.get(plan.fee_plan_id), plan)
            self.assertFalse(trades.capability.writable)

            mutations = (
                ("create trade", lambda: trades.create(self._trade("trade-2"))),
                ("update trade", lambda: trades.update(trade)),
                ("delete trade", lambda: trades.delete(trade.trade_id)),
                (
                    "create fee plan",
                    lambda: plans.create(self._plan("fee-plan-2")),
                ),
                ("update fee plan", lambda: plans.update(plan)),
                ("delete fee plan", lambda: plans.delete(plan.fee_plan_id)),
            )
            for name, mutation in mutations:
                with self.subTest(name=name), self.assertRaises(
                    RepositoryReadOnlyError
                ):
                    mutation()

        with open_app_database(self.db_path, force_read_only=True) as reopened:
            self.assertEqual(
                SqliteTradeRepository(reopened).list_all(), (trade,)
            )
            self.assertEqual(
                SqliteFeePlanRepository(reopened).list_all(), (plan,)
            )

    def test_force_read_only_missing_database_uses_stable_unavailable_error(
        self,
    ) -> None:
        self.assertFalse(self.db_path.exists())
        with self.assertRaisesRegex(
            AppDatabaseUnavailableError, "cannot be opened read-only"
        ):
            open_app_database(self.db_path, force_read_only=True)
        self.assertFalse(self.db_path.exists())

    def test_write_open_failure_automatically_degrades_to_read_only(self) -> None:
        trade = self._trade()
        with open_app_database(self.db_path) as writable:
            SqliteTradeRepository(writable).create(trade)

        real_connect = connect
        attempts = 0

        def fail_write_then_read(path, *, read_only=False):
            nonlocal attempts
            attempts += 1
            if not read_only:
                raise sqlite3.OperationalError("injected read-only filesystem")
            return real_connect(path, read_only=True)

        with patch(
            "packages.t0assistant.repositories.app_database.connect",
            side_effect=fail_write_then_read,
        ):
            with open_app_database(self.db_path) as degraded:
                repository = SqliteTradeRepository(degraded)
                self.assertEqual(repository.get(trade.trade_id), trade)
                self.assertTrue(repository.capability.readable)
                self.assertFalse(repository.capability.writable)
                self.assertIn("不可写", repository.capability.reason or "")
                with self.assertRaises(RepositoryReadOnlyError):
                    repository.delete(trade.trade_id)

        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
