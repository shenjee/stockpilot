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
from fundamentalscreener.lineage import now_cn  # noqa: E402
from services.data_service import (  # noqa: E402
    build_sector_board,
    build_sector_detail,
    collect_company_flags,
    companies_to_rows,
    financials_to_rows,
    load_snapshot,
    load_snapshot_from_db,
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
DEFAULT_DB = ROOT / "stockpilot" / "db" / "fundamental_data.sqlite"


# ---------------------------------------------------------------------------
# i18n：列名 + 枚举值的中英文映射
#
# 默认显示中文，可在侧边栏切换为 English。本模块只做"可读文字"映射，
# 不会改变 row 的顺序或语义；下钻仍然依赖 board.sectors 列表索引。
# ---------------------------------------------------------------------------


# 所有表格共享的字段标签：snake_case -> {zh, en}。
_FIELD_LABELS: Dict[str, Dict[str, str]] = {
    # ---- 通用 ----
    "code": {"zh": "代码", "en": "Code"},
    "name": {"zh": "名称", "en": "Name"},
    "warnings": {"zh": "警告", "en": "Warnings"},
    # ---- SectorEntry ----
    "sector_id": {"zh": "板块代码", "en": "Sector ID"},
    "sector_name": {"zh": "板块名称", "en": "Sector"},
    "classification_system": {"zh": "分类口径", "en": "Classification"},
    "return_1d": {"zh": "近1日涨跌幅", "en": "Return 1d"},
    "return_5d": {"zh": "近5日涨跌幅", "en": "Return 5d"},
    "return_20d": {"zh": "近20日涨跌幅", "en": "Return 20d"},
    "return_60d": {"zh": "近60日涨跌幅", "en": "Return 60d"},
    "relative_return": {"zh": "相对基准收益", "en": "Relative Return"},
    "turnover_amount_change": {"zh": "成交额变化", "en": "Turnover Change"},
    "market_turnover_share": {"zh": "成交额占比", "en": "Market Turnover Share"},
    "rising_stock_ratio": {"zh": "上涨家数占比", "en": "Rising Stock Ratio"},
    "rank_change_5d": {"zh": "5日排名变化", "en": "Rank Change 5d"},
    "state": {"zh": "板块状态", "en": "State"},
    "score": {"zh": "评分", "en": "Score"},
    # ---- CompanyEntry ----
    "market_cap": {"zh": "市值", "en": "Market Cap"},
    "turnover_amount": {"zh": "成交额", "en": "Turnover Amount"},
    "turnover_rate": {"zh": "换手率", "en": "Turnover Rate"},
    "sector_return_rank": {"zh": "板块收益排名", "en": "Sector Return Rank"},
    "leader_score": {"zh": "龙头分", "en": "Leader Score"},
    "attention_score": {"zh": "关注度分", "en": "Attention Score"},
    "financial_quality_score": {"zh": "财务质量分", "en": "Financial Score"},
    "valuation_score": {"zh": "估值分", "en": "Valuation Score"},
    "combined_score": {"zh": "综合分", "en": "Combined Score"},
    "group": {"zh": "候选分组", "en": "Group"},
    "flags": {"zh": "标记", "en": "Flags"},
    # ---- FinancialEntry ----
    "revenue_yoy": {"zh": "营收同比", "en": "Revenue YoY"},
    "net_profit_yoy": {"zh": "净利润同比", "en": "Net Profit YoY"},
    "deducted_net_profit_yoy": {"zh": "扣非净利同比", "en": "Adj. Net Profit YoY"},
    "gross_margin": {"zh": "毛利率", "en": "Gross Margin"},
    "net_margin": {"zh": "净利率", "en": "Net Margin"},
    "roe": {"zh": "ROE", "en": "ROE"},
    "operating_cashflow_to_profit": {"zh": "经营现金/利润", "en": "OCF / Net Profit"},
    "free_cashflow": {"zh": "自由现金流", "en": "Free Cash Flow"},
    "debt_to_asset": {"zh": "资产负债率", "en": "Debt / Asset"},
    "interest_bearing_debt_ratio": {"zh": "有息负债率", "en": "Interest-bearing Debt Ratio"},
    "accounts_receivable_yoy": {"zh": "应收账款同比", "en": "Receivables YoY"},
    "inventory_yoy": {"zh": "存货同比", "en": "Inventory YoY"},
    "abnormal_flags": {"zh": "财务异常标记", "en": "Financial Flags"},
    # ---- ValuationEntry ----
    "pe": {"zh": "市盈率 PE", "en": "PE"},
    "pb": {"zh": "市净率 PB", "en": "PB"},
    "ps": {"zh": "市销率 PS", "en": "PS"},
    "peg": {"zh": "PEG", "en": "PEG"},
    "dividend_yield": {"zh": "股息率", "en": "Dividend Yield"},
    "pe_percentile": {"zh": "PE 分位", "en": "PE Percentile"},
    "pb_percentile": {"zh": "PB 分位", "en": "PB Percentile"},
    "industry_valuation_position": {"zh": "行业估值位置", "en": "Industry Valuation Position"},
    "label": {"zh": "估值标签", "en": "Valuation Label"},
    # ---- Flags 汇总表 ----
    "company_flags": {"zh": "公司标记", "en": "Company Flags"},
    "financial_flags": {"zh": "财务异常标记", "en": "Financial Flags"},
    "valuation_label": {"zh": "估值标签", "en": "Valuation Label"},
}


# 枚举值映射：按字段名 -> 原始值 -> {zh, en}。原值若不在表内则原样保留。
_ENUM_LABELS: Dict[str, Dict[str, Dict[str, str]]] = {
    "state": {
        "overheated": {"zh": "过热", "en": "Overheated"},
        "strong": {"zh": "强势", "en": "Strong"},
        "low_level_active": {"zh": "低位活跃", "en": "Low-level Active"},
        "improving": {"zh": "改善中", "en": "Improving"},
        "neutral": {"zh": "中性", "en": "Neutral"},
    },
    "group": {
        "priority": {"zh": "优选", "en": "Priority"},
        "watch": {"zh": "观察", "en": "Watch"},
        "cautious": {"zh": "谨慎", "en": "Cautious"},
    },
    "label": {
        "cheap": {"zh": "便宜", "en": "Cheap"},
        "fair": {"zh": "合理", "en": "Fair"},
        "expensive": {"zh": "偏贵", "en": "Expensive"},
        "not_applicable": {"zh": "不适用", "en": "Not Applicable"},
    },
    "valuation_label": {
        "cheap": {"zh": "便宜", "en": "Cheap"},
        "fair": {"zh": "合理", "en": "Fair"},
        "expensive": {"zh": "偏贵", "en": "Expensive"},
        "not_applicable": {"zh": "不适用", "en": "Not Applicable"},
    },
    "industry_valuation_position": {
        "high": {"zh": "高位", "en": "High"},
        "mid": {"zh": "中位", "en": "Mid"},
        "low": {"zh": "低位", "en": "Low"},
    },
    "classification_system": {
        "concept": {"zh": "概念板块", "en": "Concept"},
        "industry": {"zh": "行业板块", "en": "Industry"},
        "index": {"zh": "指数板块", "en": "Index"},
    },
}


# 语言选项：界面默认中文，用户切换后整页跟随。
_LANG_LABELS = {"zh": "中文", "en": "English"}


def _t(zh: str, en: str) -> str:
    """按当前界面语言返回文案；默认中文。"""

    return zh if st.session_state.get("lang_idx", "zh") == "zh" else en


def _label(field: str, lang: str) -> str:
    """字段名 -> 显示标签。未登记字段回退原 snake_case。"""

    entry = _FIELD_LABELS.get(field)
    if not entry:
        return field
    return entry.get(lang) or entry.get("zh") or field


def _enum_value(field: str, value: Any, lang: str) -> Any:
    """枚举值翻译。非字符串 / 未登记 / None 一律原样返回。"""

    if value is None or not isinstance(value, str):
        return value
    table = _ENUM_LABELS.get(field)
    if not table:
        return value
    mapped = table.get(value)
    if not mapped:
        return value
    return mapped.get(lang) or mapped.get("zh") or value


def _localize_rows(
    rows: List[Dict[str, Any]], lang: str
) -> List[Dict[str, Any]]:
    """把一组 row 的 key/枚举值都翻成 ``lang``，list 字段元素逐个翻。"""

    localized: List[Dict[str, Any]] = []
    for row in rows:
        new_row: Dict[str, Any] = {}
        for key, value in row.items():
            new_key = _label(key, lang)
            if isinstance(value, list):
                new_row[new_key] = [_enum_value(key, v, lang) for v in value]
            else:
                new_row[new_key] = _enum_value(key, value, lang)
        localized.append(new_row)
    return localized


# ---------------------------------------------------------------------------
# 缓存：fixture 读取一次即可
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _cached_load(fixture_path: str):
    return load_snapshot(fixture_path)


@st.cache_data(show_spinner=False)
def _cached_load_db(
    db_path: str,
    analysis_date: str,
    classification_system: str,
    benchmark: str,
):
    """缓存 SQLite 数据源加载结果（Phase 7 真实数据入口）。"""

    return load_snapshot_from_db(
        db_path, analysis_date, classification_system, benchmark
    )


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


def _render_quality_issue_list(issues: List[Dict[str, Any]]) -> None:
    """渲染质量问题列表：error→红色，warning→黄色，info→蓝色。"""

    for issue in issues:
        level = issue.get("level", "")
        code = issue.get("code", "")
        msg = issue.get("message", "")
        entity = issue.get("entity_id") or issue.get("entity_type") or ""
        label = f"[{code}]{f' {entity}' if entity else ''} {msg}"
        if level == "error":
            st.error(label)
        elif level == "warning":
            st.warning(label)
        else:
            st.info(label)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="基本面量化工作台", layout="wide")

    # ----------------- 侧边栏：语言 + 数据源 + 板块排序 -----------------
    with st.sidebar:
        lang = st.selectbox(
            _t("界面语言", "Display language"),
            options=list(_LANG_LABELS.keys()),
            format_func=lambda x: _LANG_LABELS[x],
            key="lang_idx",
            help=_t(
                "表格列名与枚举值的显示语言；不影响底层数据。",
                "Display language for labels and enum values; does not affect data.",
            ),
        )

        st.header(_t("数据源", "Data Source"))
        data_source = st.radio(
            _t("数据源", "Data source"),
            options=["fixture", "sqlite"],
            format_func=lambda x: (
                _t("Fixture（示例数据）", "Fixture (sample)")
                if x == "fixture"
                else _t("SQLite（真实数据）", "SQLite (real)")
            ),
            horizontal=True,
            help=_t(
                "fixture: Phase 0-5 示例数据；sqlite: Phase 6 真实数据缓存（需先跑 sync）。",
                "fixture: Phase 0-5 sample data; sqlite: Phase 6 real cache (run sync first).",
            ),
        )

        fixture_path = ""
        db_path = ""
        analysis_date = ""
        if data_source == "fixture":
            fixture_path = st.text_input(
                _t("Fixture JSON 路径", "Fixture JSON path"),
                value=str(DEFAULT_FIXTURE),
                help=_t("与 CLI --fixture 参数一致。", "Same as CLI --fixture."),
            )
        else:
            db_path = st.text_input(
                _t("SQLite 数据库路径", "SQLite database path"),
                value=str(DEFAULT_DB),
                help=_t(
                    "Phase 6 sync 写入的 fundamental_data.sqlite，与 CLI --db 一致。",
                    "fundamental_data.sqlite written by Phase 6 sync; same as CLI --db.",
                ),
            )
            analysis_date = st.text_input(
                _t("分析日期", "Analysis date"),
                value="",
                placeholder=_t("YYYY-MM-DD，留空取今天", "YYYY-MM-DD, blank for today"),
                help=_t(
                    "所有时变数据按此日期截断（point-in-time）。",
                    "All time-variant data is cut off at this date (point-in-time).",
                ),
            )
        sort_field = st.selectbox(
            _t("板块排序字段", "Sector sort field"),
            options=list(SUPPORTED_SECTOR_SORTS),
            index=list(SUPPORTED_SECTOR_SORTS).index(DEFAULT_SECTOR_SORT),
        )
        sector_top = st.number_input(
            _t("板块 Top N", "Sector Top N"),
            min_value=1,
            max_value=200,
            value=10,
            step=1,
        )
        company_top = st.number_input(
            _t("板块内公司 Top N", "Companies Top N per sector"),
            min_value=1,
            max_value=50,
            value=5,
            step=1,
        )

    st.title(_t("基本面量化工作台", "Fundamental Screener"))

    # ----------------- 数据加载 -----------------
    metadata = None
    quality_report = None

    if data_source == "fixture":
        try:
            snapshot = _cached_load(fixture_path)
        except FileNotFoundError as exc:
            st.error(f"fixture_not_found: {exc}")
            return
        except Exception as exc:  # pragma: no cover - 兜底
            st.error(f"fixture_load_failed: {exc}")
            return
    else:
        # SQLite 真实数据源（Phase 7）
        if not db_path.strip():
            st.error(
                _t(
                    "请输入 SQLite 数据库路径。",
                    "Please enter the SQLite database path.",
                )
            )
            return
        resolved_date = analysis_date.strip() or now_cn().date().isoformat()
        try:
            load_result = _cached_load_db(
                db_path, resolved_date, "em_industry", "hs300"
            )
        except Exception as exc:  # pragma: no cover - 兜底
            st.error(f"sqlite_load_failed: {exc}")
            return

        if load_result.quality_error:
            # 质量状态 invalid：展示阻断原因 + 质量报告后退出
            st.error(
                _t(
                    f"数据质量不可用（invalid），无法生成快照：{load_result.quality_error}",
                    f"Data quality invalid; cannot build snapshot: {load_result.quality_error}",
                )
            )
            if load_result.quality_report is not None:
                issues = [i.to_dict() for i in load_result.quality_report.issues]
                with st.expander(
                    _t(
                        f"质量问题（{len(issues)}）",
                        f"Quality Issues ({len(issues)})",
                    ),
                    expanded=True,
                ):
                    _render_quality_issue_list(issues)
            return
        if load_result.snapshot is None:
            st.error("sqlite_load_failed: snapshot is None")
            return
        snapshot = load_result.snapshot
        metadata = load_result.metadata
        quality_report = load_result.quality_report

    board = build_sector_board(
        snapshot,
        sort=sort_field,
        periods=DEFAULT_PERIODS,
        top=int(sector_top),
        metadata=metadata,
        quality_report=quality_report,
    )

    # ----------------- 顶部信息卡 -----------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(_t("日期", "Date"), board.date or "-")
    c2.metric(
        _t("分类口径", "Classification"),
        _enum_value("classification_system", board.classification_system, lang)
        or "-",
    )
    c3.metric(_t("基准", "Benchmark"), board.benchmark_name or board.benchmark_id or "-")
    c4.metric(_t("板块数", "Sectors"), len(board.sectors))

    # Phase 7: SQLite 数据源展示血缘与质量状态（docs §19 DoD）
    if board.data_quality_status:
        q1, q2, q3, q4 = st.columns(4)
        q1.metric(_t("数据质量", "Quality"), board.data_quality_status)
        q2.metric(_t("数据截止", "Data Cutoff"), board.data_cutoff or "-")
        q3.metric(
            _t("数据来源", "Sources"),
            ", ".join(f"{k}={v}" for k, v in board.source_set.items()) or "-",
        )
        q4.metric(
            _t("采集批次", "Fetch Run"),
            (board.fetch_run_id[:24] + "…")
            if len(board.fetch_run_id) > 24
            else (board.fetch_run_id or "-"),
        )

    if board.quality_issues:
        with st.expander(
            _t(
                f"质量问题（{len(board.quality_issues)}）",
                f"Quality Issues ({len(board.quality_issues)})",
            ),
            expanded=False,
        ):
            _render_quality_issue_list(board.quality_issues)

    if board.warnings:
        with st.expander(_t("板块层警告", "Sector Warnings"), expanded=False):
            for w in board.warnings:
                st.warning(w)

    # ----------------- 板块走势图 + 表格 -----------------
    st.subheader(_t("板块归一化走势", "Normalized Sector Curves"))
    chart_df = _chart_dataframe(board.chart_series)
    if chart_df is None or chart_df.empty:
        _empty_message(_t("当前没有可用的板块走势数据。", "No sector chart data available."))
    else:
        st.line_chart(chart_df)

    st.subheader(_t("板块指标", "Sector Metrics"))
    sector_rows = sectors_to_rows(board.sectors)
    if not sector_rows:
        _empty_message(_t("当前选择没有板块。", "No sectors under current selection."))
        return

    st.caption(_t("点击表格行可下钻到板块详情。", "Click a row to drill into a sector."))
    selection = st.dataframe(
        _localize_rows(sector_rows, lang),
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
            _t("或下拉选择板块", "Or pick a sector"),
            options=list(sector_labels.keys()),
        )
        selected_sector_id = sector_labels[fallback_label]

    detail = build_sector_detail(
        snapshot,
        selected_sector_id,
        company_sort="combined_score",
        top=int(company_top),
    )

    st.subheader(
        _t(
            f"公司排名 — {detail.sector_name}",
            f"Company Ranking — {detail.sector_name}",
        )
    )
    company_rows = companies_to_rows(detail.companies)
    if not company_rows:
        _empty_message(_t("该板块当前没有公司数据。", "No companies in this sector."))
    else:
        st.dataframe(_localize_rows(company_rows, lang), use_container_width=True)

    # ----------------- 财务 / 估值 / Flags -----------------
    fin_tab, val_tab, flag_tab = st.tabs(
        [
            _t("财务质量对比", "Financial Quality"),
            _t("估值对比", "Valuation"),
            _t("异常标记", "Flags"),
        ]
    )

    with fin_tab:
        fin_rows = financials_to_rows(detail.financials)
        if not fin_rows:
            _empty_message(
                _t(
                    "该板块当前没有财务数据，或公司排名为空。",
                    "No financials for this sector, or company ranking is empty.",
                )
            )
        else:
            st.dataframe(_localize_rows(fin_rows, lang), use_container_width=True)

    with val_tab:
        val_rows = valuations_to_rows(detail.valuations)
        if not val_rows:
            _empty_message(
                _t(
                    "该板块当前没有估值数据，或公司排名为空。",
                    "No valuations for this sector, or company ranking is empty.",
                )
            )
        else:
            st.dataframe(_localize_rows(val_rows, lang), use_container_width=True)

    with flag_tab:
        flag_rows = collect_company_flags(
            detail.companies, detail.financials, detail.valuations
        )
        if not flag_rows:
            _empty_message(_t("该板块当前没有标记数据。", "No flags for this sector."))
        else:
            st.dataframe(_localize_rows(flag_rows, lang), use_container_width=True)

    if detail.warnings:
        with st.expander(
            _t("板块详情警告", "Sector Detail Warnings"), expanded=False
        ):
            for w in detail.warnings:
                st.warning(w)


if __name__ == "__main__":  # pragma: no cover
    main()
