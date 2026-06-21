"""Fundamental Screener Streamlit MVP (Phase 6).

Edge of responsibility (docs §18)：
- 只调用 ``packages/fundamentalscreener`` core，禁止在此重排序/重评分/重检测。
- 仅完成"板块 -> 公司 -> 财务/估值/异常 flags"的浏览。
- 不输出研报、不输出买卖建议、不预测板块。

启动方式::

    source ~/.venvs/czsc/bin/activate
    streamlit run apps/fundamental-screener/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fundamentalscreener.config import (  # noqa: E402
    DEFAULT_PERIODS,
    DEFAULT_SECTOR_SORT,
    SUPPORTED_SECTOR_SORTS,
)
from services.data_service import (  # noqa: E402
    build_sector_board,
    build_sector_detail,
    collect_company_flags,
    companies_to_rows,
    financials_to_rows,
    load_snapshot,
    sectors_to_rows,
    valuations_to_rows,
)


DEFAULT_FIXTURE = (
    ROOT
    / "packages"
    / "fundamentalscreener"
    / "tests"
    / "fixtures"
    / "minimal_market.json"
)


# ---------------------------------------------------------------------------
# 缓存：fixture 读取一次即可
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _cached_load(fixture_path: str):
    return load_snapshot(fixture_path)


# ---------------------------------------------------------------------------
# UI 工具
# ---------------------------------------------------------------------------


def _chart_dataframe(chart_series: List[Dict[str, Any]]):
    """把 chart_series 转成 ``date -> {series_name: value}`` 的宽表。"""

    import pandas as pd  # 局部导入，避免测试环境强依赖 pandas

    rows: Dict[str, Dict[str, float]] = {}
    columns: List[str] = []
    for series in chart_series:
        name = series["series_name"] or series["series_id"]
        if name not in columns:
            columns.append(name)
        for point in series["points"]:
            rows.setdefault(point["date"], {})[name] = point["value"]

    if not rows:
        return None

    ordered_dates = sorted(rows.keys())
    data = {col: [rows[d].get(col) for d in ordered_dates] for col in columns}
    df = pd.DataFrame(data, index=ordered_dates)
    df.index.name = "date"
    return df


def _empty_message(text: str) -> None:
    st.info(text)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Fundamental Screener", layout="wide")
    st.title("基本面量化工作台 / Fundamental Screener")
    st.caption(
        "数据来自 packages/fundamentalscreener core；本页面只做浏览，不重复实现算法。"
    )

    # ----------------- 侧边栏：数据源 + 板块排序 -----------------
    with st.sidebar:
        st.header("数据源")
        fixture_path = st.text_input(
            "Fixture JSON 路径",
            value=str(DEFAULT_FIXTURE),
            help="Phase 6 默认从 fixture 读取，与 CLI --fixture 参数一致。",
        )
        sort_field = st.selectbox(
            "板块排序字段",
            options=list(SUPPORTED_SECTOR_SORTS),
            index=list(SUPPORTED_SECTOR_SORTS).index(DEFAULT_SECTOR_SORT),
        )
        sector_top = st.number_input(
            "板块 Top N",
            min_value=1,
            max_value=200,
            value=10,
            step=1,
        )
        company_top = st.number_input(
            "板块内公司 Top N",
            min_value=1,
            max_value=50,
            value=5,
            step=1,
        )

    # ----------------- 数据加载 -----------------
    try:
        snapshot = _cached_load(fixture_path)
    except FileNotFoundError as exc:
        st.error(f"fixture_not_found: {exc}")
        return
    except Exception as exc:  # pragma: no cover - 兜底
        st.error(f"fixture_load_failed: {exc}")
        return

    board = build_sector_board(
        snapshot,
        sort=sort_field,
        periods=DEFAULT_PERIODS,
        top=int(sector_top),
    )

    # ----------------- 顶部信息卡 -----------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("日期 / Date", board.date or "-")
    c2.metric("分类口径", board.classification_system)
    c3.metric("基准 / Benchmark", board.benchmark_name or board.benchmark_id or "-")
    c4.metric("板块数 / Sectors", len(board.sectors))

    if board.warnings:
        with st.expander("板块层 warnings", expanded=False):
            for w in board.warnings:
                st.warning(w)

    # ----------------- 板块走势图 + 表格 -----------------
    st.subheader("板块归一化走势 / Normalized Sector Curves")
    chart_df = _chart_dataframe(board.chart_series)
    if chart_df is None or chart_df.empty:
        _empty_message("当前 fixture 没有可用 chart_series。")
    else:
        st.line_chart(chart_df)

    st.subheader("板块指标 / Sector Metrics")
    sector_rows = sectors_to_rows(board.sectors)
    if not sector_rows:
        _empty_message("当前选择没有板块。")
        return

    st.caption("点击表格行可下钻到板块详情 / Click a row to drill into a sector.")
    selection = st.dataframe(
        sector_rows,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="sector_table",
    )

    # 表格点击优先；未选中时回退到下拉选择，保证首次进入页面有默认板块。
    selected_sector_id: Optional[str] = None
    try:
        selected_rows = selection.selection.rows  # type: ignore[attr-defined]
    except AttributeError:
        selected_rows = []
    if selected_rows:
        idx = int(selected_rows[0])
        if 0 <= idx < len(board.sectors):
            selected_sector_id = board.sectors[idx].sector_id

    if selected_sector_id is None:
        sector_labels = {
            f"{s.sector_name} ({s.sector_id})": s.sector_id for s in board.sectors
        }
        fallback_label = st.selectbox(
            "或下拉选择板块 / Or pick a sector",
            options=list(sector_labels.keys()),
        )
        selected_sector_id = sector_labels[fallback_label]

    detail = build_sector_detail(
        snapshot,
        selected_sector_id,
        company_sort="combined_score",
        top=int(company_top),
    )

    st.subheader(f"公司排名 / Company Ranking — {detail.sector_name}")
    company_rows = companies_to_rows(detail.companies)
    if not company_rows:
        _empty_message("该板块当前没有公司数据。")
    else:
        st.dataframe(company_rows, use_container_width=True)

    # ----------------- 财务 / 估值 / Flags -----------------
    fin_tab, val_tab, flag_tab = st.tabs(
        ["财务质量对比 / Financial Quality", "估值对比 / Valuation", "异常 flags / Flags"]
    )

    with fin_tab:
        fin_rows = financials_to_rows(detail.financials)
        if not fin_rows:
            _empty_message("该板块当前没有财务数据，或公司排名为空。")
        else:
            st.dataframe(fin_rows, use_container_width=True)

    with val_tab:
        val_rows = valuations_to_rows(detail.valuations)
        if not val_rows:
            _empty_message("该板块当前没有估值数据，或公司排名为空。")
        else:
            st.dataframe(val_rows, use_container_width=True)

    with flag_tab:
        flag_rows = collect_company_flags(
            detail.companies, detail.financials, detail.valuations
        )
        if not flag_rows:
            _empty_message("该板块当前没有 flag 数据。")
        else:
            st.dataframe(flag_rows, use_container_width=True)

    if detail.warnings:
        with st.expander("板块详情 warnings", expanded=False):
            for w in detail.warnings:
                st.warning(w)


if __name__ == "__main__":  # pragma: no cover
    main()
