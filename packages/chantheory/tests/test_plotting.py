import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))

from chantheory.plotting import build_plot_primitives
from chantheory.schema import (
    AnalysisResult,
    CandidatePoint,
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
                    point_type="first_buy_point",
                    timestamp="2025-01-07",
                    price=10.2,
                    reference_id="pivot_1",
                    confirmed=False,
                    reason="test",
                )
            ],
            meta={"bar_count": 50},
        )

        primitives = build_plot_primitives(result)

        layers = {(item.type, item.layer) for item in primitives}
        self.assertIn(("marker", "fractals"), layers)
        self.assertIn(("line", "strokes"), layers)
        self.assertIn(("line", "segments"), layers)
        self.assertIn(("box", "pivot_zones"), layers)
        self.assertIn(("label", "alerts"), layers)


if __name__ == "__main__":
    unittest.main()
