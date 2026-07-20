from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPIKE = ROOT / "spikes" / "0008-czsc-update-and-rebuild-strategy"
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(SPIKE))

from chantheory.config import get_freq_name
from chantheory.engine import run_engine
from comparator import compare_results, semantic_diff, semantic_payload
from experiment import (
    ClosedBarProjection,
    FakeGenerationExecutor,
    IncrementalExperiment,
    full_rebuild,
    normalize_rows,
    rebuild_seek,
)
from fixture import (
    canonical_fixture_bytes,
    fixture_sha256,
    generate_fixture,
    load_fixture,
    session_end_times,
    split_rows,
    validate_fixture,
)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = load_fixture()
        cls.warm, cls.target = split_rows(cls.payload)

    def test_fixture_is_generated_and_valid(self):
        self.assertEqual(validate_fixture(self.payload), [])
        self.assertEqual(canonical_fixture_bytes(generate_fixture()), canonical_fixture_bytes(self.payload))
        self.assertEqual(len(self.warm), 500)
        self.assertEqual(len(self.target), 48)
        self.assertEqual(len(session_end_times()), 48)
        self.assertEqual(fixture_sha256(self.payload), "3d01a0b19633ca42ab49df72903a3b4ea93b9ddc9d59eff24db1d5a51ef4e79f")

    def test_normalization_sorts_and_keeps_last_duplicate(self):
        rows = [dict(self.warm[1]), dict(self.warm[0]), dict(self.warm[1])]
        rows[-1]["close"] = rows[-1]["open"]
        rows[-1]["high"] = max(rows[-1]["high"], rows[-1]["close"])
        rows[-1]["low"] = min(rows[-1]["low"], rows[-1]["close"])
        normalized = normalize_rows(rows)
        self.assertEqual(len(normalized.bars), 2)
        self.assertLess(normalized.bars[0].timestamp, normalized.bars[1].timestamp)
        self.assertEqual(normalized.bars[1].close, rows[-1]["close"])
        self.assertIn("DUPLICATE_TIMESTAMP", [item.warning_code for item in normalized.warnings])

    def test_project_5m_maps_to_czsc_f5(self):
        normalized = normalize_rows(self.warm[:3])
        analyzer, raw = run_engine(normalized, {"max_bi_num": 500})
        self.assertEqual(get_freq_name("5m"), "F5")
        self.assertEqual(raw[0].freq.name, "F5")
        self.assertEqual(raw[0].dt.strftime("%H:%M"), "13:25")
        self.assertEqual(len(analyzer.bars_raw), 3)


class ComparatorTests(unittest.TestCase):
    def test_reports_smallest_semantic_path(self):
        differences = semantic_diff({"a": [{"b": 1}]}, {"a": [{"b": 2}]})
        self.assertEqual(differences, [{"path": "$.a[0].b", "left": 1, "right": 2, "reason": "value"}])

    def test_requires_every_stable_result_field(self):
        with self.assertRaisesRegex(ValueError, "misses required"):
            semantic_payload({"symbol": "x"})


class RebuildAndIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = load_fixture()
        cls.warm, cls.target = split_rows(payload)
        cls.oracle = {}
        cls.incremental_diffs = {}
        cls.determinism_diffs = {}
        incremental = IncrementalExperiment(cls.warm)

        normalized, result = full_rebuild(cls.warm)
        cls.oracle[0] = (normalized, result)
        normalized_again, result_again = full_rebuild(cls.warm)
        cls.determinism_diffs[0] = compare_results(result, result_again, normalized, normalized_again)

        for prefix, row in enumerate(cls.target, 1):
            rows = cls.warm + cls.target[:prefix]
            normalized, result = full_rebuild(rows)
            cls.oracle[prefix] = (normalized, result)
            incremental_normalized, incremental_result = incremental.advance(row)
            cls.incremental_diffs[prefix] = compare_results(
                result, incremental_result, normalized, incremental_normalized
            )
            normalized_again, result_again = full_rebuild(rows)
            cls.determinism_diffs[prefix] = compare_results(result, result_again, normalized, normalized_again)

    def test_500_bar_full_rebuild(self):
        normalized, result = self.oracle[0]
        self.assertEqual(len(normalized.bars), 500)
        self.assertEqual(result.meta["bar_count"], 500)
        self.assertEqual(result.meta["engine_probe"]["status"], "ok")
        self.assertTrue(result.fractals)
        self.assertTrue(result.strokes)

    def test_every_closed_bar_prefix_is_deterministic(self):
        failures = {prefix: diff[:1] for prefix, diff in self.determinism_diffs.items() if diff}
        self.assertEqual(failures, {})

    def test_incremental_update_matches_rebuild_for_every_prefix(self):
        failures = {prefix: diff[:1] for prefix, diff in self.incremental_diffs.items() if diff}
        self.assertEqual(failures, {})

    def test_signal_and_plot_contract_are_exercised(self):
        _, result = self.oracle[48]
        self.assertEqual(len(result.signal_series), 4)
        self.assertTrue(result.signal_snapshots)
        self.assertTrue(result.plot_primitives)
        payload = semantic_payload(result)
        for field in ("signal_events", "candidate_buy_points", "candidate_sell_points", "pivot_zones", "plot_primitives", "meta"):
            self.assertIn(field, payload)

    def test_unsafe_raw_bar_sharing_has_prefix_two_minimal_reproduction(self):
        unsafe = IncrementalExperiment(self.warm, isolate_signal_replay=False)
        first_normalized, first = unsafe.advance(self.target[0])
        oracle_normalized, oracle = full_rebuild(self.warm + self.target[:1])
        self.assertEqual(compare_results(oracle, first, oracle_normalized, first_normalized), [])

        second_normalized, second = unsafe.advance(self.target[1])
        oracle_normalized, oracle = full_rebuild(self.warm + self.target[:2])
        differences = compare_results(oracle, second, oracle_normalized, second_normalized)
        self.assertTrue(differences)
        self.assertEqual(differences[0]["path"], "$.signal_series[3].points[0].status")
        self.assertEqual(differences[0]["left"], "not_ready")
        self.assertEqual(differences[0]["right"], "inactive")


class DynamicBarAndReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = load_fixture()
        cls.warm, cls.target = split_rows(payload)

    def test_unclosed_dynamic_bar_never_enters_analysis(self):
        gate = ClosedBarProjection(self.warm)
        baseline = gate.project()
        dynamic = dict(self.target[0])
        dynamic["close"] = dynamic["open"]
        preview = gate.project(dynamic)
        self.assertEqual(compare_results(baseline["analysis"], preview["analysis"], baseline["normalized"], preview["normalized"]), [])
        self.assertEqual(preview["dynamic_bar"]["timestamp"], dynamic["timestamp"])
        self.assertEqual(preview["analysis"].meta["bar_count"], 500)
        closed = gate.close(self.target[0])
        self.assertEqual(closed["analysis"].meta["bar_count"], 501)
        self.assertEqual(closed["normalized"].bars[-1].timestamp, self.target[0]["timestamp"])

    def test_backward_seek_discards_future_and_forward_reaches_clean_t2(self):
        t1, t2 = 16, 40
        _, late = rebuild_seek(self.warm, self.target, t2)
        t1_normalized, sought = rebuild_seek(self.warm, self.target, t1)
        direct_normalized, direct = full_rebuild(self.warm + self.target[:t1])
        self.assertEqual(compare_results(sought, direct, t1_normalized, direct_normalized), [])

        serialized = json.dumps(semantic_payload(sought, t1_normalized), ensure_ascii=False)
        future_timestamps = [str(row["timestamp"]) for row in self.target[t1:t2]]
        self.assertFalse(any(timestamp in serialized for timestamp in future_timestamps))

        forward = IncrementalExperiment(self.warm + self.target[:t1])
        forward_result = None
        for row in self.target[t1:t2]:
            forward_result = forward.advance(row)
        clean_normalized, clean = full_rebuild(self.warm + self.target[:t2])
        self.assertIsNotNone(forward_result)
        self.assertEqual(compare_results(clean, forward_result[1], clean_normalized, forward_result[0]), [])
        self.assertNotEqual(semantic_payload(late), {})


class StaleIsolationTests(unittest.TestCase):
    def test_stale_replay_and_retired_results_are_discarded(self):
        executor = FakeGenerationExecutor()
        live_pipeline, replay_pipeline = object(), object()
        executor.start_session("live", live_pipeline)
        executor.start_session("replay", replay_pipeline)
        self.assertIsNot(executor.pipeline("live"), executor.pipeline("replay"))

        old_seek = executor.submit("replay", "seek", "T1")
        new_seek = executor.submit("replay", "seek", "T2")
        self.assertTrue(executor.complete(new_seek))
        self.assertFalse(executor.complete(old_seek))
        self.assertEqual([task.value for task in executor.published], ["T2"])

        retired = executor.submit("replay", "seek", "T3")
        executor.retire("replay")
        self.assertFalse(executor.complete(retired))
        live = executor.submit("live", "closed_bar", "live-result")
        self.assertTrue(executor.complete(live))
        self.assertIs(executor.pipeline("live"), live_pipeline)


class BenchmarkSmokeTests(unittest.TestCase):
    def test_benchmark_smoke(self):
        from benchmark import run_benchmarks

        result = run_benchmarks(smoke=True)
        measurements = result["measurements"]
        for key in (
            "cold_500_full_rebuild",
            "warm_500_full_rebuild",
            "one_bar_full_rebuild",
            "forward_incremental_project_result",
            "backward_seek_rebuild_t1_16",
            "stage_profile_548",
            "peak_python_allocated_memory_bytes_548",
        ):
            self.assertIn(key, measurements)


if __name__ == "__main__":
    unittest.main()
