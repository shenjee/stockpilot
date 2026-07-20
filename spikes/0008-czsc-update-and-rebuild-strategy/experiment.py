"""Executable ADR 0008 experiments kept outside the production adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from unittest.mock import patch

from chantheory import analyze_normalized, normalize_ohlcv_rows
from chantheory.config import get_default_parameters, get_default_max_bi_num, get_freq_name
from chantheory.engine import load_czsc, parse_dt, run_engine


SYMBOL = "600584.SH"
SOURCE = "adr-0008-deterministic"
TIMEFRAME = "5m"


def normalize_rows(rows: Sequence[Mapping[str, object]]):
    return normalize_ohlcv_rows(rows, symbol=SYMBOL, timeframe=TIMEFRAME, source=SOURCE, strict=True)


def full_rebuild(rows: Sequence[Mapping[str, object]], signals_config: Sequence[object] | Mapping[str, object] | None = None):
    normalized = normalize_rows(rows)
    return normalized, analyze_normalized(normalized, signals_config=signals_config)


class IncrementalExperiment:
    """Own a raw CZSC object strictly inside the Spike boundary.

    Callers receive only the stable project ``AnalysisResult``.  This class is
    evidence code, not a proposed public API.
    """

    def __init__(
        self,
        rows: Sequence[Mapping[str, object]],
        signals_config: Sequence[object] | Mapping[str, object] | None = None,
        isolate_signal_replay: bool = True,
    ):
        self._rows = [dict(row) for row in rows]
        self._signals_config = signals_config
        self._isolate_signal_replay = isolate_signal_replay
        normalized = normalize_rows(self._rows)
        parameters = get_default_parameters()
        parameters["max_bi_num"] = get_default_max_bi_num(TIMEFRAME)
        self._parameters = parameters
        self._analyzer, self._raw_bars = run_engine(normalized, parameters)
        self._RawBar, self._Freq, _ = load_czsc()

    def advance(self, row: Mapping[str, object]):
        self._rows.append(dict(row))
        normalized = normalize_rows(self._rows)
        bar = normalized.bars[-1]
        raw_bar = self._RawBar(
            symbol=bar.symbol,
            id=bar.bar_index,
            dt=parse_dt(bar.timestamp),
            freq=getattr(self._Freq, get_freq_name(bar.timeframe)),
            open=bar.open,
            close=bar.close,
            high=bar.high,
            low=bar.low,
            vol=bar.volume,
            amount=bar.amount,
        )
        self._analyzer.update(raw_bar)
        self._raw_bars.append(raw_bar)
        return self.result(normalized)

    def result(self, normalized=None):
        normalized = normalized or normalize_rows(self._rows)
        # Signal functions cache calculations on RawBar objects. Reusing the
        # engine-owned bars across published snapshots makes the second output
        # differ from a clean rebuild. A safe stateful boundary must therefore
        # keep signal-replay mutation away from the analyzer's mutable input.
        signal_bars = self._clone_raw_bars(normalized) if self._isolate_signal_replay else list(self._raw_bars)
        with patch("chantheory.adapters._run_engine", return_value=(self._analyzer, signal_bars)):
            return normalized, analyze_normalized(
                normalized,
                parameters=dict(self._parameters),
                signals_config=self._signals_config,
            )

    def _clone_raw_bars(self, normalized):
        freq = getattr(self._Freq, get_freq_name(normalized.timeframe))
        return [
            self._RawBar(
                symbol=bar.symbol,
                id=bar.bar_index,
                dt=parse_dt(bar.timestamp),
                freq=freq,
                open=bar.open,
                close=bar.close,
                high=bar.high,
                low=bar.low,
                vol=bar.volume,
                amount=bar.amount,
            )
            for bar in normalized.bars
        ]


class ClosedBarProjection:
    """Minimal display/analysis boundary for an unclosed dynamic 5m bar."""

    def __init__(self, closed_rows: Sequence[Mapping[str, object]], signals_config=None):
        self._closed_rows = [dict(row) for row in closed_rows]
        self._signals_config = signals_config

    def project(self, dynamic_row: Mapping[str, object] | None = None) -> dict[str, Any]:
        normalized, analysis = full_rebuild(self._closed_rows, self._signals_config)
        return {
            "closed_bars": [dict(row) for row in self._closed_rows],
            "dynamic_bar": dict(dynamic_row) if dynamic_row is not None else None,
            "analysis": analysis,
            "normalized": normalized,
        }

    def close(self, row: Mapping[str, object]) -> dict[str, Any]:
        self._closed_rows.append(dict(row))
        return self.project()


def rebuild_seek(warm_rows, target_rows, prefix, signals_config=None):
    return full_rebuild(list(warm_rows) + list(target_rows[:prefix]), signals_config)


@dataclass(frozen=True)
class FakeTask:
    session_id: str
    pipeline_id: str
    generation: int
    kind: str
    value: object


class FakeGenerationExecutor:
    """Small stale-result isolation model; it intentionally does no threading."""

    def __init__(self):
        self._generation: dict[str, int] = {}
        self._active: set[str] = set()
        self._pipelines: dict[str, object] = {}
        self.published: list[FakeTask] = []

    def start_session(self, session_id: str, pipeline: object) -> str:
        self._generation[session_id] = 0
        self._active.add(session_id)
        self._pipelines[session_id] = pipeline
        return f"pipeline:{session_id}:{id(pipeline)}"

    def submit(self, session_id: str, kind: str, value: object) -> FakeTask:
        if session_id not in self._active:
            raise ValueError("session is retired")
        self._generation[session_id] += 1
        pipeline_id = f"pipeline:{session_id}:{id(self._pipelines[session_id])}"
        return FakeTask(session_id, pipeline_id, self._generation[session_id], kind, value)

    def complete(self, task: FakeTask) -> bool:
        expected_pipeline = f"pipeline:{task.session_id}:{id(self._pipelines.get(task.session_id))}"
        publishable = (
            task.session_id in self._active
            and task.generation == self._generation.get(task.session_id)
            and task.pipeline_id == expected_pipeline
        )
        if publishable:
            self.published.append(task)
        return publishable

    def retire(self, session_id: str) -> None:
        self._active.discard(session_id)
        self._generation[session_id] = self._generation.get(session_id, 0) + 1

    def pipeline(self, session_id: str) -> object:
        return self._pipelines[session_id]
