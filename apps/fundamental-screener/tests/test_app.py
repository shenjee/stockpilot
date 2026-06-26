"""App import + main() smoke test (Phase 7 产品化前端).

Stubs ``streamlit`` so the test can run without the real package, and verifies
``app.main()`` renders correctly via the product-level ``load_or_refresh_snapshot``
entry point. Tests assert that the UI does NOT expose fixture / SQLite / path
inputs (docs §2.4).
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
    def __init__(
        self,
        selection_rows: Optional[List[int]] = None,
        button_return: bool = False,
    ) -> None:
        self.dataframes: List[Any] = []
        self.line_charts: List[Any] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.infos: List[str] = []
        self.selectbox_calls: List[str] = []
        self.text_input_calls: List[str] = []
        self.radio_calls: List[str] = []
        self.button_calls: List[str] = []
        self.checkbox_calls: List[str] = []
        self.date_input_calls: List[str] = []
        self._button_return = button_return
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

        def _text_input(label, value="", **kw):
            recorder.text_input_calls.append(str(label))
            return value

        st.text_input = _text_input

        def _selectbox(label, options, index=0, **kw):
            recorder.selectbox_calls.append(str(label))
            return options[index] if options else None

        st.selectbox = _selectbox

        def _radio(label, options, index=0, **kw):
            recorder.radio_calls.append(str(label))
            return options[index] if options else None

        st.radio = _radio
        st.number_input = lambda label, min_value=None, max_value=None, value=0, step=1, **kw: value

        def _checkbox(label, value=False, **kw):
            recorder.checkbox_calls.append(str(label))
            return value

        st.checkbox = _checkbox

        def _date_input(label, value=None, **kw):
            recorder.date_input_calls.append(str(label))
            from datetime import date
            return value or date.today()

        st.date_input = _date_input

        def _button(*a, **kw):
            recorder.button_calls.append(str(a[0]) if a else "")
            return recorder._button_return

        st.button = _button

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
        st.spinner = lambda *a, **kw: _DummyContext()
        st.sidebar = _DummyContext()

        def _cache_data(func=None, **kwargs):
            if func is None:
                def wrapper(inner):
                    return inner
                return wrapper
            return func

        st.cache_data = _cache_data

        # column_config 子模块：NumberColumn 在真实 streamlit 里返回列描述对象，
        # 测试中只需返回一个占位对象即可。
        column_config = types.ModuleType("streamlit.column_config")
        column_config.NumberColumn = lambda *a, **kw: object()
        st.column_config = column_config
        return st


class _MetricColumn:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def metric(self, *args, **kwargs):
        return None


APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parents[1]
APP_PATH = APP_DIR / "app.py"
FIXTURE_PATH = (
    ROOT
    / "packages"
    / "fundamentalscreener"
    / "tests"
    / "fixtures"
    / "minimal_market.json"
)


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


def _make_ok_result(app_module):
    """构造一个 status=ok 的 FrontendSnapshotResult，内部使用 fixture 快照。"""

    from services.data_service import FrontendSnapshotResult, load_snapshot

    snapshot = load_snapshot(str(FIXTURE_PATH))
    return FrontendSnapshotResult(snapshot=snapshot, status="ok")


class AppSmokeTests(unittest.TestCase):
    def test_main_runs_with_cached_data(self) -> None:
        """页面启动时读取缓存，正常渲染板块表。"""

        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: _make_ok_result(app)

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertEqual(recorder.errors, [], f"errors: {recorder.errors}")
        self.assertTrue(
            recorder.dataframes,
            "expected at least one dataframe rendered for sectors",
        )

    def test_no_cache_shows_empty_state(self) -> None:
        """无缓存时展示空状态，不渲染表格。"""

        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        from services.data_service import FrontendSnapshotResult

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: FrontendSnapshotResult(
            status="no_cache", message="暂无本地数据，请点击获取数据。"
        )

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertTrue(recorder.infos, "expected info message for no_cache")
        self.assertFalse(recorder.dataframes, "should not render tables for no_cache")
        self.assertEqual(recorder.errors, [])

    def test_refresh_failed_shows_old_cache_and_warning(self) -> None:
        """刷新失败但有旧缓存时展示旧缓存和失败提示。"""

        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        from services.data_service import FrontendSnapshotResult

        snapshot_result = _make_ok_result(app)
        app.load_or_refresh_snapshot = lambda refresh=False, **kw: FrontendSnapshotResult(
            snapshot=snapshot_result.snapshot,
            status="refresh_failed",
            message="数据刷新失败，展示最近可用缓存：connection error",
        )

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertTrue(recorder.warnings, "expected warning for refresh_failed")
        self.assertTrue(recorder.dataframes, "should render tables from old cache")
        self.assertEqual(recorder.errors, [])

    def test_ui_does_not_expose_fixture_sqlite_or_path(self) -> None:
        """UI 不出现 fixture、SQLite、数据库路径（docs §2.4）。"""

        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: _make_ok_result(app)

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        # 不应有 radio 调用（旧版数据源 radio 已删除）
        self.assertEqual(recorder.radio_calls, [], f"radio should not be used: {recorder.radio_calls}")

        # 不应有包含 fixture / SQLite / 路径 的 text_input
        forbidden = ["fixture", "sqlite", "路径", "path", "数据库", "database"]
        for label in recorder.text_input_calls:
            for word in forbidden:
                self.assertNotIn(
                    word.lower(),
                    label.lower(),
                    f"text_input label should not contain '{word}': {label}",
                )

    def test_ui_exposes_analysis_date_control(self) -> None:
        """侧边栏应暴露分析日期控件（docs §2.5 侧边栏保留项）。"""

        recorder = _Recorder()
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: _make_ok_result(app)

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        self.assertTrue(
            any(
                "分析日期" in label or "analysis date" in label.lower()
                for label in recorder.date_input_calls
            ),
            f"expected analysis date input, got {recorder.date_input_calls}",
        )

    def test_row_selection_drives_sector_drill_down(self) -> None:
        """模拟用户点击板块表第二行，应直接下钻到对应板块且不再走下拉回退。"""

        recorder = _Recorder(selection_rows=[1])
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: _make_ok_result(app)

        detail_calls: List[Dict[str, Any]] = []
        original_build_sector_detail = app.build_sector_detail

        def _spy(snapshot, sector_id, **kwargs):
            detail_calls.append({"sector_id": sector_id, "kwargs": kwargs})
            return original_build_sector_detail(snapshot, sector_id, **kwargs)

        app.build_sector_detail = _spy

        # 拿到板块表当前顺序，用于断言行 1 = 第二行。
        ok_result = _make_ok_result(app)
        board = app.build_sector_board(
            ok_result.snapshot,
            sort=app.DEFAULT_SECTOR_SORT,
            periods=app.DEFAULT_PERIODS,
            top=10,
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
        self.assertFalse(
            any("下拉" in label for label in recorder.selectbox_calls),
            f"selectbox fallback should not fire when row is selected, got {recorder.selectbox_calls}",
        )


class SectorDetailFailureDisplayTests(unittest.TestCase):
    """§15.9.4b: 板块详情按需加载失败时 UI 应展示 warning，不静默吞掉。

    覆盖 refresh_sector_detail 返回 no_cache / invalid / refresh_failed 三种
    失败状态。通过 mock build_sector_detail 返回空公司列表触发 refresh 路径，
    再 mock refresh_sector_detail 返回指定失败状态，断言 st.warning 被调用。
    """

    def _run_with_detail_failure(self, detail_status: str, detail_message: str):
        """通用脚手架：返回 ok 快照但板块详情为空，触发 refresh 后返回指定失败。

        返回 (app, recorder) 供调用方做进一步断言。
        """

        recorder = _Recorder(selection_rows=[0])
        try:
            app = _load_app(recorder)
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return None, None

        app.load_or_refresh_snapshot = lambda refresh=False, **kw: _make_ok_result(app)

        # build_sector_detail 返回空公司 → 触发 refresh_sector_detail 调用
        def _empty_detail(snapshot, sector_id, **kwargs):
            from services.data_service import SectorDetailData

            return SectorDetailData(sector_id=sector_id, sector_name="", companies=[])

        app.build_sector_detail = _empty_detail

        # refresh_sector_detail 返回指定失败状态
        from services.data_service import SectorDetailData, SectorDetailResult

        def _fake_refresh(sector_id, **kw):
            return SectorDetailResult(
                detail=SectorDetailData(sector_id=sector_id, sector_name="", companies=[]),
                status=detail_status,
                message=detail_message,
            )

        app.refresh_sector_detail = _fake_refresh

        try:
            app.main()
        except ModuleNotFoundError as exc:
            self.skipTest(f"missing dependency: {exc}")
            return None, None

        return app, recorder

    def test_detail_no_cache_shows_warning(self) -> None:
        """成分股同步失败且无旧缓存 → no_cache → st.warning 展示失败原因。"""

        app, recorder = self._run_with_detail_failure(
            "no_cache", "板块详情刷新失败且无可用成分股数据。原因：constituents failure"
        )
        if app is None:
            return
        self.assertTrue(
            any("constituents failure" in w for w in recorder.warnings),
            f"expected warning for no_cache, got: {recorder.warnings}",
        )

    def test_detail_invalid_shows_warning(self) -> None:
        """质量检查阻断 → invalid → st.warning 展示阻断原因。"""

        app, recorder = self._run_with_detail_failure(
            "invalid", "data_quality_status is 'invalid': quality check blocked"
        )
        if app is None:
            return
        self.assertTrue(
            any("invalid" in w or "quality" in w for w in recorder.warnings),
            f"expected warning for invalid, got: {recorder.warnings}",
        )

    def test_detail_refresh_failed_shows_warning(self) -> None:
        """成分股同步失败但有旧缓存 → refresh_failed → st.warning 展示失败原因。"""

        app, recorder = self._run_with_detail_failure(
            "refresh_failed", "板块数据刷新失败，展示最近可用缓存：network error"
        )
        if app is None:
            return
        self.assertTrue(
            any("network error" in w for w in recorder.warnings),
            f"expected warning for refresh_failed, got: {recorder.warnings}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
