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

    def test_rejects_unexpected_result_fields(self):
        _, result = full_rebuild(load_fixture()["bars"][:3], signals_config=[])
        payload = result.to_dict()
        payload["future_schema_field"] = {"must_not_be_ignored": True}
        with self.assertRaisesRegex(ValueError, "unexpected semantic fields.*future_schema_field"):
            semantic_payload(payload)


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

    def test_finished_bis_tail_exclusion_condition(self):
        """Regression assertion: czsc 1.0.1 retains the tail-exclusion
        condition on finished_bis. The type stubs claim finished_bis is
        identical to bi_list, but the actual engine excludes the last bi
        when the unfinished-bi (ubi) buffer holds fewer than 5 bars
        (``len(bars_ubi) < 5``). When ``bars_ubi >= 5`` the last bi is
        confirmed and finished_bis equals bi_list.

        ``ubi`` is a plain dict in 1.0.1 (not an object), so attribute
        access via ``getattr(ubi, "bars", [])`` silently returns ``[]`` and
        never exercises the exclusion branch. This test uses two real
        fixed prefixes of the 5m fixture — one per branch — and verifies
        bi endpoints so the condition cannot regress undetected."""
        # Prefix 60: bars_ubi=3 (< 5) → last bi excluded.
        normalized_excl = normalize_rows(self.warm[:60])
        analyzer_excl, _ = run_engine(normalized_excl, {"max_bi_num": 500})
        bi_list_excl = list(getattr(analyzer_excl, "bi_list", []) or [])
        finished_bis_excl = list(getattr(analyzer_excl, "finished_bis", []) or [])
        ubi_excl = getattr(analyzer_excl, "ubi", None)
        bars_ubi_excl = len(ubi_excl["bars"]) if isinstance(ubi_excl, dict) else 0

        self.assertGreaterEqual(len(bi_list_excl), 2)
        self.assertEqual(bars_ubi_excl, 3)
        self.assertEqual(len(finished_bis_excl), len(bi_list_excl) - 1)
        self.assertNotIn(bi_list_excl[-1], finished_bis_excl)
        # Excluded last bi endpoints (frozen from the 5m fixture).
        self.assertEqual(str(getattr(bi_list_excl[-1], "sdt", None)), "2026-06-30 10:45:00")
        self.assertEqual(str(getattr(bi_list_excl[-1], "edt", None)), "2026-06-30 14:00:00")

        # Prefix 62: bars_ubi=5 (>= 5) → last bi confirmed → included.
        normalized_conf = normalize_rows(self.warm[:62])
        analyzer_conf, _ = run_engine(normalized_conf, {"max_bi_num": 500})
        bi_list_conf = list(getattr(analyzer_conf, "bi_list", []) or [])
        finished_bis_conf = list(getattr(analyzer_conf, "finished_bis", []) or [])
        ubi_conf = getattr(analyzer_conf, "ubi", None)
        bars_ubi_conf = len(ubi_conf["bars"]) if isinstance(ubi_conf, dict) else 0

        self.assertGreaterEqual(len(bi_list_conf), 2)
        self.assertEqual(bars_ubi_conf, 5)
        self.assertEqual(len(finished_bis_conf), len(bi_list_conf))
        self.assertIn(bi_list_conf[-1], finished_bis_conf)
        # Confirmed last bi endpoints (same bi as the excluded case).
        self.assertEqual(str(getattr(bi_list_conf[-1], "sdt", None)), "2026-06-30 10:45:00")
        self.assertEqual(str(getattr(bi_list_conf[-1], "edt", None)), "2026-06-30 14:00:00")

    def test_zs_list_matches_get_zs_seq_on_finished_bis(self):
        """czsc 1.0.1 exposes zs_list as a CZSC instance attribute and
        get_zs_seq as a top-level function. Both must produce the same
        pivot-zone sequence when applied to finished_bis."""
        from chantheory.engine import load_czsc_utils

        normalized = normalize_rows(self.warm)
        analyzer, _ = run_engine(normalized, {"max_bi_num": 500})
        zs_list = list(getattr(analyzer, "zs_list", []) or [])
        if not zs_list:
            self.skipTest("no pivot zones in 500-bar fixture")

        sig_module = load_czsc_utils()
        get_zs_seq = getattr(sig_module, "get_zs_seq")
        finished_bis = list(getattr(analyzer, "finished_bis", []) or [])
        zs_from_func = list(get_zs_seq(finished_bis) or [])

        self.assertEqual(len(zs_list), len(zs_from_func))
        for zs_a, zs_b in zip(zs_list, zs_from_func):
            self.assertEqual(getattr(zs_a, "sdt", None), getattr(zs_b, "sdt", None))
            self.assertEqual(getattr(zs_a, "edt", None), getattr(zs_b, "edt", None))
            self.assertEqual(getattr(zs_a, "zg", None), getattr(zs_b, "zg", None))
            self.assertEqual(getattr(zs_a, "zd", None), getattr(zs_b, "zd", None))

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


class RealEngineSignalTests(unittest.TestCase):
    """Real czsc 1.0.1 engine samples for the four default signals (#175 DoD).

    These tests exercise the Rust-native ``czsc._native.call_signal``
    dispatcher against the frozen 5m fixture. They document the coverage
    status of each default signal (triggered vs. non-triggered) so that
    regressions in signal evaluation are caught immediately.

    Coverage summary for the 548-bar fixture (500 warm + 48 target):
    - cxt_first_buy_V221126:  NOT triggered (0 active points)
    - cxt_first_sell_V221126: triggered (46 active points, e.g. bar 276)
    - cxt_second_bs_V240524:  triggered (38 active points, e.g. bar 341)
    - cxt_third_bs_V230319:   NOT triggered (0 active points)
    """

    @classmethod
    def setUpClass(cls):
        payload = load_fixture()
        cls.warm, cls.target = split_rows(payload)
        cls.normalized, cls.result = full_rebuild(cls.warm + cls.target)
        cls.series_by_key = {ss.signal_key: ss for ss in cls.result.signal_series}

    def test_four_default_signal_series_are_present(self):
        """All four default signals produce a series with the expected keys."""
        self.assertEqual(
            [ss.signal_key for ss in self.result.signal_series],
            ["first_buy", "first_sell", "second_bs", "third_bs"],
        )
        for ss in self.result.signal_series:
            self.assertEqual(ss.module, "czsc._native")
            self.assertTrue(ss.points)

    def test_first_sell_signal_triggers_on_real_engine(self):
        """cxt_first_sell_V221126 triggers on the 5m fixture (real engine sample 1).

        The signal fires at bar 276 with value ``一卖_9笔_任意_0`` and produces
        a ``triggered`` event. This verifies the Rust-native dispatcher
        evaluates the first-sell signal correctly against real chan structure.
        """
        series = self.series_by_key["first_sell"]
        active_points = [p for p in series.points if p.active]
        self.assertGreater(len(active_points), 0)

        bar_276 = next(p for p in series.points if p.bar_index == 276)
        self.assertEqual(bar_276.status, "active")
        self.assertEqual(bar_276.value, "一卖_9笔_任意_0")

        events = [e for e in self.result.signal_events if e.signal_key == "first_sell" and e.event_type == "triggered"]
        self.assertTrue(any(e.bar_index == 276 for e in events))

    def test_second_bs_signal_triggers_on_real_engine(self):
        """cxt_second_bs_V240524 triggers on the 5m fixture (real engine sample 2).

        The signal fires at bar 341 with value ``二买_任意_任意_0`` and produces
        a ``triggered`` event. This verifies the Rust-native dispatcher
        evaluates the second buy/sell signal correctly against real chan
        structure.
        """
        series = self.series_by_key["second_bs"]
        active_points = [p for p in series.points if p.active]
        self.assertGreater(len(active_points), 0)

        bar_341 = next(p for p in series.points if p.bar_index == 341)
        self.assertEqual(bar_341.status, "active")
        self.assertEqual(bar_341.value, "二买_任意_任意_0")

        events = [e for e in self.result.signal_events if e.signal_key == "second_bs" and e.event_type == "triggered"]
        self.assertTrue(any(e.bar_index == 341 for e in events))

    def test_first_buy_signal_does_not_trigger_on_fixture(self):
        """cxt_first_buy_V221126 does not trigger on the 5m fixture.

        The fixture's chan structure does not form a valid first-buy pattern.
        All 548 points are ``inactive`` with value ``其他_任意_任意_0``. This is
        documented coverage: the signal is exercised (evaluated bar-by-bar)
        but the structure does not match.
        """
        series = self.series_by_key["first_buy"]
        active_points = [p for p in series.points if p.active]
        self.assertEqual(len(active_points), 0)
        self.assertEqual(series.latest_value, "其他_任意_任意_0")

    def test_third_bs_signal_does_not_trigger_on_fixture(self):
        """cxt_third_bs_V230319 does not trigger on the 5m fixture.

        The fixture's chan structure does not form a valid third buy/sell
        pattern. All 548 points are ``inactive`` with value
        ``其他_任意_任意_0``. This is documented coverage: the signal is
        exercised (evaluated bar-by-bar) but the structure does not match.
        """
        series = self.series_by_key["third_bs"]
        active_points = [p for p in series.points if p.active]
        self.assertEqual(len(active_points), 0)
        self.assertEqual(series.latest_value, "其他_任意_任意_0")

    def test_candidate_point_events_align_with_signal_events(self):
        """Signal evaluations flow through to candidate point events.

        first_sell triggers map to ``first_sell`` candidate events, and
        second_bs triggers map to ``second_buy``/``second_sell`` candidate
        events. This verifies the full pipeline from dispatcher → evaluation
        → candidate point mapping works end-to-end on the real engine.
        """
        sell_events = [e for e in self.result.candidate_point_events if e.point_type == "first_sell"]
        buy_events = [e for e in self.result.candidate_point_events if e.point_type == "second_buy"]
        self.assertGreater(len(sell_events), 0)
        self.assertGreater(len(buy_events), 0)
        self.assertTrue(any(e.bar_index == 276 for e in sell_events))
        self.assertTrue(any(e.bar_index == 341 for e in buy_events))

    def test_unsafe_raw_bar_sharing_has_prefix_two_minimal_reproduction(self):
        # czsc 1.0.1's Rust-native signal dispatcher (czsc._native.call_signal)
        # is stateless with respect to RawBar objects: it does not cache
        # intermediate calculations on the bar instances the way the old
        # pure-Python czsc.signals.cxt functions did. Consequently, sharing
        # the engine-owned RawBar list across signal replay no longer produces
        # a different result from a clean rebuild. This test was originally
        # written under czsc 0.10.12 where the old signal functions mutated
        # shared bar state; under 1.0.1 the unsafe and isolated paths are
        # equivalent, which is the correct and safer behaviour.
        unsafe = IncrementalExperiment(self.warm, isolate_signal_replay=False)
        first_normalized, first = unsafe.advance(self.target[0])
        oracle_normalized, oracle = full_rebuild(self.warm + self.target[:1])
        self.assertEqual(compare_results(oracle, first, oracle_normalized, first_normalized), [])

        second_normalized, second = unsafe.advance(self.target[1])
        oracle_normalized, oracle = full_rebuild(self.warm + self.target[:2])
        differences = compare_results(oracle, second, oracle_normalized, second_normalized)
        # Under czsc 1.0.1 the Rust-native dispatcher does not mutate shared
        # RawBar state, so the unsafe path matches the clean rebuild.
        self.assertEqual(differences, [])


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
        for key in ("one_bar_full_rebuild", "forward_incremental_project_result"):
            measurement = measurements[key]
            self.assertEqual(measurement["sweeps"], 1)
            self.assertEqual(measurement["prefixes_per_sweep"], 3)
            self.assertEqual(len(measurement["rounds"][0]["samples"]), 3)
            self.assertEqual(sorted(measurement["by_prefix"]), ["1", "24", "48"])


if __name__ == "__main__":
    unittest.main()
