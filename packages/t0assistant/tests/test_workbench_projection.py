"""Tests for the Workbench Projection builder.

All tests use deterministic fakes; no network, provider, SQLite or Electron is
accessed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from packages.t0assistant.runtime import (
    PipelineResult,
    ReplayProjectionInput,
    SessionProjectionInput,
    WorkbenchProjectionError,
    build_workbench_projection,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_DIR = _REPO_ROOT / "apps" / "t0-assistant" / "contracts"


def _load_logical_schema() -> dict[str, Any]:
    path = _CONTRACTS_DIR / "logical-schema.json"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _load_replay_schema() -> dict[str, Any]:
    path = _CONTRACTS_DIR / "replay-v1.schema.json"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _logical_validator(definition: str) -> Draft202012Validator:
    schema_doc = _load_logical_schema()
    registry = Registry().with_resource(
        schema_doc["$id"], Resource.from_contents(schema_doc)
    )
    schema = {"$ref": f"{schema_doc['$id']}#/$defs/{definition}"}
    return Draft202012Validator(schema, registry=registry)


def _replay_validator(definition: str) -> Draft202012Validator:
    logical = _load_logical_schema()
    replay = _load_replay_schema()
    registry = Registry().with_resources(
        [
            (logical["$id"], Resource.from_contents(logical)),
            (replay["$id"], Resource.from_contents(replay)),
        ]
    )
    schema = {"$ref": f"{replay['$id']}#/$defs/{definition}"}
    return Draft202012Validator(schema, registry=registry)


def _bar(
    timestamp: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    amount: float,
    *,
    closed: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "closed": closed,
    }


def _empty_5m_indicators() -> dict[str, Any]:
    return {
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
    }


def _empty_1m_indicators() -> dict[str, Any]:
    return {
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
    }


def _make_chan_analysis(symbol: str) -> dict[str, Any]:
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


def _make_pipeline_result(
    *,
    symbol: str = "sh.600000",
    trade_date: date = date(2026, 7, 22),
    target_time: datetime = datetime(2026, 7, 22, 10, 0, 0),
    bars_1m: list[dict[str, Any]] | None = None,
    bars_5m: list[dict[str, Any]] | None = None,
    closed_5m_prefix: list[dict[str, Any]] | None = None,
    daily_bars: list[dict[str, Any]] | None = None,
    daily_bar: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    indicators_1m: dict[str, Any] | None = None,
    indicators_5m: dict[str, Any] | None = None,
    chan_analysis: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> PipelineResult:
    return PipelineResult(
        target_time=target_time,
        symbol=symbol,
        trade_date=trade_date,
        bars_1m=tuple(bars_1m if bars_1m is not None else []),
        bars_5m=tuple(bars_5m if bars_5m is not None else []),
        closed_5m_prefix=tuple(closed_5m_prefix if closed_5m_prefix is not None else []),
        daily_bars=tuple(daily_bars if daily_bars is not None else []),
        daily_bar=daily_bar,
        quote=quote,
        indicators_1m=indicators_1m if indicators_1m is not None else _empty_1m_indicators(),
        indicators_5m=indicators_5m if indicators_5m is not None else _empty_5m_indicators(),
        chan_analysis=chan_analysis if chan_analysis is not None else _make_chan_analysis(symbol),
        warnings=list(warnings if warnings is not None else []),
    )


def _live_session(revision: int = 1, state: str = "ready") -> SessionProjectionInput:
    return SessionProjectionInput(
        session_id="live-1",
        session_type="live",
        symbol="sh.600000",
        trade_date=None,
        state=state,
        revision=revision,
    )


def _replay_session(
    revision: int = 1,
    state: str = "paused",
    trade_date: str = "2026-07-22",
) -> SessionProjectionInput:
    return SessionProjectionInput(
        session_id="replay-1",
        session_type="replay",
        symbol="sh.600000",
        trade_date=trade_date,
        state=state,
        revision=revision,
    )


def _replay_input(
    *,
    granularity: str = "one_minute",
    current_time: str = "2026-07-22 10:00:00",
    next_bar_time: str | None = "2026-07-22 10:01:00",
    start_time: str = "2026-07-22 09:30:00",
    end_time: str = "2026-07-22 15:00:00",
    playing: bool = False,
    playback_speed: int = 1,
    step_seconds: int = 60,
) -> ReplayProjectionInput:
    return ReplayProjectionInput(
        granularity=granularity,
        current_time=current_time,
        next_bar_time=next_bar_time,
        start_time=start_time,
        end_time=end_time,
        playing=playing,
        playback_speed=playback_speed,
        step_seconds=step_seconds,
    )


class LiveSnapshotTests(unittest.TestCase):
    def test_live_snapshot_built_from_pipeline_result(self) -> None:
        result = _make_pipeline_result()
        session = _live_session()
        projection = build_workbench_projection(result, session)
        snapshot = projection.to_dict()

        self.assertEqual(snapshot["timezone"], "Asia/Shanghai")
        self.assertEqual(snapshot["session"]["session_type"], "live")
        self.assertEqual(snapshot["session"]["trade_date"], None)
        self.assertEqual(snapshot["replay"], None)
        self.assertIn("market", snapshot)
        self.assertIn("indicators", snapshot)
        self.assertIn("chan_analysis", snapshot)
        self.assertIn("warnings", snapshot)

    def test_output_validates_against_logical_schema(self) -> None:
        result = _make_pipeline_result()
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        validator = _logical_validator("workbench_snapshot")
        validator.validate(snapshot)

    def test_snapshot_shape_matches_fixture(self) -> None:
        result = _make_pipeline_result()
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        fixture_path = _CONTRACTS_DIR / "fixtures" / "workbench-flow-v1.json"
        with fixture_path.open(encoding="utf-8") as stream:
            fixture = json.load(stream)
        fixture_keys = set(fixture["initial_snapshot_event"]["payload"].keys())
        self.assertEqual(set(snapshot.keys()), fixture_keys)

    def test_market_indicators_chan_analysis_and_warnings_combined(self) -> None:
        result = _make_pipeline_result(
            bars_1m=[_bar("2026-07-22 09:31:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
            bars_5m=[_bar("2026-07-22 09:35:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
            daily_bars=[_bar("2026-07-22", 10.0, 10.1, 9.9, 10.05, 1000, 10000, closed=False)],
            quote={
                "timestamp": "2026-07-22 09:35:03",
                "latest_price": 10.05,
                "change_percent": 0.5,
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "previous_close": 10.0,
                "volume": 1000.0,
                "amount": 10000.0,
                "volume_ratio": None,
                "order_imbalance": None,
                "turnover_rate": None,
            },
            indicators_5m={
                **_empty_5m_indicators(),
                "volume": {
                    "values": [{"timestamp": "2026-07-22 09:35:00", "value": 1000.0}],
                    "ma5": [],
                    "ma10": [],
                },
            },
            indicators_1m={
                **_empty_1m_indicators(),
                "volume": {"values": [{"timestamp": "2026-07-22 09:31:00", "value": 1000.0}]},
            },
            chan_analysis=_make_chan_analysis("sh.600000"),
            warnings=[
                {
                    "warning_code": "test_warning",
                    "severity": "info",
                    "message": "test",
                    "affected_capability": "intraday_chart",
                    "affected_field": "market.bars_1m",
                    "details": {},
                }
            ],
        )
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        self.assertEqual(len(snapshot["market"]["bars_1m"]), 1)
        self.assertEqual(len(snapshot["market"]["bars_5m"]), 1)
        self.assertEqual(len(snapshot["market"]["daily_bars"]), 1)
        self.assertIsNotNone(snapshot["market"]["quote"])
        self.assertEqual(
            snapshot["indicators"]["five_minute"]["volume"]["values"][0]["value"],
            1000.0,
        )
        self.assertEqual(
            snapshot["indicators"]["one_minute"]["volume"]["values"][0]["value"],
            1000.0,
        )
        self.assertEqual(snapshot["chan_analysis"]["symbol"], "sh.600000")
        self.assertEqual(len(snapshot["warnings"]), 1)


class CompleteHistoryTests(unittest.TestCase):
    def test_cross_trade_date_5m_history_retained_without_truncation(self) -> None:
        history = [
            _bar("2026-07-21 14:55:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000),
            _bar("2026-07-21 15:00:00", 10.05, 10.15, 10.0, 10.1, 1200, 12000),
            _bar("2026-07-22 09:35:00", 10.1, 10.2, 10.05, 10.15, 1500, 15000),
        ]
        result = _make_pipeline_result(bars_5m=history)
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        self.assertEqual(len(snapshot["market"]["bars_5m"]), 3)
        self.assertEqual(
            [b["timestamp"] for b in snapshot["market"]["bars_5m"]],
            [
                "2026-07-21 14:55:00",
                "2026-07-21 15:00:00",
                "2026-07-22 09:35:00",
            ],
        )

    def test_complete_chan_analysis_and_plot_primitives_not_cropped(self) -> None:
        chan_analysis = _make_chan_analysis("sh.600000")
        chan_analysis["plot_primitives"] = [
            {"type": "line", "points": [("2026-07-22 09:35:00", 10.1)]}
        ]
        chan_analysis["strokes"] = [{"start": "2026-07-21 15:00:00"}]
        result = _make_pipeline_result(chan_analysis=chan_analysis)
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        self.assertEqual(len(snapshot["chan_analysis"]["plot_primitives"]), 1)
        self.assertEqual(len(snapshot["chan_analysis"]["strokes"]), 1)


class DynamicKTests(unittest.TestCase):
    def test_dynamic_5m_only_appears_in_market_bars_5m(self) -> None:
        closed = [
            _bar("2026-07-22 09:35:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000),
        ]
        dynamic = [
            _bar(
                "2026-07-22 09:40:00",
                10.05,
                10.15,
                10.0,
                10.1,
                500,
                5000,
                closed=False,
            ),
        ]
        result = _make_pipeline_result(
            bars_5m=closed + dynamic,
            closed_5m_prefix=closed,
            indicators_5m={
                **_empty_5m_indicators(),
                "volume": {
                    "values": [
                        {"timestamp": "2026-07-22 09:35:00", "value": 1000.0}
                    ],
                    "ma5": [],
                    "ma10": [],
                },
            },
        )
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        bars_5m = snapshot["market"]["bars_5m"]
        self.assertEqual(len(bars_5m), 2)
        self.assertTrue(bars_5m[0]["closed"])
        self.assertFalse(bars_5m[1]["closed"])

        indicator_timestamps = [
            p["timestamp"]
            for p in snapshot["indicators"]["five_minute"]["volume"]["values"]
        ]
        self.assertNotIn("2026-07-22 09:40:00", indicator_timestamps)


class ReplayFieldTests(unittest.TestCase):
    def test_live_replay_is_null(self) -> None:
        result = _make_pipeline_result()
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()
        self.assertIsNone(snapshot["replay"])

    def test_replay_snapshot_includes_authoritative_replay_metadata(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input()
        snapshot = build_workbench_projection(result, session, replay=replay).to_dict()

        self.assertEqual(snapshot["session"]["session_type"], "replay")
        self.assertEqual(snapshot["session"]["trade_date"], "2026-07-22")
        self.assertEqual(snapshot["replay"]["granularity"], "one_minute")
        self.assertEqual(snapshot["replay"]["current_time"], "2026-07-22 10:00:00")
        self.assertEqual(snapshot["replay"]["playback_speed"], 1)

    def test_replay_output_validates_against_replay_schema(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session(state="playing")
        replay = _replay_input(playing=True)
        snapshot = build_workbench_projection(result, session, replay=replay).to_dict()

        validator = _replay_validator("workbench_snapshot")
        validator.validate(snapshot)


class ValidationTests(unittest.TestCase):
    def test_symbol_mismatch_rejected(self) -> None:
        result = _make_pipeline_result(symbol="sh.600000")
        session = SessionProjectionInput(
            session_id="live-1",
            session_type="live",
            symbol="sz.000001",
            trade_date=None,
            state="ready",
            revision=1,
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "symbol"):
            build_workbench_projection(result, session)

    def test_replay_trade_date_mismatch_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session(trade_date="2026-07-23")
        replay = _replay_input()
        with self.assertRaisesRegex(WorkbenchProjectionError, "trade_date"):
            build_workbench_projection(result, session, replay=replay)

    def test_invalid_state_rejected(self) -> None:
        result = _make_pipeline_result()
        session = _live_session(state="unknown")
        with self.assertRaisesRegex(WorkbenchProjectionError, "state"):
            build_workbench_projection(result, session)

    def test_invalid_revision_rejected(self) -> None:
        result = _make_pipeline_result()
        session = SessionProjectionInput(
            session_id="live-1",
            session_type="live",
            symbol="sh.600000",
            trade_date=None,
            state="ready",
            revision=-1,
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "revision"):
            build_workbench_projection(result, session)

    def test_boolean_revision_rejected(self) -> None:
        result = _make_pipeline_result()
        session = SessionProjectionInput(
            session_id="live-1",
            session_type="live",
            symbol="sh.600000",
            trade_date=None,
            state="ready",
            revision=True,  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "revision"):
            build_workbench_projection(result, session)

    def test_empty_session_id_rejected(self) -> None:
        result = _make_pipeline_result()
        session = SessionProjectionInput(
            session_id="",
            session_type="live",
            symbol="sh.600000",
            trade_date=None,
            state="ready",
            revision=1,
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "session_id"):
            build_workbench_projection(result, session)

    def test_live_rejects_non_null_trade_date(self) -> None:
        result = _make_pipeline_result()
        session = SessionProjectionInput(
            session_id="live-1",
            session_type="live",
            symbol="sh.600000",
            trade_date="2026-07-22",
            state="ready",
            revision=1,
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "live"):
            build_workbench_projection(result, session)

    def test_replay_requires_replay_metadata(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "replay"):
            build_workbench_projection(result, session)

    def test_replay_rejects_plain_dict(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = {
            "granularity": "one_minute",
            "current_time": "2026-07-22 10:00:00",
            "next_bar_time": "2026-07-22 10:01:00",
            "start_time": "2026-07-22 09:30:00",
            "end_time": "2026-07-22 15:00:00",
            "playing": False,
            "playback_speed": 1,
            "step_seconds": 60,
        }
        with self.assertRaisesRegex(WorkbenchProjectionError, "ReplayProjectionInput"):
            build_workbench_projection(result, session, replay=replay)  # type: ignore[arg-type]

    def test_live_state_playing_rejected(self) -> None:
        result = _make_pipeline_result()
        session = _live_session(state="playing")
        with self.assertRaisesRegex(WorkbenchProjectionError, "state"):
            build_workbench_projection(result, session)

    def test_live_state_paused_rejected(self) -> None:
        result = _make_pipeline_result()
        session = _live_session(state="paused")
        with self.assertRaisesRegex(WorkbenchProjectionError, "state"):
            build_workbench_projection(result, session)

    def test_replay_state_created_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session(state="created")
        replay = _replay_input()
        with self.assertRaisesRegex(WorkbenchProjectionError, "state"):
            build_workbench_projection(result, session, replay=replay)


class ReplayConsistencyTests(unittest.TestCase):
    def test_replay_invalid_granularity_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(granularity="tick")
        with self.assertRaisesRegex(WorkbenchProjectionError, "granularity"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_invalid_playback_speed_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(playback_speed=3)
        with self.assertRaisesRegex(WorkbenchProjectionError, "playback_speed"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_invalid_step_seconds_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(step_seconds=120)
        with self.assertRaisesRegex(WorkbenchProjectionError, "step_seconds"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_one_minute_requires_step_60(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(granularity="one_minute", step_seconds=300)
        with self.assertRaisesRegex(WorkbenchProjectionError, "one_minute"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_five_minute_requires_step_300(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(granularity="five_minute", step_seconds=60)
        with self.assertRaisesRegex(WorkbenchProjectionError, "five_minute"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_current_time_before_start_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(
            current_time="2026-07-22 09:29:00",
            next_bar_time="2026-07-22 09:30:00",
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "current_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_current_time_after_end_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(
            current_time="2026-07-22 15:01:00",
            next_bar_time="2026-07-22 15:02:00",
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "current_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_current_time_must_match_target_time(self) -> None:
        result = _make_pipeline_result(
            trade_date=date(2026, 7, 22),
            target_time=datetime(2026, 7, 22, 10, 30, 0),
        )
        session = _replay_session()
        replay = _replay_input()
        with self.assertRaisesRegex(WorkbenchProjectionError, "current_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_not_later_than_current_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(next_bar_time="2026-07-22 10:00:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "next_bar_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_earlier_than_current_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(next_bar_time="2026-07-22 09:59:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "next_bar_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_null_allowed(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(next_bar_time=None)
        snapshot = build_workbench_projection(result, session, replay=replay).to_dict()
        self.assertIsNone(snapshot["replay"]["next_bar_time"])

    def test_replay_playing_must_match_state_playing(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session(state="playing")
        replay = _replay_input(playing=False)
        with self.assertRaisesRegex(WorkbenchProjectionError, "playing"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_paused_must_match_state_paused(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session(state="paused")
        replay = _replay_input(playing=True)
        with self.assertRaisesRegex(WorkbenchProjectionError, "playing"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_start_time_wrong_trade_date_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(start_time="2026-07-21 09:30:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "trade_date"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_current_time_wrong_trade_date_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(
            current_time="2026-07-23 10:00:00",
            next_bar_time="2026-07-23 10:01:00",
        )
        with self.assertRaisesRegex(WorkbenchProjectionError, "trade_date"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_end_time_wrong_trade_date_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(end_time="2026-07-23 15:00:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "trade_date"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_wrong_trade_date_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(next_bar_time="2026-07-23 10:01:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "trade_date"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_after_end_rejected(self) -> None:
        result = _make_pipeline_result(trade_date=date(2026, 7, 22))
        session = _replay_session()
        replay = _replay_input(next_bar_time="2026-07-22 15:01:00")
        with self.assertRaisesRegex(WorkbenchProjectionError, "next_bar_time"):
            build_workbench_projection(result, session, replay=replay)

    def test_replay_next_bar_time_equal_end_allowed(self) -> None:
        result = _make_pipeline_result(
            trade_date=date(2026, 7, 22),
            target_time=datetime(2026, 7, 22, 14, 59, 0),
        )
        session = _replay_session()
        replay = _replay_input(
            current_time="2026-07-22 14:59:00",
            next_bar_time="2026-07-22 15:00:00",
        )
        snapshot = build_workbench_projection(result, session, replay=replay).to_dict()
        self.assertEqual(snapshot["replay"]["next_bar_time"], "2026-07-22 15:00:00")


class PayloadContractValidationTests(unittest.TestCase):
    def test_missing_chan_analysis_field_rejected(self) -> None:
        chan_analysis = _make_chan_analysis("sh.600000")
        del chan_analysis["source"]
        result = _make_pipeline_result(chan_analysis=chan_analysis)
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)

    def test_extra_chan_analysis_field_rejected(self) -> None:
        chan_analysis = _make_chan_analysis("sh.600000")
        chan_analysis["future_price"] = 999.0
        result = _make_pipeline_result(chan_analysis=chan_analysis)
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)

    def test_incomplete_indicators_rejected(self) -> None:
        result = _make_pipeline_result(
            indicators_1m={"volume": {"values": []}},
        )
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)

    def test_invalid_warning_rejected(self) -> None:
        result = _make_pipeline_result(
            warnings=[
                {
                    "warning_code": "bad",
                    "severity": "critical",
                    "message": "bad",
                    "affected_capability": "intraday_chart",
                    "affected_field": "x",
                    "details": {},
                }
            ],
        )
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)

    def test_invalid_bar_rejected(self) -> None:
        result = _make_pipeline_result(
            bars_1m=[_bar("2026-07-22 09:31:00", 10.0, 10.1, 9.9, 10.05, -100, 10000)],
        )
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)

    def test_invalid_quote_rejected(self) -> None:
        result = _make_pipeline_result(
            quote={
                "timestamp": "2026-07-22 09:35:03",
                "latest_price": 10.05,
            },
        )
        session = _live_session()
        with self.assertRaisesRegex(WorkbenchProjectionError, "contract"):
            build_workbench_projection(result, session)


class IsolationTests(unittest.TestCase):
    def test_input_mutation_does_not_pollute_projection(self) -> None:
        result = _make_pipeline_result(
            bars_1m=[_bar("2026-07-22 09:31:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
        )
        session = _live_session()
        projection = build_workbench_projection(result, session)

        # Mutate the mutable objects inside the PipelineResult after building.
        result.bars_1m[0]["close"] = 99.0  # type: ignore[index]
        result.indicators_1m["vwap"].append({"timestamp": "x", "value": 1.0})
        result.warnings.append({"new": "warning"})

        snapshot = projection.to_dict()
        self.assertEqual(snapshot["market"]["bars_1m"][0]["close"], 10.05)
        self.assertEqual(len(snapshot["indicators"]["one_minute"]["vwap"]), 0)
        self.assertEqual(len(snapshot["warnings"]), 0)

    def test_to_dict_mutation_does_not_affect_subsequent_calls(self) -> None:
        result = _make_pipeline_result(
            bars_1m=[_bar("2026-07-22 09:31:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
        )
        session = _live_session()
        projection = build_workbench_projection(result, session)

        first = projection.to_dict()
        first["market"]["bars_1m"][0]["close"] = 99.0
        first["session"]["revision"] = 999

        second = projection.to_dict()
        self.assertEqual(second["market"]["bars_1m"][0]["close"], 10.05)
        self.assertEqual(second["session"]["revision"], 1)

    def test_repeated_build_produces_identical_dicts(self) -> None:
        result = _make_pipeline_result(
            bars_5m=[_bar("2026-07-22 09:35:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
        )
        session = _live_session(revision=3)
        first = build_workbench_projection(result, session).to_dict()
        second = build_workbench_projection(result, session).to_dict()
        self.assertEqual(first, second)


class InternalFieldExclusionTests(unittest.TestCase):
    def test_snapshot_excludes_closed_5m_prefix_and_daily_bar(self) -> None:
        result = _make_pipeline_result(
            closed_5m_prefix=[_bar("2026-07-22 09:35:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000)],
            daily_bar=_bar("2026-07-22", 10.0, 10.1, 9.9, 10.05, 1000, 10000, closed=False),
        )
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        self.assertNotIn("closed_5m_prefix", snapshot)
        self.assertNotIn("daily_bar", snapshot)
        self.assertNotIn("closed_5m_prefix", snapshot["market"])
        self.assertNotIn("daily_bar", snapshot["market"])


class FailureAtomicityTests(unittest.TestCase):
    def test_build_failure_does_not_produce_partial_state(self) -> None:
        result = _make_pipeline_result()
        session = SessionProjectionInput(
            session_id="live-1",
            session_type="live",
            symbol="sh.600000",
            trade_date=None,
            state="ready",
            revision=-5,
        )
        with self.assertRaises(WorkbenchProjectionError):
            build_workbench_projection(result, session)


class DeterminismTests(unittest.TestCase):
    def test_no_viewport_or_truncation_parameters_exist(self) -> None:
        result = _make_pipeline_result(
            bars_5m=[_bar(f"2026-07-22 {h:02d}:00:00", 10.0, 10.1, 9.9, 10.05, 1000, 10000) for h in range(10, 15)],
        )
        session = _live_session()
        snapshot = build_workbench_projection(result, session).to_dict()

        self.assertEqual(len(snapshot["market"]["bars_5m"]), 5)
        self.assertNotIn("viewport", snapshot)
        self.assertNotIn("visible_count", snapshot)
        self.assertNotIn("start_index", snapshot)


class SchemaSyncTests(unittest.TestCase):
    def test_package_data_matches_canonical_contracts(self) -> None:
        """Runtime package data must not drift from apps/t0-assistant/contracts."""
        for name in ("logical-schema.json", "replay-v1.schema.json"):
            canonical = (_CONTRACTS_DIR / name).read_text(encoding="utf-8")
            packaged = (resources.files("packages.t0assistant") / "contracts" / name).read_text(
                encoding="utf-8"
            )
            self.assertEqual(canonical, packaged, f"{name} package data drift")

    def test_contracts_are_accessible_via_package_resources(self) -> None:
        for name in ("logical-schema.json", "replay-v1.schema.json"):
            data_file = resources.files("packages.t0assistant") / "contracts" / name
            self.assertTrue(data_file.is_file())
            payload = json.loads(data_file.read_text(encoding="utf-8"))
            self.assertIn("$defs", payload)


class WheelSmokeTests(unittest.TestCase):
    def test_runtime_imports_from_built_wheel(self) -> None:
        """Build a wheel in a temp source copy and verify runtime imports only from the wheel."""
        for tool in ("setuptools", "wheel"):
            try:
                __import__(tool)
            except ImportError as exc:
                self.skipTest(f"missing build dependency: {tool} ({exc})")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            source_copy = tmpdir_path / "src"
            wheel_dir = tmpdir_path / "wheels"
            wheel_dir.mkdir()
            extract_dir = tmpdir_path / "extracted"

            shutil.copytree(
                _REPO_ROOT,
                source_copy,
                ignore=shutil.ignore_patterns(
                    ".git",
                    "build",
                    "dist",
                    "node_modules",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    "*.egg-info",
                    ".DS_Store",
                ),
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--no-build-isolation",
                    "-w",
                    str(wheel_dir),
                ],
                cwd=source_copy,
                check=True,
                capture_output=True,
            )

            wheels = list(wheel_dir.glob("*.whl"))
            self.assertTrue(wheels, "no wheel produced")

            with zipfile.ZipFile(wheels[0]) as archive:
                archive.extractall(extract_dir)

            for name in ("logical-schema.json", "replay-v1.schema.json"):
                self.assertTrue(
                    (extract_dir / "packages" / "t0assistant" / "contracts" / name).is_file(),
                    f"{name} missing from wheel",
                )

            env = {**os.environ, "PYTHONPATH": str(extract_dir)}
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import packages.t0assistant.runtime.workbench_projection as module; print(module.__file__)",
                ],
                cwd=tmpdir_path,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            module_path = Path(result.stdout.strip()).resolve()
            self.assertTrue(
                str(module_path).startswith(str(extract_dir.resolve())),
                f"module loaded from {module_path}, not from extracted wheel {extract_dir}",
            )


if __name__ == "__main__":
    unittest.main()
