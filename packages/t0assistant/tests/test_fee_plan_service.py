from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from packages.t0assistant.preferences import (
    FeePlanNotFoundError,
    FeePlanService,
)
from packages.t0assistant.repositories import (
    FeePlanRecord,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryReadOnlyError,
    SqliteFeePlanRepository,
    SqliteTradeRepository,
    TransferFeeSide,
    open_app_database,
)
from packages.t0assistant.trading import TradeDraft, TradeRecord


class FeePlanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "t0-assistant.sqlite3"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_default_plan_is_created_on_first_initialization(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            plans = service.list_plans()

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].fee_plan_id, "shenwan-hongyuan")
        self.assertEqual(plans[0].name, "申万宏源（示例）")
        self.assertEqual(plans[0].a_share_commission_rate, Decimal("0.0003"))
        self.assertTrue(plans[0].stamp_duty_sell_only)

    def test_default_plan_initialization_is_idempotent(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqliteFeePlanRepository(database)
            service = FeePlanService(repository)
            first = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)
            service.seed_default_plan()
            second = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)

        self.assertEqual(first, second)

        with open_app_database(self.db_path) as database:
            repository = SqliteFeePlanRepository(database)
            self.assertEqual(repository.list_all(), (first,))

    def test_user_edits_to_default_plan_are_preserved_across_restarts(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            original = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)
            updated = replace(original, name="申万宏源（已编辑）")
            service.update_plan(updated)

        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            plan = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)

        self.assertEqual(plan.name, "申万宏源（已编辑）")

    def test_default_plan_does_not_resurrect_after_deletion(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            service.delete_plan(FeePlanService.DEFAULT_PLAN_ID)

        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            plans = service.list_plans()

        self.assertEqual(plans, ())

    def test_custom_plans_can_be_created_and_deleted(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            custom = FeePlanRecord(
                fee_plan_id="custom-1",
                name="Custom Broker",
                a_share_commission_rate=Decimal("0.00025"),
                a_share_min_commission=Decimal("5"),
                etf_commission_rate=Decimal("0.00015"),
                etf_min_commission=Decimal("5"),
                stamp_duty_rate=Decimal("0.0005"),
                stamp_duty_sell_only=True,
                transfer_fee_rate=Decimal("0.00001"),
                transfer_fee_side=TransferFeeSide.BOTH,
                transfer_fee_enabled=True,
            )
            service.create_plan(custom)
            self.assertEqual(service.get_plan("custom-1"), custom)
            self.assertTrue(service.delete_plan("custom-1"))
            self.assertIsNone(service._repository.get("custom-1"))

    def test_updating_or_deleting_plan_does_not_change_saved_trade_fees(self) -> None:
        with open_app_database(self.db_path) as database:
            plans = SqliteFeePlanRepository(database)
            trades = SqliteTradeRepository(database)
            service = FeePlanService(plans)
            service.seed_default_plan()

            trade = TradeRecord(
                "trade-1",
                TradeDraft.from_mapping(
                    {
                        "trade_scope": "real",
                        "symbol": "sh.600584",
                        "side": "buy",
                        "executed_at": "2026-07-22 10:03:00",
                        "price": 38.25,
                        "quantity": 200,
                        "fee": 5.01,
                        "note": "manual fill",
                        "fee_plan_id": "shenwan-hongyuan",
                    }
                ),
            )
            trades.create(trade)

            plan = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)
            updated = replace(
                plan,
                a_share_commission_rate=Decimal("0.0005"),
                a_share_min_commission=Decimal("10"),
            )
            service.update_plan(updated)
            self.assertEqual(trades.get("trade-1").trade.fee, Decimal("5.01"))

            service.delete_plan(FeePlanService.DEFAULT_PLAN_ID)
            self.assertEqual(trades.get("trade-1").trade.fee, Decimal("5.01"))

    def test_read_only_repository_rejects_mutations(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            service.seed_default_plan()

        with open_app_database(self.db_path, force_read_only=True) as database:
            service = FeePlanService(
                SqliteFeePlanRepository(database), seed_defaults=False
            )
            self.assertFalse(service.capability.writable)
            with self.assertRaises(RepositoryReadOnlyError):
                service.delete_plan(FeePlanService.DEFAULT_PLAN_ID)

    def test_read_only_default_constructor_allows_reading_existing_plans(
        self,
    ) -> None:
        with open_app_database(self.db_path) as database:
            writable_service = FeePlanService(SqliteFeePlanRepository(database))
            expected = writable_service.get_plan(FeePlanService.DEFAULT_PLAN_ID)

        with open_app_database(self.db_path, force_read_only=True) as database:
            service = FeePlanService(SqliteFeePlanRepository(database))
            self.assertFalse(service.capability.writable)
            self.assertEqual(
                service.get_plan(FeePlanService.DEFAULT_PLAN_ID), expected
            )
            with self.assertRaises(RepositoryReadOnlyError):
                service.create_plan(expected)
            with self.assertRaises(RepositoryReadOnlyError):
                service.update_plan(expected)
            with self.assertRaises(RepositoryReadOnlyError):
                service.delete_plan(FeePlanService.DEFAULT_PLAN_ID)

    def test_get_missing_plan_raises_not_found(self) -> None:
        with open_app_database(self.db_path) as database:
            service = FeePlanService(
                SqliteFeePlanRepository(database), seed_defaults=False
            )
            with self.assertRaises(FeePlanNotFoundError):
                service.get_plan("missing-plan")

    def test_duplicate_plan_id_raises_conflict(self) -> None:
        with open_app_database(self.db_path) as database:
            repository = SqliteFeePlanRepository(database)
            service = FeePlanService(repository)
            existing = service.get_plan(FeePlanService.DEFAULT_PLAN_ID)
            with self.assertRaises(RepositoryConflictError):
                service.create_plan(existing)


if __name__ == "__main__":
    unittest.main()
