"""T0-052 acceptance: persistent fee plans and real trades as one App flow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from packages.t0assistant.preferences import FeePlanService
from packages.t0assistant.preferences.fee_plan_api import FeePlanCommandApi
from packages.t0assistant.repositories import (
    SqliteFeePlanRepository,
    SqliteTradeRepository,
    open_app_database,
)
from packages.t0assistant.trading import TradeCommandApi, TradeService


def _request(command: str, payload: dict, request_id: str) -> dict:
    return {
        "schema_version": "t0_app_v1",
        "request_id": request_id,
        "command": command,
        "session_id": None,
        "payload": payload,
    }


class TradeEndToEndAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "t0_assistant.sqlite"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _apis(self, database, *, ids=iter(("trade-1", "trade-2", "trade-3"))):
        trade_service = TradeService(
            SqliteTradeRepository(database), id_factory=lambda: next(ids)
        )
        trade_api = TradeCommandApi(trade_service, service_generation=1)
        plan_service = FeePlanService(SqliteFeePlanRepository(database))
        plan_api = FeePlanCommandApi(plan_service, service_generation=1)
        return trade_service, trade_api, plan_service, plan_api

    def test_fee_crud_real_trade_markers_and_restart_persistence(self) -> None:
        with open_app_database(self.db_path) as database:
            trade_service, trade_api, plans, plan_api = self._apis(database)
            listed = plan_api.dispatch(
                "list_fee_plans", _request("list_fee_plans", {}, "plans-1")
            )
            self.assertTrue(listed["accepted"])
            self.assertEqual(listed["data"]["fee_plans"][0]["fee_plan_id"], "shenwan-hongyuan")

            quote = plan_api.dispatch(
                "calculate_trade_fee",
                _request(
                    "calculate_trade_fee",
                    {
                        "fee_plan_id": "shenwan-hongyuan",
                        "security_type": "a_share",
                        "side": "sell",
                        "price": "38.25",
                        "quantity": 200,
                    },
                    "fee-1",
                ),
            )
            self.assertTrue(quote["accepted"])
            confirmed_fee = quote["data"]["total_fee"]

            draft = {
                "trade_scope": "real",
                "symbol": "sh.600584",
                "side": "sell",
                "executed_at": "2026-07-24 10:03:00",
                "price": 38.25,
                "quantity": 200,
                "fee": confirmed_fee,
                "note": "confirmed fill",
                "fee_plan_id": "shenwan-hongyuan",
            }
            created = trade_api.dispatch(
                "create_trade",
                _request("create_trade", {"trade": draft}, "trade-create"),
            )
            self.assertTrue(created["accepted"])
            record = trade_service.list_all_trades()[0]
            marker = trade_service.project_markers(record)[0].to_dict()
            self.assertEqual(marker["bucket_start"], "2026-07-24 10:00:00")
            self.assertEqual(marker["price"], 38.25)
            self.assertEqual(marker["label"], "S2")

            original_plan = plans.get_plan("shenwan-hongyuan")
            changed_plan = replace(
                original_plan,
                a_share_min_commission=original_plan.a_share_min_commission * 3,
            )
            updated = plan_api.dispatch(
                "update_fee_plan",
                _request(
                    "update_fee_plan",
                    {"fee_plan": FeePlanCommandApi._wire(changed_plan)},
                    "plan-update",
                ),
            )
            self.assertTrue(updated["accepted"])
            self.assertEqual(
                trade_service.list_all_trades()[0].trade.fee,
                record.trade.fee,
                "changing a plan must not retroactively rewrite a confirmed fee",
            )

        # A fresh process/service graph reads both facts back from SQLite.
        with open_app_database(self.db_path) as database:
            trade_service, trade_api, plans, plan_api = self._apis(database)
            persisted = trade_service.list_all_trades()
            self.assertEqual(len(persisted), 1)
            self.assertEqual(float(persisted[0].trade.fee), confirmed_fee)
            self.assertEqual(
                plans.get_plan("shenwan-hongyuan").a_share_min_commission,
                changed_plan.a_share_min_commission,
            )
            deleted = trade_api.dispatch(
                "delete_trade",
                _request(
                    "delete_trade",
                    {"trade_id": persisted[0].trade_id, "trade_scope": "real"},
                    "trade-delete",
                ),
            )
            self.assertTrue(deleted["accepted"])

        # Deletion is a hard delete: restarting cannot resurrect the row.
        with open_app_database(self.db_path) as database:
            trade_service, *_ = self._apis(database)
            self.assertEqual(trade_service.list_all_trades(), ())

    def test_failed_write_keeps_truth_and_retry_recovers(self) -> None:
        with open_app_database(self.db_path) as database:
            trade_service, trade_api, *_ = self._apis(database)
            database.connection.execute(
                """
                CREATE TRIGGER reject_trade_create
                BEFORE INSERT ON trades
                BEGIN
                    SELECT RAISE(ABORT, 'injected failure');
                END
                """
            )
            database.connection.commit()
            draft = {
                "trade_scope": "real",
                "symbol": "sh.600584",
                "side": "buy",
                "executed_at": "2026-07-24 10:03:00",
                "price": 38.25,
                "quantity": 200,
                "fee": 5.01,
                "note": "",
                "fee_plan_id": "shenwan-hongyuan",
            }
            failed = trade_api.dispatch(
                "create_trade",
                _request("create_trade", {"trade": draft}, "failed-create"),
            )
            self.assertFalse(failed["accepted"])
            self.assertEqual(failed["error"]["error_code"], "trade_persist_failed")
            self.assertTrue(failed["error"]["retryable"])
            self.assertEqual(trade_api.trade_revision, 0)
            self.assertEqual(trade_service.list_all_trades(), ())

            database.connection.execute("DROP TRIGGER reject_trade_create")
            database.connection.commit()
            retried = trade_api.dispatch(
                "create_trade",
                _request("create_trade", {"trade": draft}, "retry-create"),
            )
            self.assertTrue(retried["accepted"])
            self.assertEqual(trade_api.trade_revision, 1)
            self.assertEqual(len(trade_service.list_all_trades()), 1)


if __name__ == "__main__":
    unittest.main()
