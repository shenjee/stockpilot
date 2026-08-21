"""Replay v1.0 command facade contract tests."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Thread
import unittest

from packages.t0assistant.replay import (
    DEFAULT_ERROR_DELIVERY,
    REPLAY_COMMANDS,
    ReplayAccepted,
    ReplayApiError,
    ReplayCommandApi,
    ReplayDeliveryChannel,
    map_computation_outcome_to_replay_error,
    map_replay_prepare_error_to_replay_error,
)
from packages.t0assistant.runtime.computation_contract import (
    CancelReason,
    ComputationOutcome,
    ComputationStatus,
)
from packages.t0assistant.runtime.replay_data import (
    ReplayDataInvalidError,
    ReplayDataTimeoutError,
    ReplayDataUnavailableError,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_REPLAY_FIXTURE = json.loads(
    (
        _REPOSITORY_ROOT
        / "apps/t0-assistant/contracts/fixtures/replay-speed-v1.json"
    ).read_text(encoding="utf-8")
)


def _prepared_operation(session_id: str, operation_id: str) -> ReplayAccepted:
    return ReplayAccepted(
        session_id,
        operation_id,
        start_operation=lambda: None,
    )


class _FakePort:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.results: dict[str, ReplayAccepted] = {
            "select_symbol": ReplayAccepted(
                data={
                    "security": {
                        "symbol": "sh.600000",
                        "code": "600000",
                        "market": "sh",
                        "name": "浦发银行",
                        "instrument_type": "stock",
                    }
                }
            ),
            "begin_replay": _prepared_operation("replay-1", "operation-begin"),
            "set_replay_playback": ReplayAccepted("replay-1"),
            "set_replay_speed": ReplayAccepted("replay-1"),
            "step_replay": _prepared_operation("replay-1", "operation-step"),
            "seek_replay": _prepared_operation("replay-1", "operation-seek"),
            "end_replay": ReplayAccepted("replay-1", commit=lambda: None),
            "get_replay_snapshot": ReplayAccepted(
                "replay-1",
                data={"snapshot": deepcopy(_REPLAY_FIXTURE["snapshot"])},
            ),
        }
        self.error: ReplayApiError | Exception | None = None

    def execute(self, command, request):
        self.requests.append((command, dict(request)))
        if self.error is not None:
            raise self.error
        return self.results[command]


def _requests() -> dict[str, dict[str, object]]:
    common = {"schema_version": "t0_replay_v2", "request_id": "request-1"}
    session = {**common, "session_id": "replay-1"}
    return {
        "select_symbol": {**common, "symbol": "sh.600000"},
        "begin_replay": {
            **common,
            "symbol": "sh.600000",
            "trade_date": "2026-07-01",
        },
        "set_replay_playback": {**session, "playing": True},
        "set_replay_speed": {**session, "playback_speed": 5},
        "step_replay": session,
        "seek_replay": {
            **session,
            "target_time": "2026-07-01 10:23:00",
        },
        "end_replay": session,
        "get_replay_snapshot": session,
    }


class ReplayCommandApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.port = _FakePort()
        self.events: list[dict[str, object]] = []
        self.api = ReplayCommandApi(
            self.port,
            service_generation=7,
            publish_event=self.events.append,
        )

    def test_all_frozen_commands_validate_and_dispatch(self) -> None:
        requests = _requests()
        self.assertEqual(set(requests), set(REPLAY_COMMANDS))

        results = {
            command: self.api.dispatch(command, request)
            for command, request in requests.items()
        }

        self.assertTrue(all(result.status == 200 for result in results.values()))
        self.assertEqual(
            results["select_symbol"].payload["security"]["symbol"], "sh.600000"
        )
        self.assertEqual(
            results["begin_replay"].payload["operation_id"], "operation-begin"
        )
        self.assertNotIn(
            "operation_id", results["set_replay_speed"].payload
        )
        self.assertEqual(
            results["get_replay_snapshot"].payload["snapshot"]["session"]["revision"],
            8,
        )
        self.assertTrue(
            all(
                result.payload["service_generation"] == 7
                for result in results.values()
            )
        )

    def test_protocol_validation_rejects_without_calling_port_or_operation(self) -> None:
        invalid_requests = [
            ("set_replay_speed", {**_requests()["set_replay_speed"], "playback_speed": 3}),
            ("set_replay_playback", {**_requests()["set_replay_playback"], "playing": 1}),
            ("seek_replay", {**_requests()["seek_replay"], "target_time": "10:23"}),
            ("begin_replay", {**_requests()["begin_replay"], "trade_date": "2026-2-1"}),
            ("step_replay", {**_requests()["step_replay"], "unexpected": True}),
            ("step_replay", {**_requests()["step_replay"], "schema_version": "v0.9"}),
        ]

        for command, request in invalid_requests:
            with self.subTest(command=command, request=request):
                result = self.api.dispatch(command, request)
                self.assertEqual(result.status, 400)
                self.assertEqual(result.payload["error_code"], "invalid_request")
                self.assertNotIn("operation_id", result.payload)
        self.assertEqual(self.port.requests, [])
        self.assertEqual(self.events, [])

    def test_domain_rejection_uses_stable_mapping_and_is_sync_only(self) -> None:
        self.port.error = ReplayApiError(
            "session_retired", details={"session_id": "replay-1"}
        )

        result = self.api.dispatch("step_replay", _requests()["step_replay"])

        self.assertEqual(result.status, 409)
        self.assertEqual(result.payload["category"], "session")
        self.assertFalse(result.payload["retryable"])
        self.assertEqual(result.payload["affected_capability"], "replay")
        self.assertEqual(result.payload["details"], {"session_id": "replay-1"})
        self.assertNotIn("operation_id", result.payload)
        self.assertEqual(self.events, [])

    def test_accepted_operation_failure_is_published_exactly_once(self) -> None:
        accepted = self.api.dispatch("seek_replay", _requests()["seek_replay"])
        self.assertEqual(accepted.payload["operation_id"], "operation-seek")
        self.assertTrue(accepted.response_delivered())

        first = self.api.deliver_operation_failure(
            operation_id="operation-seek",
            session_id="replay-1",
            revision=9,
            error=ReplayApiError("calculation_failed"),
        )
        duplicate = self.api.deliver_operation_failure(
            operation_id="operation-seek",
            session_id="replay-1",
            revision=10,
            error=ReplayApiError("calculation_failed"),
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        self.assertEqual(event["event_type"], "operation_failed")
        self.assertEqual(event["operation_id"], "operation-seek")
        self.assertEqual(event["payload"]["request_id"], "request-1")
        self.assertEqual(event["payload"]["operation_id"], "operation-seek")

    def test_prepared_operation_cannot_fail_before_response_delivery(self) -> None:
        def start_operation() -> None:
            self.api.deliver_operation_failure(
                operation_id="operation-seek",
                session_id="replay-1",
                revision=9,
                error=ReplayApiError("calculation_failed"),
            )

        self.port.results["seek_replay"] = ReplayAccepted(
            "replay-1",
            "operation-seek",
            start_operation=start_operation,
        )

        result = self.api.dispatch("seek_replay", _requests()["seek_replay"])

        self.assertEqual(self.events, [])
        self.assertTrue(result.response_delivered())
        self.assertEqual(len(self.events), 1)
        self.assertFalse(result.response_delivered())

    def test_completed_or_mismatched_operation_cannot_publish_failure(self) -> None:
        accepted = self.api.dispatch("step_replay", _requests()["step_replay"])
        accepted.response_delivered()
        self.assertFalse(
            self.api.deliver_operation_failure(
                operation_id="operation-step",
                session_id="another-session",
                revision=1,
                error=ReplayApiError("operation_superseded"),
            )
        )
        self.assertTrue(self.api.complete_operation("operation-step"))
        self.assertFalse(
            self.api.deliver_operation_failure(
                operation_id="operation-step",
                session_id="replay-1",
                revision=2,
                error=ReplayApiError("operation_superseded"),
            )
        )
        self.assertEqual(self.events, [])

    def test_end_replay_retires_all_session_operations_and_blocks_late_failure(self) -> None:
        self.api.dispatch(
            "step_replay", _requests()["step_replay"]
        ).response_delivered()
        self.api.dispatch(
            "seek_replay", _requests()["seek_replay"]
        ).response_delivered()

        ended = self.api.dispatch("end_replay", _requests()["end_replay"])

        self.assertEqual(ended.status, 200)
        for operation_id in ("operation-step", "operation-seek"):
            self.assertFalse(
                self.api.deliver_operation_failure(
                    operation_id=operation_id,
                    session_id="replay-1",
                    revision=10,
                    error=ReplayApiError("calculation_failed"),
                )
            )
        self.assertEqual(self.events, [])

    def test_end_replay_commit_and_operation_retirement_share_one_lock_boundary(self) -> None:
        self.api.dispatch(
            "step_replay", _requests()["step_replay"]
        ).response_delivered()
        attempted = Event()
        finished = Event()
        worker_result: list[bool] = []

        def report_late_failure() -> None:
            attempted.set()
            worker_result.append(
                self.api.deliver_operation_failure(
                    operation_id="operation-step",
                    session_id="replay-1",
                    revision=10,
                    error=ReplayApiError("calculation_failed"),
                )
            )
            finished.set()

        def commit() -> None:
            Thread(target=report_late_failure, daemon=True).start()
            self.assertTrue(attempted.wait(1))

        self.port.results["end_replay"] = ReplayAccepted(
            "replay-1",
            commit=commit,
        )

        ended = self.api.dispatch("end_replay", _requests()["end_replay"])

        self.assertEqual(ended.status, 200)
        self.assertTrue(finished.wait(1))
        self.assertEqual(worker_result, [False])
        self.assertEqual(self.events, [])

    def test_explicit_session_retirement_only_clears_matching_operations(self) -> None:
        self.api.dispatch(
            "step_replay", _requests()["step_replay"]
        ).response_delivered()
        self.port.results["seek_replay"] = _prepared_operation(
            "replay-2", "operation-other"
        )
        other_request = {
            **_requests()["seek_replay"],
            "session_id": "replay-2",
        }
        self.api.dispatch("seek_replay", other_request).response_delivered()

        self.assertEqual(self.api.retire_session("replay-1"), 1)
        self.assertFalse(self.api.complete_operation("operation-step"))
        self.assertTrue(self.api.complete_operation("operation-other"))

    def test_step_end_of_series_can_be_successful_without_operation(self) -> None:
        self.port.results["step_replay"] = ReplayAccepted("replay-1")

        result = self.api.dispatch("step_replay", _requests()["step_replay"])

        self.assertEqual(result.status, 200)
        self.assertNotIn("operation_id", result.payload)

    def test_unexpected_port_failure_is_sanitized(self) -> None:
        self.port.error = RuntimeError("/private/path and raw provider response")

        result = self.api.dispatch("begin_replay", _requests()["begin_replay"])

        self.assertEqual(result.status, 503)
        self.assertEqual(result.payload["error_code"], "service_unavailable")
        self.assertNotIn("private", str(result.payload))

    def test_port_cannot_replace_protocol_identity_or_reuse_operation_id(self) -> None:
        self.port.results["set_replay_speed"] = ReplayAccepted(
            "replay-1", data={"request_id": "forged"}
        )
        forged = self.api.dispatch(
            "set_replay_speed", _requests()["set_replay_speed"]
        )
        self.assertEqual(forged.status, 503)
        self.assertEqual(forged.payload["request_id"], "request-1")

        first = self.api.dispatch("seek_replay", _requests()["seek_replay"])
        second = self.api.dispatch("seek_replay", _requests()["seek_replay"])
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 503)
        self.assertEqual(second.payload["error_code"], "service_unavailable")

    def test_port_success_shape_and_session_identity_are_enforced(self) -> None:
        self.port.results["get_replay_snapshot"] = ReplayAccepted("replay-1")
        missing_snapshot = self.api.dispatch(
            "get_replay_snapshot", _requests()["get_replay_snapshot"]
        )
        self.assertEqual(missing_snapshot.status, 503)

        invalid_snapshot = deepcopy(_REPLAY_FIXTURE["snapshot"])
        del invalid_snapshot["market"]["bars_5m"]
        self.port.results["get_replay_snapshot"] = ReplayAccepted(
            "replay-1", data={"snapshot": invalid_snapshot}
        )
        invalid_nested_shape = self.api.dispatch(
            "get_replay_snapshot", _requests()["get_replay_snapshot"]
        )
        self.assertEqual(invalid_nested_shape.status, 503)

        self.port.results["end_replay"] = ReplayAccepted("replay-other")
        mismatched_session = self.api.dispatch(
            "end_replay", _requests()["end_replay"]
        )
        self.assertEqual(mismatched_session.status, 503)

    def test_default_error_delivery_table_is_complete_and_frozen(self) -> None:
        self.assertEqual(
            DEFAULT_ERROR_DELIVERY,
            {
                "invalid_request": ReplayDeliveryChannel.SYNCHRONOUS,
                "symbol_not_found": ReplayDeliveryChannel.SYNCHRONOUS,
                "invalid_trade_date": ReplayDeliveryChannel.SYNCHRONOUS,
                "replay_price_data_unavailable": ReplayDeliveryChannel.ASYNCHRONOUS,
                "replay_data_invalid": ReplayDeliveryChannel.ASYNCHRONOUS,
                "session_not_found": ReplayDeliveryChannel.SYNCHRONOUS,
                "session_retired": ReplayDeliveryChannel.SYNCHRONOUS,
                "invalid_replay_state": ReplayDeliveryChannel.SYNCHRONOUS,
                "operation_superseded": ReplayDeliveryChannel.ASYNCHRONOUS,
                "replay_busy": ReplayDeliveryChannel.SYNCHRONOUS,
                "calculation_failed": ReplayDeliveryChannel.ASYNCHRONOUS,
                "service_unavailable": ReplayDeliveryChannel.SYNCHRONOUS,
            },
        )

    def test_every_error_has_an_explicit_synchronous_http_mapping(self) -> None:
        expected = {
            "invalid_request": 400,
            "symbol_not_found": 404,
            "invalid_trade_date": 400,
            "replay_price_data_unavailable": 503,
            "replay_data_invalid": 422,
            "session_not_found": 404,
            "session_retired": 409,
            "invalid_replay_state": 409,
            "operation_superseded": 409,
            "replay_busy": 409,
            "calculation_failed": 500,
            "service_unavailable": 503,
        }
        for error_code, status in expected.items():
            with self.subTest(error_code=error_code):
                self.port.error = ReplayApiError(error_code)
                result = self.api.dispatch(
                    "set_replay_speed", _requests()["set_replay_speed"]
                )
                self.assertEqual(result.status, status)

    def test_computation_outcome_to_replay_error_mapping_is_frozen(self) -> None:
        cases = [
            (
                "failed",
                ComputationOutcome("t1", ComputationStatus.FAILED),
                "calculation_failed",
                ReplayDeliveryChannel.ASYNCHRONOUS,
            ),
            (
                "superseded",
                ComputationOutcome(
                    "t2",
                    ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.SUPERSEDED,
                ),
                "operation_superseded",
                ReplayDeliveryChannel.ASYNCHRONOUS,
            ),
            (
                "deadline_exceeded",
                ComputationOutcome(
                    "t3",
                    ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.DEADLINE_EXCEEDED,
                ),
                "calculation_failed",
                ReplayDeliveryChannel.ASYNCHRONOUS,
            ),
            (
                "executor_closed",
                ComputationOutcome(
                    "t4",
                    ComputationStatus.CANCELLED,
                    cancel_reason=CancelReason.EXECUTOR_CLOSED,
                ),
                "service_unavailable",
                ReplayDeliveryChannel.ASYNCHRONOUS,
            ),
        ]
        for name, outcome, error_code, channel in cases:
            with self.subTest(name=name):
                error, actual_channel = map_computation_outcome_to_replay_error(
                    outcome
                )
                self.assertIsNotNone(error)
                self.assertEqual(error.error_code, error_code)
                self.assertEqual(actual_channel, channel)

    def test_computation_outcome_drop_cases_are_frozen(self) -> None:
        for reason in (CancelReason.CANCELLED, CancelReason.SESSION_INVALID):
            with self.subTest(reason=reason):
                error, channel = map_computation_outcome_to_replay_error(
                    ComputationOutcome(
                        "t-drop",
                        ComputationStatus.CANCELLED,
                        cancel_reason=reason,
                    )
                )
                self.assertIsNone(error)
                self.assertIsNone(channel)

    def test_replay_prepare_error_to_replay_error_mapping_is_frozen(self) -> None:
        unavailable_error, unavailable_channel = (
            map_replay_prepare_error_to_replay_error(
                ReplayDataUnavailableError("no bars")
            )
        )
        self.assertEqual(
            unavailable_error.error_code, "replay_price_data_unavailable"
        )
        self.assertEqual(unavailable_channel, ReplayDeliveryChannel.ASYNCHRONOUS)

        invalid_error, invalid_channel = map_replay_prepare_error_to_replay_error(
            ReplayDataInvalidError(
                "bad ohlc",
                details={"timeframe": "1m", "reason": "high below close"},
            )
        )
        self.assertEqual(invalid_error.error_code, "replay_data_invalid")
        self.assertEqual(invalid_error.details["timeframe"], "1m")
        self.assertEqual(invalid_channel, ReplayDeliveryChannel.ASYNCHRONOUS)

        timeout_error, timeout_channel = map_replay_prepare_error_to_replay_error(
            ReplayDataTimeoutError("deadline")
        )
        self.assertEqual(timeout_error.error_code, "replay_price_data_unavailable")
        self.assertEqual(timeout_channel, ReplayDeliveryChannel.ASYNCHRONOUS)

        unknown_error, unknown_channel = map_replay_prepare_error_to_replay_error(
            RuntimeError("boom")
        )
        self.assertEqual(unknown_error.error_code, "replay_price_data_unavailable")
        self.assertEqual(unknown_error.details, {"prepare_failed": True})
        self.assertEqual(unknown_channel, ReplayDeliveryChannel.ASYNCHRONOUS)


if __name__ == "__main__":
    unittest.main()
