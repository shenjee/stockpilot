"""Fundamental Screener Streamlit MVP (Phase 7 产品化前端)。

Edge of responsibility (docs §18/§19)：
- 只调用 ``services/data_service.py`` 的产品级函数，禁止在此重排序/重评分/重检测。
- 仅完成"板块 -> 公司 -> 财务/估值/异常 flags"的浏览。
- 不输出研报、不输出买卖建议、不预测板块。
- 用户界面不出现 fixture、SQLite、数据库路径或 CLI 参数。

启动方式::

    source ~/.venvs/czsc/bin/activate
    streamlit run apps/fundamental-screener/app.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fundamentalscreener.config import (  # noqa: E402
    DEFAULT_PERIODS,
    DEFAULT_SECTOR_SORT,
)
from services.data_service import (  # noqa: E402
    build_sector_board,
    build_sector_detail,
    collect_company_flags,
    companies_to_rows,
    financials_to_rows,
    get_latest_cached_date,
    load_or_refresh_snapshot,
    refresh_sector_detail,
    sectors_to_rows,
    valuations_to_rows,
)


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
        "ths_industry": {"zh": "同花顺行业", "en": "THS Industry"},
        "em_industry": {"zh": "东方财富行业", "en": "EM Industry"},
        "concept": {"zh": "概念板块", "en": "Concept"},
        "industry": {"zh": "行业板块", "en": "Industry"},
        "index": {"zh": "指数板块", "en": "Index"},
    },
}


# 基准指数代码 → 可读名称。
_BENCHMARK_LABELS: Dict[str, Dict[str, str]] = {
    "hs300": {"zh": "沪深300", "en": "CSI 300"},
    "sse": {"zh": "上证综指", "en": "SSE Composite"},
    "szse": {"zh": "深证成指", "en": "SZSE Component"},
    "chinext": {"zh": "创业板指", "en": "ChiNext"},
    "star50": {"zh": "科创50", "en": "STAR 50"},
}

# 数据质量状态 → 可读描述。
_QUALITY_STATUS_LABELS: Dict[str, Dict[str, str]] = {
    "ok": {"zh": "完整", "en": "Complete"},
    "degraded": {"zh": "部分缺失", "en": "Partial"},
    "stale": {"zh": "可能过期", "en": "Stale"},
    "invalid": {"zh": "不可用", "en": "Invalid"},
}

# 数据来源标识 → 可读名称。
_SOURCE_LABELS: Dict[str, Dict[str, str]] = {
    "akshare_ths": {"zh": "AkShare（同花顺）", "en": "AkShare (THS)"},
    "akshare_em": {"zh": "AkShare（东方财富）", "en": "AkShare (EM)"},
    "akshare": {"zh": "AkShare", "en": "AkShare"},
    "sina": {"zh": "新浪财经", "en": "Sina Finance"},
}

# source_set 角色键 → 可读名称。
_SOURCE_ROLE_LABELS: Dict[str, Dict[str, str]] = {
    "benchmark": {"zh": "基准指数", "en": "Benchmark"},
    "sector": {"zh": "板块行情", "en": "Sector"},
    "financial": {"zh": "财务数据", "en": "Financials"},
    "valuation": {"zh": "估值数据", "en": "Valuation"},
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


def _benchmark_label(benchmark_id: str, lang: str) -> str:
    """基准代码 → 可读名称（如 hs300 → 沪深300）。"""

    entry = _BENCHMARK_LABELS.get(benchmark_id)
    if entry:
        return entry.get(lang) or entry["zh"]
    return benchmark_id


def _quality_status_label(status: str, lang: str) -> str:
    """数据质量状态 → 可读描述（如 degraded → 部分缺失）。"""

    entry = _QUALITY_STATUS_LABELS.get(status)
    if entry:
        return entry.get(lang) or entry["zh"]
    return status


def _source_set_text(source_set: Dict[str, str], lang: str) -> str:
    """把 source_set 翻译成一句话描述，如"全部来自 AkShare（同花顺）"。"""

    if not source_set:
        return "-"
    # 收集所有去重后的来源名称
    sources = sorted(set(source_set.values()))
    parts = []
    for src in sources:
        entry = _SOURCE_LABELS.get(src)
        name = (entry.get(lang) or entry["zh"]) if entry else src
        roles = [k for k, v in source_set.items() if v == src]
        role_names = [
            (_SOURCE_ROLE_LABELS.get(r, {}).get(lang) or _SOURCE_ROLE_LABELS.get(r, {}).get("zh") or r)
            for r in roles
        ]
        parts.append(f"{name}（{'、'.join(role_names)}）")
    return "；".join(parts)


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
# 百分比格式化
# ---------------------------------------------------------------------------

# 比率字段（0-1 小数），展示时格式化为百分比字符串（如 0.0368 → "3.68%"）。
_PERCENT_FIELDS: set = {
    "return_1d", "return_5d", "return_20d", "return_60d",
    "relative_return", "turnover_amount_change",
    "market_turnover_share", "rising_stock_ratio",
    "turnover_rate",
    "revenue_yoy", "net_profit_yoy", "deducted_net_profit_yoy",
    "gross_margin", "net_margin", "roe",
    "operating_cashflow_to_profit",
    "debt_to_asset", "interest_bearing_debt_ratio",
    "accounts_receivable_yoy", "inventory_yoy",
    "dividend_yield", "pe_percentile", "pb_percentile",
}


def _to_display_rows(
    rows: List[Dict[str, Any]], lang: str
) -> List[Dict[str, Any]]:
    """翻译列名/枚举值，供 st.dataframe 直接展示。

    比率字段保持数值类型（0-1 小数），由 ``_percent_column_config`` 通过
    ``st.column_config.NumberColumn(format="percent")`` 负责展示为百分比；
    这样表格点击列头排序时按数值而非字符串比较。``None`` 原样保留。
    """

    return _localize_rows(rows, lang)


def _percent_column_config(lang: str) -> Dict[str, Any]:
    """为所有百分比字段生成 ``column_config``，按数值排序、按百分比展示。"""

    return {
        _label(field, lang): st.column_config.NumberColumn(format="percent")
        for field in _PERCENT_FIELDS
    }


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
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"] { display: none; }
        .block-container { padding-top: 0.75rem; padding-bottom: 2rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] { padding-top: 0.25rem; }
        section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
            min-height: 0px !important;
            height: 0px !important;
            margin-bottom: 4px !important;
            align-items: flex-start !important;
        }
        .page-title { font-size: 1.2rem; font-weight: 700; line-height: 1.25; margin: 0 0 0.25rem 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ----------------- 侧边栏：产品参数 -----------------
    with st.sidebar:
        # 分析日期：默认展示缓存中最新可用交易日，用户可自由调整。
        default_date_str = get_latest_cached_date()
        if default_date_str:
            default_date = date.fromisoformat(default_date_str)
        else:
            default_date = date.today()
        picked = st.date_input(
            _t("分析日期", "Analysis date"),
            value=default_date,
        )
        analysis_date_str = picked.isoformat()

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
        refresh_clicked = st.button(
            _t("分析", "Analyze"),
            type="primary",
            use_container_width=True,
            help=_t(
                "从同花顺行业板块同步最新数据并运行分析。",
                "Sync latest data from THS industry sectors and run analysis.",
            ),
        )
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

    st.markdown(
        f'<div class="page-title">{_t("基本面量化工作台", "Fundamental Screener")}</div>',
        unsafe_allow_html=True,
    )

    result = load_or_refresh_snapshot(
        refresh=refresh_clicked,
        analysis_date=analysis_date_str,
    )

    # ----------------- 状态处理 -----------------
    if result.status == "no_cache":
        st.info(
            result.message
            or _t(
                "暂无本地数据，请点击上方按钮获取数据。",
                "No local data. Click the button above to fetch.",
            )
        )
        return

    if result.status == "invalid":
        st.error(
            result.message
            or _t("数据质量不可用，无法生成快照。", "Data quality invalid; cannot build snapshot.")
        )
        if result.quality_report is not None:
            issues = [i.to_dict() for i in result.quality_report.issues]
            with st.expander(
                _t(
                    f"质量问题（{len(issues)}）",
                    f"Quality Issues ({len(issues)})",
                ),
                expanded=True,
            ):
                _render_quality_issue_list(issues)
        return

    snapshot = result.snapshot
    if snapshot is None:
        st.error(_t("加载数据失败。", "Failed to load data."))
        return

    # 刷新失败但有旧缓存：展示旧缓存 + 失败提示
    if result.status == "refresh_failed":
        st.warning(result.message)

    # degraded / stale 质量提示
    if result.status == "stale":
        st.warning(
            _t(
                "数据可能过期，请考虑刷新。",
                "Data may be stale. Consider refreshing.",
            )
        )

    board = build_sector_board(
        snapshot,
        sort=DEFAULT_SECTOR_SORT,
        periods=DEFAULT_PERIODS,
        top=int(sector_top),
        metadata=result.metadata,
        quality_report=result.quality_report,
    )

    # ----------------- 顶部信息：单行小字体 -----------------
    quality_text = (
        _quality_status_label(board.data_quality_status, lang)
        if board.data_quality_status
        else "-"
    )
    info_parts = [
        f"{_t('日期', 'Date')}：{board.date or '-'}",
        f"{_t('分类', 'Classification')}：{_enum_value('classification_system', board.classification_system, lang) or '-'}",
        f"{_t('基准', 'Benchmark')}：{_benchmark_label(board.benchmark_id, lang)}",
        f"{_t('板块', 'Sectors')}：{_t(f'前{len(board.sectors)}', f'Top {len(board.sectors)}')}",
        f"{_t('数据质量', 'Quality')}：{quality_text}",
    ]
    st.caption("　｜　".join(info_parts))
    if board.source_set:
        st.caption(
            f"{_t('数据来源', 'Data source')}：{_source_set_text(board.source_set, lang)}"
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
    st.markdown(
        f'<div class="page-title">{_t("板块归一化走势", "Normalized Sector Curves")}</div>',
        unsafe_allow_html=True,
    )
    chart_df = _chart_dataframe(board.chart_series)
    if chart_df is None or chart_df.empty:
        _empty_message(_t("当前没有可用的板块走势数据。", "No sector chart data available."))
    else:
        st.line_chart(chart_df)

    st.markdown(
        f'<div class="page-title">{_t("板块指标", "Sector Metrics")}</div>',
        unsafe_allow_html=True,
    )
    sector_rows = sectors_to_rows(board.sectors)
    if not sector_rows:
        _empty_message(_t("当前选择没有板块。", "No sectors under current selection."))
        return

    st.caption(_t("点击表格行可下钻到板块详情。", "Click a row to drill into a sector."))
    selection = st.dataframe(
        _to_display_rows(sector_rows, lang),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config=_percent_column_config(lang),
        key="sector_table",
    )

    # 表格点击优先；未选中时默认取第一个板块，保证首次进入页面有默认板块。
    selected_sector_id: Optional[str] = None
    try:
        selected_rows = selection.selection.rows  # type: ignore[attr-defined]
    except AttributeError:
        selected_rows = []
    if selected_rows:
        idx = int(selected_rows[0])
        if 0 <= idx < len(board.sectors):
            selected_sector_id = board.sectors[idx].sector_id

    if selected_sector_id is None and board.sectors:
        selected_sector_id = board.sectors[0].sector_id

    # §15.9: 按需加载 — 先从当前快照构建详情，成分股为空或缺少个股日线行情时
    # 触发该板块的重量层同步。后者发生在首屏轻量同步已写入成分股、但未抓取
    # company_daily_snapshot 的场景：此时 companies 非空但 market_cap /
    # turnover_amount 全为 None。
    detail = build_sector_detail(
        snapshot,
        selected_sector_id,
        company_sort="combined_score",
        top=int(company_top),
    )
    _needs_detail_refresh = not detail.companies or all(
        c.market_cap is None and c.turnover_amount is None
        for c in detail.companies
    )
    if _needs_detail_refresh:
        with st.spinner(_t("正在加载板块详情...", "Loading sector detail...")):
            detail_result = refresh_sector_detail(
                selected_sector_id,
                analysis_date=snapshot.date,
                company_sort="combined_score",
                top=int(company_top),
            )
        if detail_result.detail is not None:
            detail = detail_result.detail
        # §15.9.4b: 展示详情层失败原因，不静默吞掉 no_cache / invalid。
        # refresh_sector_detail 在成分股同步失败且无旧缓存时返回 no_cache，
        # 旧代码只处理 refresh_failed，导致 no_cache 静默落到"没有公司数据"。
        if detail_result.status in ("refresh_failed", "no_cache", "invalid"):
            st.warning(detail_result.message)

    st.markdown(
        f'<div class="page-title">{_t(f"公司排名 — {detail.sector_name}", f"Company Ranking — {detail.sector_name}")}</div>',
        unsafe_allow_html=True,
    )
    company_rows = companies_to_rows(detail.companies)
    if not company_rows:
        _empty_message(_t("该板块当前没有公司数据。", "No companies in this sector."))
    else:
        st.dataframe(
            _to_display_rows(company_rows, lang),
            use_container_width=True,
            column_config=_percent_column_config(lang),
        )

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
            st.dataframe(
                _to_display_rows(fin_rows, lang),
                use_container_width=True,
                column_config=_percent_column_config(lang),
            )

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
            st.dataframe(
                _to_display_rows(val_rows, lang),
                width="stretch",
                column_config=_percent_column_config(lang),
            )

    with flag_tab:
        flag_rows = collect_company_flags(
            detail.companies, detail.financials, detail.valuations
        )
        if not flag_rows:
            _empty_message(_t("该板块当前没有标记数据。", "No flags for this sector."))
        else:
            st.dataframe(
                _to_display_rows(flag_rows, lang),
                use_container_width=True,
                column_config=_percent_column_config(lang),
            )

    if detail.warnings:
        with st.expander(
            _t("板块详情警告", "Sector Detail Warnings"), expanded=False
        ):
            for w in detail.warnings:
                st.warning(w)


if __name__ == "__main__":  # pragma: no cover
    main()
