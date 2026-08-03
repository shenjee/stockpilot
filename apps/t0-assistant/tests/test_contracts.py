from __future__ import annotations

import json
import unittest
from pathlib import Path

from datetime import date, datetime

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.t0assistant.runtime import (
    PipelineResult,
    SessionProjectionInput,
    build_workbench_projection,
)


APP_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = APP_ROOT / "contracts"


def load_json(name: str):
    with (CONTRACTS / name).open(encoding="utf-8") as stream:
        return json.load(stream)


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.logical = load_json("logical-schema.json")
        cls.app = load_json("app-v1.schema.json")
        cls.replay = load_json("replay-v1.schema.json")
        cls.fixture = load_json("fixtures/replay-speed-v1.json")
        cls.workbench_flow = load_json("fixtures/workbench-flow-v1.json")
        cls.list_trades_flow = load_json("fixtures/list-trades-flow-v1.json")
        cls.historical_snapshot_flow = load_json(
            "fixtures/historical-snapshot-flow-v1.json"
        )
        cls.registry = Registry().with_resources(
            [
                (cls.logical["$id"], Resource.from_contents(cls.logical)),
                (cls.app["$id"], Resource.from_contents(cls.app)),
                (cls.replay["$id"], Resource.from_contents(cls.replay)),
            ]
        )

    def validator(self, definition: str) -> Draft202012Validator:
        schema = {"$ref": f"{self.replay['$id']}#/$defs/{definition}"}
        return Draft202012Validator(schema, registry=self.registry)

    def app_validator(self, definition: str) -> Draft202012Validator:
        schema = {"$ref": f"{self.app['$id']}#/$defs/{definition}"}
        return Draft202012Validator(schema, registry=self.registry)

    def logical_validator(self, definition: str) -> Draft202012Validator:
        schema = {"$ref": f"{self.logical['$id']}#/$defs/{definition}"}
        return Draft202012Validator(schema, registry=self.registry)

    def test_logical_schema_is_not_storage_schema(self) -> None:
        self.assertIn("not a SQLite schema", self.logical["description"])
        expected = {"security", "bar", "quote", "indicators", "chan_analysis", "session", "warning", "workbench_snapshot"}
        self.assertTrue(expected.issubset(self.logical["$defs"]))

    def test_all_contract_documents_are_valid_draft_2020_12_schemas(self) -> None:
        for schema in (self.logical, self.app, self.replay):
            Draft202012Validator.check_schema(schema)

    def test_all_four_speed_commands_validate(self) -> None:
        validator = self.validator("set_replay_speed_request")
        self.assertEqual(self.fixture["valid_speeds"], [1, 2, 5, 10])
        for request in self.fixture["set_speed_requests"]:
            validator.validate(request)

    def test_invalid_speed_is_rejected(self) -> None:
        invalid = dict(self.fixture["set_speed_requests"][0], playback_speed=3)
        errors = list(self.validator("set_replay_speed_request").iter_errors(invalid))
        self.assertTrue(errors)

    def test_speed_event_and_snapshot_validate(self) -> None:
        self.validator("event_envelope").validate(self.fixture["changed_event"])
        self.validator("workbench_snapshot").validate(self.fixture["snapshot"])
        self.assertNotIn("operation_id", self.fixture["changed_event"])
        self.assertEqual(self.fixture["changed_event"]["revision"], self.fixture["snapshot"]["session"]["revision"])

    def test_app_contract_references_replay_v1_without_redefining_commands(self) -> None:
        refs = {
            self.app["$defs"][name]["$ref"]
            for name in ("replay_set_speed_request", "replay_event_envelope", "replay_workbench_snapshot")
        }
        self.assertTrue(all("t0-replay-v1.schema.json" in ref for ref in refs))
        commands = self.app["$defs"]["command_request"]["properties"]["command"]["enum"]
        self.assertNotIn("set_replay_speed", commands)

    def test_live_trade_and_preference_commands_validate(self) -> None:
        requests = [
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-search",
                "command": "search_securities",
                "session_id": None,
                "payload": {"query": "gzmt", "limit": 20},
            },
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-select",
                "command": "select_security",
                "session_id": None,
                "payload": {"symbol": "sh.600519"},
            },
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-trade",
                "command": "create_trade",
                "session_id": "live-1",
                "payload": {
                    "trade": {
                        "trade_scope": "real",
                        "symbol": "sh.600519",
                        "side": "buy",
                        "executed_at": "2026-07-22 10:01:00",
                        "price": 1500.0,
                        "quantity": 200,
                        "fee": None,
                        "note": "",
                        "fee_plan_id": None,
                    }
                },
            },
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-prefs",
                "command": "save_preferences",
                "session_id": None,
                "payload": {
                    "preferences": {
                        "last_symbol": "sh.600519",
                        "layout": {"chart_split": "64_36", "show_intraday": True},
                        "layers": {
                            "ma5": False,
                            "ma10": False,
                            "ma20": False,
                            "ma30": False,
                            "ma60": False,
                            "strokes": True,
                            "pivot_zones": True,
                        },
                    }
                },
            },
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-hist",
                "command": "get_historical_snapshot",
                "session_id": None,
                "payload": {"symbol": "sh.600519", "trade_date": "2026-07-22"},
            },
        ]
        validator = self.app_validator("command_request")
        for request in requests:
            validator.validate(request)

    def test_historical_command_response_validates(self) -> None:
        """A successful get_historical_snapshot response carries a workbench_snapshot."""
        result = PipelineResult(
            target_time=datetime(2026, 7, 22, 10, 0, 0),
            symbol="sh.600519",
            trade_date=date(2026, 7, 22),
            bars_1m=tuple(),
            bars_5m=tuple(),
            closed_5m_prefix=tuple(),
            daily_bars=tuple(),
            daily_bar=None,
            quote=None,
            indicators_1m={
                "vwap": [],
                "volume": {"values": []},
                "macd": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "dif": [],
                    "dea": [],
                    "histogram": [],
                },
            },
            indicators_5m={
                "ma": {f"ma{period}": [] for period in (5, 10, 20, 30, 60)},
                "boll": {
                    "period": 20,
                    "stddev": 2.0,
                    "upper": [],
                    "middle": [],
                    "lower": [],
                },
                "volume": {"values": [], "ma5": [], "ma10": []},
                "macd": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "dif": [],
                    "dea": [],
                    "histogram": [],
                },
            },
            chan_analysis={
                "symbol": "sh.600519",
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
            },
            warnings=[],
        )
        session = SessionProjectionInput(
            session_id="historical:sh.600519:2026-07-22",
            session_type="historical",
            symbol="sh.600519",
            trade_date="2026-07-22",
            state="ready",
            revision=0,
        )
        snapshot = build_workbench_projection(result, session).to_dict()
        response = {
            "schema_version": "t0_app_v1",
            "request_id": "req-hist-resp",
            "accepted": True,
            "operation_id": None,
            "data": snapshot,
            "error": None,
        }
        validator = self.app_validator("command_response")
        validator.validate(response)
        self.assertEqual(snapshot["session"]["session_type"], "historical")
        self.assertEqual(snapshot["session"]["trade_date"], "2026-07-22")
        self.assertEqual(snapshot["replay"], None)

    def test_app_events_enforce_generation_session_and_revision(self) -> None:
        event = {
            "schema_version": "t0_app_v1",
            "service_generation": 2,
            "session_id": "live-1",
            "revision": 4,
            "event_type": "market_update",
            "payload": {
                "target": "quote",
                "bars": [],
                "quote": {
                    "timestamp": "2026-07-22 10:15:03",
                    "latest_price": 1500.0,
                    "change_percent": 0.1,
                    "open": 1498.0,
                    "high": 1501.0,
                    "low": 1497.0,
                    "previous_close": 1499.0,
                    "volume": 100,
                    "amount": 150000.0,
                    "volume_ratio": None,
                    "order_imbalance": None,
                    "turnover_rate": None,
                },
            },
        }
        validator = self.app_validator("event_envelope")
        validator.validate(event)
        errors = list(validator.iter_errors({**event, "revision": -1}))
        self.assertTrue(errors)

    def test_live_market_view_updated_event_matches_contract(self) -> None:
        event = {
            "schema_version": "t0_app_v1",
            "service_generation": 2,
            "session_id": "live-1",
            "revision": 5,
            "event_type": "live_market_view_updated",
            "payload": {
                "effective_trade_date": "2026-07-24",
                "calendar_status": "available",
                "market_phase": "morning",
                "symbol_availability": "available",
                "data_quality": "partial",
                "polling_profile": "active",
                "quote_as_of": "2026-07-24 09:31:03",
                "bars_1m_as_of": "2026-07-24 09:31:00",
                "bars_5m_as_of": None,
                "daily_as_of": None,
                "one_minute_indicators_as_of": "2026-07-24 09:31:00",
                "five_minute_indicators_as_of": None,
                "czsc_as_of": None,
            },
        }
        self.app_validator("event_envelope").validate(event)

    def test_synchronous_rejection_cannot_claim_an_operation(self) -> None:
        response = {
            "schema_version": "t0_app_v1",
            "request_id": "req-bad",
            "accepted": False,
            "operation_id": "must-not-exist",
            "data": None,
            "error": {
                "error_code": "invalid_request",
                "category": "validation",
                "severity": "error",
                "retryable": False,
                "affected_capability": "trades",
                "message": "invalid trade",
                "request_id": "req-bad",
                "details": {},
            },
        }
        self.assertTrue(list(self.app_validator("command_response").iter_errors(response)))

    def test_accepted_response_distinguishes_sync_and_async_completion(self) -> None:
        validator = self.app_validator("command_response")
        validator.validate(
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-sync",
                "accepted": True,
                "operation_id": None,
                "data": None,
                "error": None,
            }
        )
        validator.validate(
            {
                "schema_version": "t0_app_v1",
                "request_id": "req-async",
                "accepted": True,
                "operation_id": "operation-1",
                "data": None,
                "error": None,
            }
        )

    def test_trade_event_uses_shared_record_shape_and_explicit_scope(self) -> None:
        event = {
            "schema_version": "t0_app_v1",
            "service_generation": 2,
            "session_id": None,
            "revision": 5,
            "event_type": "trades_changed",
            "payload": {
                "trade_revision": 1,
                "trades": [
                    {
                        "trade_id": "trade-1",
                        "bucket_start": "2026-07-22 10:00:00",
                        "trade_scope": "real",
                        "symbol": "sh.600519",
                        "side": "buy",
                        "executed_at": "2026-07-22 10:01:00",
                        "price": 1500.0,
                        "quantity": 200,
                        "fee": None,
                        "note": "",
                        "fee_plan_id": None,
                    }
                ],
            },
        }
        self.app_validator("event_envelope").validate(event)

        simulated = {
            **event,
            "session_id": "replay-1",
            "payload": {
                **event["payload"],
                "trades": [
                    {**event["payload"]["trades"][0], "trade_scope": "simulated"}
                ],
            },
        }
        self.app_validator("event_envelope").validate(simulated)

        invalid_real_scope = {
            **event,
            "session_id": "live-1",
        }
        self.assertTrue(
            list(self.app_validator("event_envelope").iter_errors(invalid_real_scope))
        )

    def test_python_consumes_complete_incremental_out_of_order_and_error_fixture(self) -> None:
        flow = self.workbench_flow
        event_validator = self.app_validator("event_envelope")
        response_validator = self.app_validator("command_response")

        event_validator.validate(flow["initial_snapshot_event"])
        for event in flow["incremental_events"]:
            event_validator.validate(event)
        event_validator.validate(flow["operation_failed_event"])
        response_validator.validate(flow["synchronous_error_response"])

        delivered = [
            flow["incremental_events"][index]["revision"]
            for index in flow["out_of_order_delivery"]
        ]
        self.assertEqual(delivered, [2, 4, 3])
        self.assertEqual(
            flow["initial_snapshot_event"]["payload"]["session"]["revision"],
            flow["initial_snapshot_event"]["revision"],
        )
        self.assertEqual(
            flow["operation_failed_event"]["operation_id"],
            flow["operation_failed_event"]["payload"]["operation_id"],
        )

    def test_workbench_projection_output_validates_against_logical_schema(self) -> None:
        """A fake PipelineResult produces a snapshot that satisfies the frozen contract."""
        result = PipelineResult(
            target_time=datetime(2026, 7, 22, 10, 0, 0),
            symbol="sh.600000",
            trade_date=date(2026, 7, 22),
            bars_1m=tuple(),
            bars_5m=tuple(),
            closed_5m_prefix=tuple(),
            daily_bars=tuple(),
            daily_bar=None,
            quote=None,
            indicators_1m={
                "vwap": [],
                "volume": {"values": []},
                "macd": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "dif": [],
                    "dea": [],
                    "histogram": [],
                },
            },
            indicators_5m={
                "ma": {f"ma{period}": [] for period in (5, 10, 20, 30, 60)},
                "boll": {
                    "period": 20,
                    "stddev": 2.0,
                    "upper": [],
                    "middle": [],
                    "lower": [],
                },
                "volume": {"values": [], "ma5": [], "ma10": []},
                "macd": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "dif": [],
                    "dea": [],
                    "histogram": [],
                },
            },
            chan_analysis={
                "symbol": "sh.600000",
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
            },
            warnings=[],
        )
        session = SessionProjectionInput(
            session_id="live-contract-1",
            session_type="live",
            symbol="sh.600000",
            trade_date=None,
            state="ready",
            revision=1,
        )
        snapshot = build_workbench_projection(result, session).to_dict()

        self.logical_validator("workbench_snapshot").validate(snapshot)
        self.assertEqual(snapshot["session"]["trade_date"], None)
        self.assertEqual(snapshot["replay"], None)
        self.assertNotIn("closed_5m_prefix", snapshot)
        self.assertNotIn("daily_bar", snapshot)


    def test_list_trades_is_fact_via_changed_event(self) -> None:
        """list_trades -> trades_changed cause/effect (T0-041 contract).

        list_trades is a fact-via-changed-event command: the accepted response
        carries operation_id:null and data:null (the renderer must not consume
        command_response.data.trades). After an accepted list_trades the backend
        must publish one authoritative real trades_changed event (session_id
        null), including when the repository is empty. The event's trades are a
        complete repository snapshot, not a query-scoped subset.
        """
        flow = self.list_trades_flow
        request_validator = self.app_validator("command_request")
        response_validator = self.app_validator("command_response")
        event_validator = self.app_validator("event_envelope")

        for scenario_name in ("existing_trades", "empty_repository"):
            scenario = flow[scenario_name]
            request_validator.validate(scenario["list_trades_request"])
            response = scenario["list_trades_response"]
            response_validator.validate(response)

            # The renderer must not consume the sync response data: the accepted
            # list_trades response carries operation_id:null and data:null.
            self.assertTrue(response["accepted"])
            self.assertIsNone(response["operation_id"])
            self.assertIsNone(response["data"])

            event = scenario["trades_changed_event"]
            event_validator.validate(event)
            # The event is the sole authoritative source for the trade list.
            self.assertEqual(event["event_type"], "trades_changed")
            self.assertIsNone(event["session_id"])
            self.assertIn("trade_revision", event["payload"])

        # The existing-trades event is a COMPLETE REPOSITORY SNAPSHOT: it
        # contains trades for multiple symbols and trading dates, not just the
        # symbol/date the list_trades request asked for.
        existing_event = flow["existing_trades"]["trades_changed_event"]
        snapshot_trades = existing_event["payload"]["trades"]
        self.assertGreater(len(snapshot_trades), 1)
        symbols = {trade["symbol"] for trade in snapshot_trades}
        dates = {trade["executed_at"][:10] for trade in snapshot_trades}
        self.assertGreater(len(symbols), 1)
        self.assertGreater(len(dates), 1)

        # The empty repository still publishes a trades_changed event with an
        # empty trades array (the cause/effect holds even when there is nothing).
        empty_event = flow["empty_repository"]["trades_changed_event"]
        self.assertEqual(empty_event["payload"]["trades"], [])

        # The expected scope filter documents which snapshot trades a renderer
        # keeps for the list_trades request's symbol/date (it filters the full
        # snapshot itself; the event carries no scope fields).
        scope = flow["existing_trades"]["expected_scope_filter"]
        matched = [
            trade["trade_id"]
            for trade in snapshot_trades
            if trade["symbol"] == scope["symbol"]
            and trade["executed_at"][:10] == scope["trade_date"]
        ]
        self.assertEqual(matched, scope["matched_trade_ids"])

    def test_historical_snapshot_command_returns_static_workbench_snapshot(self) -> None:
        """get_historical_snapshot is a synchronous command that returns a static
        workbench_snapshot with session_type 'historical' and replay null.
        """
        flow = self.historical_snapshot_flow
        request_validator = self.app_validator("command_request")
        response_validator = self.app_validator(
            "historical_snapshot_success_response"
        )

        request_validator.validate(flow["historical_snapshot_request"])
        response_validator.validate(flow["historical_snapshot_response"])

        response = flow["historical_snapshot_response"]
        self.assertTrue(response["accepted"])
        self.assertIsNone(response["operation_id"])
        self.assertIsNone(response["error"])

        snapshot = response["data"]
        self.assertEqual(snapshot["session"]["session_type"], "historical")
        self.assertEqual(snapshot["session"]["state"], "ready")
        self.assertEqual(
            snapshot["session"]["trade_date"],
            flow["historical_snapshot_request"]["payload"]["trade_date"],
        )
        self.assertIsNone(snapshot["replay"])

    def test_historical_success_schema_rejects_generic_or_non_static_data(self) -> None:
        response = self.historical_snapshot_flow["historical_snapshot_response"]
        validator = self.app_validator("historical_snapshot_success_response")

        invalid_responses = (
            {**response, "data": {}},
            {
                **response,
                "data": {
                    **response["data"],
                    "session": {
                        **response["data"]["session"],
                        "session_type": "live",
                    },
                },
            },
            {
                **response,
                "data": {
                    **response["data"],
                    "session": {
                        **response["data"]["session"],
                        "state": "loading",
                    },
                },
            },
            {**response, "data": {**response["data"], "replay": {}}},
            {**response, "operation_id": "operation-must-not-exist"},
        )
        for invalid in invalid_responses:
            with self.subTest(invalid=invalid):
                self.assertTrue(list(validator.iter_errors(invalid)))

    def test_historical_error_schema_freezes_both_failure_semantics(self) -> None:
        flow = self.historical_snapshot_flow
        validator = self.app_validator("historical_snapshot_error_response")

        for fixture_name in (
            "historical_data_unavailable_response",
            "service_unavailable_response",
        ):
            validator.validate(flow[fixture_name])

        unavailable = flow["historical_data_unavailable_response"]
        wrong_retryability = {
            **unavailable,
            "error": {**unavailable["error"], "retryable": False},
        }
        self.assertTrue(list(validator.iter_errors(wrong_retryability)))

        service = flow["service_unavailable_response"]
        wrong_capability = {
            **service,
            "error": {**service["error"], "affected_capability": "service"},
        }
        self.assertTrue(list(validator.iter_errors(wrong_capability)))


if __name__ == "__main__":
    unittest.main()
