import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.plotting import build_plot_primitives
from chantheory.schema import (
    AnalysisResult,
    CandidatePoint,
    Divergence,
    Fractal,
    PivotZone,
    Segment,
    Stroke,
    StructureAlert,
)


class PlottingTests(unittest.TestCase):
    def test_build_plot_primitives_returns_layers_for_mapped_structures(self):
        result = AnalysisResult(
            symbol="000001.SZ",
            timeframe="day",
            source="tencent",
            engine="czsc",
            engine_version="0.10.12",
            parameters={},
            fractals=[
                Fractal(
                    id="fractal_1",
                    fractal_type="bottom",
                    bar_index=1,
                    timestamp="2025-01-02",
                    price=9.7,
                    confirmed=True,
                ),
                Fractal(
                    id="fractal_2",
                    fractal_type="top",
                    bar_index=2,
                    timestamp="2025-01-03",
                    price=10.9,
                    confirmed=True,
                )
            ],
            strokes=[
                Stroke(
                    id="stroke_1",
                    direction="up",
                    start_fractal_id="fractal_1",
                    end_fractal_id="fractal_2",
                    start_timestamp="2025-01-02",
                    end_timestamp="2025-01-03",
                    start_price=9.7,
                    end_price=10.9,
                    confirmed=True,
                )
            ],
            segments=[
                Segment(
                    id="segment_1",
                    direction="up",
                    stroke_ids=["stroke_1"],
                    start_timestamp="2025-01-02",
                    end_timestamp="2025-01-07",
                    start_price=9.7,
                    end_price=11.3,
                    confirmed=True,
                )
            ],
            pivot_zones=[
                PivotZone(
                    id="pivot_1",
                    start_timestamp="2025-01-02",
                    end_timestamp="2025-01-07",
                    high=11.0,
                    low=10.0,
                    segment_ids=["segment_1"],
                    level="stroke",
                    active=True,
                )
            ],
            divergences=[
                Divergence(
                    id="divergence_1",
                    divergence_type="bearish",
                    reference_type="stroke",
                    reference_id="stroke_1",
                    timestamp="2025-01-07",
                    strength="normal",
                    confirmed=True,
                    description="test divergence",
                    meta={"price": 11.3},
                )
            ],
            structure_alerts=[
                StructureAlert(
                    id="alert_1",
                    alert_type="active_pivot_zone",
                    severity="info",
                    timestamp="2025-01-07",
                    related_ids=["pivot_1"],
                    message="An active pivot zone is present.",
                )
            ],
            candidate_buy_points=[
                CandidatePoint(
                    id="buy_1",
                    point_type="first_buy",
                    timestamp="2025-01-07",
                    price=10.2,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "down"},
                ),
                CandidatePoint(
                    id="buy_2",
                    point_type="second_buy",
                    timestamp="2025-01-07",
                    price=10.2,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "down"},
                ),
                CandidatePoint(
                    id="buy_3",
                    point_type="third_buy",
                    timestamp="2025-01-07",
                    price=10.2,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "down"},
                ),
            ],
            candidate_sell_points=[
                CandidatePoint(
                    id="sell_1",
                    point_type="first_sell",
                    timestamp="2025-01-08",
                    price=11.5,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "up"},
                ),
                CandidatePoint(
                    id="sell_2",
                    point_type="second_sell",
                    timestamp="2025-01-08",
                    price=11.5,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "up"},
                ),
                CandidatePoint(
                    id="sell_3",
                    point_type="third_sell",
                    timestamp="2025-01-08",
                    price=11.5,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                    meta={"signal_scope": "cxt_signal", "direction": "up"},
                ),
            ],
            meta={
                "bar_count": 50,
                "pending_stroke": Stroke(
                    id="stroke_pending_1",
                    direction="up",
                    start_fractal_id="fractal_2",
                    end_fractal_id="fractal_pending",
                    start_timestamp="2025-01-03",
                    end_timestamp="2025-01-08",
                    start_price=10.9,
                    end_price=11.5,
                    confirmed=False,
                    meta={"pending": True},
                ),
            },
        )

        primitives = build_plot_primitives(result)

        layers = {(item.type, item.layer) for item in primitives}
        self.assertIn(("marker", "fractals"), layers)
        self.assertIn(("line", "strokes"), layers)
        self.assertIn(("line", "segments"), layers)
        self.assertIn(("box", "pivot_zones"), layers)
        self.assertIn(("marker", "candidate_points"), layers)
        self.assertIn(("marker", "divergences"), layers)
        self.assertIn(("label", "alerts"), layers)
        candidates = [item for item in primitives if item.layer == "candidate_points"]
        divergence = next(item for item in primitives if item.layer == "divergences")
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].text, "<br>↑<br>1B, 2B, 3B")
        self.assertEqual(candidates[0].meta["signal_scope"], "cxt_signal")
        self.assertEqual(candidates[1].text, "1S, 2S, 3S<br>↓<br>")
        self.assertEqual(divergence.text, "Bear Div")
        self.assertEqual(divergence.meta["strength"], "normal")
        bottom_fractal = next(item for item in primitives if item.id == "primitive_fractal_1")
        top_fractal = next(item for item in primitives if item.id == "primitive_fractal_2")
        pending_stroke = next(item for item in primitives if item.id == "primitive_stroke_pending_1")
        self.assertEqual(bottom_fractal.text, "")
        self.assertEqual(bottom_fractal.meta["textposition"], "bottom center")
        self.assertEqual(top_fractal.text, "")
        self.assertEqual(top_fractal.meta["textposition"], "top center")
        self.assertEqual(pending_stroke.style, "dashed")
        self.assertFalse(pending_stroke.meta["confirmed"])
        self.assertTrue(pending_stroke.meta["pending"])


if __name__ == "__main__":
    unittest.main()
