import unittest
from pathlib import Path


WIDGET_HTML = Path(__file__).resolve().parents[1] / "chan_chart_widget" / "index.html"


class ChartWidgetSourceTests(unittest.TestCase):
    def test_plotly_axis_conversions_use_axis_relative_pixels(self):
        source = WIDGET_HTML.read_text(encoding="utf-8")

        # Plotly p2d/d2p operate in the axis-local pixel coordinate system.
        # The overlay receives chartDiv-local pointer coordinates, so the
        # axis offset must be removed before p2d and restored after d2p.
        self.assertIn("xa.p2d(cx - xa._offset)", source)
        self.assertIn("xa._offset + xa.d2p(index)", source)
        self.assertNotIn("dataX = xa.p2d(cx);", source)


if __name__ == "__main__":
    unittest.main()
