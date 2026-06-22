"""AkShare 真实数据源实现（Phase 6B + 6C）。

板块层（Phase 6B，``em_industry`` 口径）：

- ``list_sectors``：``ak.stock_board_industry_name_em()``
- ``get_sector_constituents``：``ak.stock_board_industry_cons_em(symbol=BK 代码)``
- ``get_sector_daily``：``ak.stock_board_industry_hist_em(symbol=BK 代码, ...)``
- ``get_benchmark_daily``：``ak.index_zh_a_hist(symbol=指数代码, ...)``，至少支持 ``hs300``

公司层（Phase 6C）：

- ``get_stock_universe``：``ak.stock_info_a_code_name()``
- ``get_company_daily_snapshot``：``ak.stock_zh_a_spot_em()``
- ``get_company_valuation_history``：``ak.stock_zh_valuation_baidu(symbol, indicator, period)``
- ``get_financial_metrics``：``ak.stock_financial_analysis_indicator(symbol, start_year)``

所有时变数据按 ``analysis_date`` 截断：行情/估值使用 ``trade_date <= end_date``，
财务使用估算的 ``disclosure_date <= as_of_date``（监管最晚披露日）。

设计原则：
- 只负责读取外部数据并返回标准化前的 ``List[Dict]``，不写 SQLite、不做质量检查。
- ``akshare`` 采用**惰性导入**：模块导入不触发 ``import akshare``，只有在真正调用
  方法且未注入 ``akshare`` 时才导入。这样在没有安装 akshare 的环境下也能 import
  本模块、运行注入式测试。
- 支持**依赖注入**：构造时传入 ``akshare=<fake>`` 即可替换真实 akshare 模块，便于
  在无网络环境对转换逻辑做单元测试。真实网络同步作为手动 smoke，不是单元测试阻塞项。
- 返回行使用 ``snake_case``；缺失值用 ``None``，不用 ``0`` 占位。
- ``source`` / ``fetch_run_id`` / ``created_at`` / ``updated_at`` 由同步层 enricher
  填充，本数据源只提供 ``source_updated_at``（取抓取时间）和业务字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..lineage import now_cn, now_cn_isoformat

# Phase 6B 第一版固定口径。其他口径（em_concept / sw_l1 / citic_l1 / custom）在后续
# Phase 扩展，本数据源遇到非 em_industry 时返回空列表，不抛错，让同步层记 0 行成功。
SUPPORTED_CLASSIFICATION_SYSTEM: str = "em_industry"

# benchmark 名称 -> 东方财富指数代码。Phase 6B 只要求 hs300；其余为后续便利预留。
DEFAULT_BENCHMARK_SYMBOLS: Dict[str, str] = {
    "hs300": "000300",      # 沪深300
    "sse": "000001",        # 上证综指
    "szse": "399001",       # 深证成指
    "chinext": "399006",    # 创业板指
    "star50": "000688",     # 科创50
}


class AkShareFundamentalDataSource:
    """基于 AkShare 的东方财富行业板块数据源。

    参数：
        akshare: 可选的 akshare 模块注入。默认 ``None`` 时在首次调用方法时惰性
            ``import akshare``。测试中可传入实现相同函数签名的 fake 对象，避免联网。
        benchmark_symbols: 可选的 ``{benchmark_name: 指数代码}`` 覆盖默认映射。
        today: 可选的"今日"日期 ``YYYY-MM-DD``，用于 ``get_company_daily_snapshot``
            的点-in-time 校验。默认 ``None`` 时取 ``now_cn().date()``。测试中注入
            固定值以避免依赖系统时钟。

    ``name`` 固定为 ``"akshare_em"``，会作为 SQLite 采集表的 ``source`` 列写入。
    """

    name: str = "akshare_em"

    def __init__(
        self,
        akshare: Optional[Any] = None,
        benchmark_symbols: Optional[Dict[str, str]] = None,
        today: Optional[str] = None,
    ) -> None:
        self._akshare = akshare
        self._benchmark_symbols = dict(DEFAULT_BENCHMARK_SYMBOLS)
        if benchmark_symbols:
            self._benchmark_symbols.update(benchmark_symbols)
        self._today = today

    # ------------------------------------------------------------------
    # akshare 惰性访问
    # ------------------------------------------------------------------

    @property
    def _ak(self) -> Any:
        if self._akshare is None:
            try:
                import akshare as ak  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - 真实环境路径
                raise ImportError(
                    "akshare is required for AkShareFundamentalDataSource. "
                    "Install it with `pip install akshare` to enable real "
                    "em_industry sync."
                ) from exc
            self._akshare = ak
        return self._akshare

    # ------------------------------------------------------------------
    # 板块层
    # ------------------------------------------------------------------

    def list_sectors(self, classification_system: str) -> List[Dict[str, Any]]:
        if classification_system != SUPPORTED_CLASSIFICATION_SYSTEM:
            return []
        df = self._ak.stock_board_industry_name_em()
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            sector_id = _to_str(r.get("板块代码"))
            sector_name = _to_str(r.get("板块名称"))
            if not sector_id:
                # 缺板块代码的行无法稳定对齐成分/行情，交给同步层 PK 校验拒绝。
                continue
            rows.append(
                {
                    "sector_id": sector_id,
                    "sector_name": sector_name,
                    "classification_system": SUPPORTED_CLASSIFICATION_SYSTEM,
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_sector_constituents(
        self, sector_id: str, classification_system: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        if classification_system != SUPPORTED_CLASSIFICATION_SYSTEM:
            return []
        if not sector_id:
            return []
        df = self._ak.stock_board_industry_cons_em(symbol=sector_id)
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            code = _to_str(r.get("代码"))
            if not code:
                continue
            rows.append(
                {
                    "sector_id": sector_id,
                    "classification_system": SUPPORTED_CLASSIFICATION_SYSTEM,
                    "code": code,
                    "as_of_date": as_of_date,
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_sector_daily(
        self,
        sector_id: str,
        classification_system: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        if classification_system != SUPPORTED_CLASSIFICATION_SYSTEM:
            return []
        if not sector_id:
            return []
        # akshare ``stock_board_industry_hist_em`` 显式支持 BK 代码：内部用
        # ``re.match(r"^BK\d+", symbol)`` 判断，匹配时直接用作 secid，否则按板块
        # 名称查表（源码见 akshare.stock_board_industry_hist_em）。sector_id 来自
        # list_sectors 的板块代码，传 BK 代码更高效（省一次全量查表）且已通过真实
        # smoke 验证（493 板块 × 60 交易日写入 sector_daily_bars）。
        df = self._ak.stock_board_industry_hist_em(
            symbol=sector_id,
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
            period="日k",
            adjust="",
        )
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            trade_date = _normalize_date(r.get("日期"))
            if not trade_date:
                continue
            rows.append(
                {
                    "sector_id": sector_id,
                    "classification_system": SUPPORTED_CLASSIFICATION_SYSTEM,
                    "trade_date": trade_date,
                    "open": _to_float(r.get("开盘")),
                    "high": _to_float(r.get("最高")),
                    "low": _to_float(r.get("最低")),
                    "close": _to_float(r.get("收盘")),
                    "turnover_amount": _to_float(r.get("成交额")),
                    # rising_count / total_count 东方财富板块历史行情不提供，留空。
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_benchmark_daily(
        self, benchmark: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        symbol = self._benchmark_symbols.get(benchmark)
        if symbol is None:
            raise ValueError(
                f"unsupported benchmark: {benchmark!r}. Supported: "
                f"{sorted(self._benchmark_symbols)}"
            )
        df = self._ak.index_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
        )
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            trade_date = _normalize_date(r.get("日期"))
            if not trade_date:
                continue
            rows.append(
                {
                    "benchmark": benchmark,
                    "trade_date": trade_date,
                    "open": _to_float(r.get("开盘")),
                    "high": _to_float(r.get("最高")),
                    "low": _to_float(r.get("最低")),
                    "close": _to_float(r.get("收盘")),
                    "turnover_amount": _to_float(r.get("成交额")),
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # 公司层（Phase 6C）
    # ------------------------------------------------------------------

    def get_stock_universe(self, as_of_date: str) -> List[Dict[str, Any]]:
        """A 股股票池：``ak.stock_info_a_code_name()`` 返回全量 code/name。"""
        df = self._ak.stock_info_a_code_name()
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            code = _to_str(r.get("code"))
            if not code:
                continue
            name = _to_str(r.get("name"))
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "market": _derive_market(code),
                    "listing_status": "L",  # stock_info 只返回在市股票
                    "delisted_at": None,
                    "as_of_date": as_of_date,
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_company_daily_snapshot(self, trade_date: str) -> List[Dict[str, Any]]:
        """全 A 最新行情快照：``ak.stock_zh_a_spot_em()``。

        返回东方财富实时行情。``stock_zh_a_spot_em()`` 是实时接口，无法回溯历史
        某日快照。为遵守点-in-time 原则（docs §20: 行情使用 ``trade_date <=
        analysis_date``），仅当 ``trade_date`` 等于当前日期时才允许调用；否则
        抛出 ``ValueError``，避免将今日可见的价格错误标记为历史日期。

        百分比字段（``涨跌幅`` / ``换手率``）按 docs §20 标准化规则转成小数
        比率（``1.2`` → ``0.012``）。
        """
        today = self._today or now_cn().date().isoformat()
        if trade_date != today:
            raise ValueError(
                f"get_company_daily_snapshot: trade_date {trade_date!r} is not the "
                f"current date ({today}). stock_zh_a_spot_em() returns realtime data "
                f"only; historical daily snapshots require a per-code daily-quote "
                f"source not yet integrated in Phase 6C."
            )
        df = self._ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            code = _to_str(r.get("代码"))
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "trade_date": trade_date,
                    "close": _to_float(r.get("最新价")),
                    "turnover_amount": _to_float(r.get("成交额")),
                    "turnover_rate": _pct_to_ratio(r.get("换手率")),
                    "market_cap": _to_float(r.get("总市值")),
                    "change_pct": _pct_to_ratio(r.get("涨跌幅")),
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_company_valuation_history(
        self, codes: List[str], start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """PE/PB 历史日度：``ak.stock_zh_valuation_baidu(symbol, indicator, period)``。

        百度股市通接口每次返回一个指标的时间序列（date, value），需对每只股票
        分别调用 PE(TTM) 和 PB，按日期合并后按 ``[start_date, end_date]`` 过滤。
        PS / dividend_yield 百度接口不提供，留 None。
        """
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for code in codes:
            if not code:
                continue
            pe_map = self._fetch_baidu_indicator(code, "市盈率(TTM)")
            pb_map = self._fetch_baidu_indicator(code, "市净率")
            all_dates = sorted(set(pe_map.keys()) | set(pb_map.keys()))
            for d in all_dates:
                if d < start_date or d > end_date:
                    continue
                rows.append(
                    {
                        "code": code,
                        "trade_date": d,
                        "market": _derive_market(code),
                        "pe": pe_map.get(d),
                        "pb": pb_map.get(d),
                        "ps": None,
                        "dividend_yield": None,
                        "source_updated_at": fetched_at,
                    }
                )
        return rows

    def get_financial_metrics(
        self, codes: List[str], as_of_date: str
    ) -> List[Dict[str, Any]]:
        """财务指标：``ak.stock_financial_analysis_indicator(symbol, start_year)``。

        新浪财务指标接口返回季度长表（日期 + 88 个比率列）。按报告期末月份
        推导 period_type / report_period，用监管最晚披露日估算 disclosure_date，
        并按 ``disclosure_date <= as_of_date`` 做点-in-time 过滤。
        """
        # 回看 5 年财报（覆盖 lookback 窗口）
        start_year = str(int(as_of_date[:4]) - 5)
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for code in codes:
            if not code:
                continue
            df = self._ak.stock_financial_analysis_indicator(
                symbol=code, start_year=start_year
            )
            if df is None or len(df) == 0:
                continue
            for r in df.to_dict(orient="records"):
                period_end = _normalize_date(r.get("日期"))
                if not period_end:
                    continue
                disclosure_date = _estimate_disclosure_date(period_end)
                # 点-in-time：只保留在 analysis_date 当日已可公开获取的财报
                if disclosure_date > as_of_date:
                    continue
                rows.append(
                    {
                        "code": code,
                        "report_period": _derive_report_period(period_end),
                        "period_end_date": period_end,
                        "disclosure_date": disclosure_date,
                        "period_type": _derive_period_type(period_end),
                        "as_of_date": as_of_date,
                        "revenue_yoy": _pct_to_ratio(r.get("主营业务收入增长率(%)")),
                        "net_profit_yoy": _pct_to_ratio(r.get("净利润增长率(%)")),
                        "deducted_net_profit_yoy": None,
                        "gross_margin": _pct_to_ratio(r.get("销售毛利率(%)")),
                        "net_margin": _pct_to_ratio(r.get("销售净利率(%)")),
                        "roe": _pct_to_ratio(r.get("净资产收益率(%)")),
                        "operating_cashflow_to_profit": _pct_to_ratio(
                            r.get("经营现金净流量与净利润的比率(%)")
                        ),
                        "free_cashflow": None,
                        "debt_to_asset": _pct_to_ratio(r.get("资产负债率(%)")),
                        "interest_bearing_debt_ratio": _pct_to_ratio(
                            r.get("长期负债比率(%)")
                        ),
                        "accounts_receivable_yoy": None,
                        "inventory_yoy": None,
                        "gross_margin_yoy_change": None,
                        "source_updated_at": fetched_at,
                    }
                )
        return rows

    # ------------------------------------------------------------------
    # 公司层辅助
    # ------------------------------------------------------------------

    def _fetch_baidu_indicator(
        self, code: str, indicator: str
    ) -> Dict[str, Optional[float]]:
        """调用百度估值接口，返回 ``{trade_date: value}`` 映射。"""
        df = self._ak.stock_zh_valuation_baidu(
            symbol=code, indicator=indicator, period="全部"
        )
        if df is None or len(df) == 0:
            return {}
        result: Dict[str, Optional[float]] = {}
        for r in df.to_dict(orient="records"):
            d = _normalize_date(r.get("date"))
            if not d:
                continue
            result[d] = _to_float(r.get("value"))
        return result


# ---------------------------------------------------------------------------
# 标准化辅助
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> Optional[float]:
    """把 akshare 返回值转成 float，``None`` / NaN / 非数都返回 ``None``。"""

    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _pct_to_ratio(value: Any) -> Optional[float]:
    """把百分比数值转成小数比率（docs §20: ``0.18`` 表示 18%）。

    akshare 的 ``(%)`` 列返回原始百分比数字（``15.0`` 表示 15%），需 ``/ 100``
    转成小数，与 core 阈值（``high_debt_ratio: 0.7`` 等）对齐。
    """

    f = _to_float(value)
    if f is None:
        return None
    return f / 100.0


def _to_str(value: Any) -> Optional[str]:
    """去除空白后的字符串；空串/None/NaN 都返回 ``None``。

    pandas 可能把缺失值表示成 float NaN（甚至混进 object 列），``str(nan)`` 会变成
    ``"nan"``，因此需要先把 NaN/NA 拦下来，避免把 ``"nan"`` 当成合法板块代码写入。
    """

    if value is None:
        return None
    # float NaN（含 numpy.float64 nan，它继承自 Python float）。
    if isinstance(value, float) and value != value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 兜底：pandas NA 在个别路径会被转成 "<NA>" 字符串；真实板块/股票代码不会叫这个名字。
    if s.lower() in ("nan", "<na>", "none"):
        return None
    return s


def _compact_date(value: str) -> str:
    """``YYYY-MM-DD`` -> ``YYYYMMDD``（akshare 历史接口要求无分隔符）。"""

    return str(value).replace("-", "")


def _normalize_date(value: Any) -> Optional[str]:
    """把 akshare 日期统一成 ``YYYY-MM-DD``。容忍 ``YYYY-MM-DD`` / ``YYYYMMDD`` / datetime。"""

    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "-" in s:
        # 已是 YYYY-MM-DD（可能带时间，取前 10 位）。
        return s[:10]
    if "/" in s:
        return s.replace("/", "-")[:10]
    if len(s) >= 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if len(s) >= 10 else None


def _derive_market(code: str) -> Optional[str]:
    """从 6 位代码推导交易所：``6`` → SH，``0/3`` → SZ，``4/8`` → BJ。"""
    if not code or len(code) < 1:
        return None
    first = code[0]
    if first == "6":
        return "SH"
    if first in ("0", "3"):
        return "SZ"
    if first in ("4", "8"):
        return "BJ"
    return None


def _derive_period_type(period_end_date: str) -> str:
    """报告期末 → period_type：12 月 → annual，6 月 → semiannual，其余 → quarterly。

    值域对齐 docs 契约 ``quarterly | semiannual | annual | ttm``。"""
    month = period_end_date[5:7]
    if month == "12":
        return "annual"
    if month == "06":
        return "semiannual"
    return "quarterly"


def _derive_report_period(period_end_date: str) -> str:
    """报告期末 → report_period：``2026-03-31`` → ``2026Q1``。"""
    year = period_end_date[:4]
    month = period_end_date[5:7]
    if month == "03":
        return f"{year}Q1"
    if month == "06":
        return f"{year}H1"
    if month == "09":
        return f"{year}Q3"
    if month == "12":
        return f"{year}A"
    return f"{year}-{month}"


def _estimate_disclosure_date(period_end_date: str) -> str:
    """估算财报最晚披露日（监管截止日），用于 point-in-time 过滤。

    - Q1 (03-31): 04-30
    - H1 (06-30): 08-31
    - Q3 (09-30): 10-31
    - Annual (12-31): 次年 04-30
    """
    year = int(period_end_date[:4])
    month = period_end_date[5:7]
    if month == "03":
        return f"{year}-04-30"
    if month == "06":
        return f"{year}-08-31"
    if month == "09":
        return f"{year}-10-31"
    if month == "12":
        return f"{year + 1}-04-30"
    return period_end_date


__all__ = [
    "AkShareFundamentalDataSource",
    "DEFAULT_BENCHMARK_SYMBOLS",
    "SUPPORTED_CLASSIFICATION_SYSTEM",
]
