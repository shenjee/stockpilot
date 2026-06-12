import importlib.util
import unittest
from pathlib import Path


APP_PATH = Path(__file__).resolve().parent / "app.py"
SPEC = importlib.util.spec_from_file_location("chan_streamlit_app", APP_PATH)
app = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(app)


class ChartAxisTests(unittest.TestCase):
    def test_minute_timeframe_uses_continuous_category_axis(self):
        rows = [
            {"date": "2026-06-11 14:30:00", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100},
            {"date": "2026-06-11 15:00:00", "open": 11, "close": 10.5, "high": 11.1, "low": 10.4, "volume": 120},
            {"date": "2026-06-12 09:30:00", "open": 10.6, "close": 10.8, "high": 11.0, "low": 10.5, "volume": 130},
            {"date": "2026-06-12 10:00:00", "open": 10.8, "close": 11.2, "high": 11.3, "low": 10.7, "volume": 150},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={},
            timeframe="30m",
            language="zh",
            x_window=3,
        )

        self.assertEqual(figure.layout.xaxis.type, "category")
        self.assertEqual(tuple(figure.layout.xaxis.categoryarray), tuple(row["date"] for row in rows))
        self.assertEqual(tuple(figure.layout.xaxis.tickvals), ("2026-06-11 14:30:00", "2026-06-12 09:30:00"))
        self.assertEqual(tuple(figure.layout.xaxis.ticktext), ("2026-06-11", "2026-06-12"))
        self.assertEqual(tuple(figure.layout.xaxis.range), (0.5, 3.5))

    def test_day_timeframe_keeps_date_range(self):
        rows = [
            {"date": "2026-06-10", "open": 10, "close": 11, "high": 11.2, "low": 9.8, "volume": 100},
            {"date": "2026-06-11", "open": 11, "close": 10.5, "high": 11.1, "low": 10.4, "volume": 120},
            {"date": "2026-06-12", "open": 10.6, "close": 10.8, "high": 11.0, "low": 10.5, "volume": 130},
        ]

        figure = app._build_figure(
            rows=rows,
            result_payload={"plot_primitives": []},
            visibility={},
            timeframe="day",
            language="zh",
            x_window=2,
        )

        self.assertIsNone(figure.layout.xaxis.type)
        self.assertEqual(tuple(figure.layout.xaxis.range), ("2026-06-11", "2026-06-12"))


if __name__ == "__main__":
    unittest.main()
