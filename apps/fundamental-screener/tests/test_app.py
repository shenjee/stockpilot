"""App import + main() smoke test.

Stubs ``streamlit`` so the test can run without the real package, and verifies
``app.main()`` loads the default fixture and renders without raising.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional


class _DummyContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Recorder:
    def __init__(self, selection_rows: Optional[List[int]] = None) -> None:
        self.dataframes: List[Any] = []
        self.line_charts: List[Any] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.infos: List[str] = []
        self.selectbox_calls: List[str] = []
        # 第一个 st.dataframe 调用（板块表）返回这组 selection.rows，
        # 其余 dataframe 调用一律返回空选择，避免被误用。
        self._pending_selection = list(selection_rows or [])
        self._dataframe_index = 0

    def stub(self) -> types.ModuleType:
        recorder = self
        st = types.ModuleType("streamlit")
        st.session_state = {}
        st.set_page_config = lambda *a, **kw: None
        st.title = lambda *a, **kw: None
        st.caption = lambda *a, **kw: None
        st.header = lambda *a, **kw: None
        st.subheader = lambda *a, **kw: None
        st.markdown = lambda *a, **kw: None
        st.metric = lambda *a, **kw: None
        st.text_input = lambda label, value="", **kw: value

        def _selectbox(label, options, index=0, **kw):
            recorder.selectbox_calls.append(str(label))
            return options[index] if options else None

        st.selectbox = _selectbox

        def _radio(label, options, index=0, **kw):
            return options[index] if options else None

        st.radio = _radio
        st.number_input = lambda label, min_value=None, max_value=None, value=0, step=1, **kw: value
        st.button = lambda *a, **kw: False

        def _info(msg, *a, **kw):
            recorder.infos.append(str(msg))

        def _warning(msg, *a, **kw):
            recorder.warnings.append(str(msg))

        def _error(msg, *a, **kw):
            recorder.errors.append(str(msg))

        st.info = _info
        st.warning = _warning
        st.error = _error

        def _dataframe(rows, *a, **kw):
            recorder.dataframes.append(rows)
            # 只对第一个 dataframe（板块表）返回配置的 selection.rows；
            # 其余调用一律返回空 selection，避免别处误把数据当板块下钻。
            if recorder._dataframe_index == 0:
                rows_out = list(recorder._pending_selection)
            else:
                rows_out = []
            recorder._dataframe_index += 1
            return types.SimpleNamespace(
                selection=types.SimpleNamespace(rows=rows_out, columns=[])
            )

        def _line_chart(df, *a, **kw):
            recorder.line_charts.append(df)

        st.dataframe = _dataframe
        st.line_chart = _line_chart
        st.columns = lambda n: tuple(_MetricColumn(recorder) for _ in range(n))
        st.tabs = lambda labels: tuple(_DummyContext() for _ in labels)
        st.expander = lambda *a, **kw: _DummyContext()
        st.sidebar = _DummyContext()

        def _cache_data(func=None, **kwargs):
            if func is None:
                def wrapper(inner):
                    return inner
                return wrapper
            return func

        st.cache_data = _cache_data
        return st


class _MetricColumn:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def metric(self, *args, **kwargs):
        return None


APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parents[1]
APP_PATH = APP_DIR / "app.py"


def _load_app(recorder: _Recorder):
    st_stub = recorder.stub()
    sys.modules["streamlit"] = st_stub
    sys.path.insert(0, str(ROOT / "packages"))
    sys.path.insert(0, str(APP_DIR))
    spec = importlib.util.spec_from_file_location("fundamental_screener_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AppSmokeTests(unittest.TestCase):
    def test_main_runs_with_default_fixture(self) -> None:
        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        try:
            app.main()
        except ModuleNotFoundError as exc:
            # pandas 不在测试环境时跳过：核心调用边界由 test_data_service 覆盖。
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertEqual(recorder.errors, [], f"errors: {recorder.errors}")
        # 至少渲染了一张表（板块表）。
        self.assertTrue(
            recorder.dataframes,
            "expected at least one dataframe rendered for sectors",
        )
        # 未选行的默认路径必须落到下拉回退。
        self.assertTrue(
            any("下拉" in label for label in recorder.selectbox_calls),
            f"expected selectbox fallback when no row selected, got {recorder.selectbox_calls}",
        )

    def test_row_selection_drives_sector_drill_down(self) -> None:
        """模拟用户点击板块表第二行，应直接下钻到对应板块且不再走下拉回退。"""

        recorder = _Recorder(selection_rows=[1])
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        # 在 main 调用 build_sector_detail 前打补丁，记录实际被下钻的 sector_id。
        detail_calls: List[Dict[str, Any]] = []
        original_build_sector_detail = app.build_sector_detail

        def _spy(snapshot, sector_id, **kwargs):
            detail_calls.append({"sector_id": sector_id, "kwargs": kwargs})
            return original_build_sector_detail(snapshot, sector_id, **kwargs)

        app.build_sector_detail = _spy

        # 拿到板块表当前顺序（与 main 实际使用的排序一致），用于断言行 1 = 第二行。
        snapshot = app.load_snapshot(str(app.DEFAULT_FIXTURE))
        board = app.build_sector_board(
            snapshot, sort=app.DEFAULT_SECTOR_SORT, periods=app.DEFAULT_PERIODS, top=10
        )
        if len(board.sectors) < 2:
            self.skipTest("fixture exposes fewer than 2 sectors; row-1 test not meaningful")
            return
        expected_sector_id = board.sectors[1].sector_id

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertEqual(recorder.errors, [], f"errors: {recorder.errors}")
        self.assertEqual(
            len(detail_calls), 1, f"expected single drill-down, got {detail_calls}"
        )
        self.assertEqual(detail_calls[0]["sector_id"], expected_sector_id)
        # 选中行时不应再触发下拉回退。
        self.assertFalse(
            any("下拉" in label for label in recorder.selectbox_calls),
            f"selectbox fallback should not fire when row is selected, got {recorder.selectbox_calls}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
