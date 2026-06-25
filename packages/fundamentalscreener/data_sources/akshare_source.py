"""AkShare 真实数据源实现（Phase 6B + 6C）。

板块层支持两种口径：

- ``ths_industry``（默认主源）：同花顺行业板块
  - ``list_sectors``：``ak.stock_board_industry_name_ths()``
  - ``get_sector_constituents``：抓取同花顺板块详情页 ``q.10jqka.com.cn/thshy/detail``
    解析成分股表格（含 AJAX 分页）。akshare 无直接 THS 成分接口。
  - ``get_sector_daily``：``ak.stock_board_industry_index_ths(symbol=板块名称, ...)``

- ``em_industry``（对照源）：东方财富行业板块
  - ``list_sectors``：``ak.stock_board_industry_name_em()``
  - ``get_sector_constituents``：``ak.stock_board_industry_cons_em(symbol=BK 代码)``
  - ``get_sector_daily``：``ak.stock_board_industry_hist_em(symbol=BK 代码, ...)``

- ``get_benchmark_daily``：``ak.stock_zh_index_daily(symbol=新浪指数代码)``，至少支持 ``hs300``（§15.9 改用新浪指数源，东方财富 push2 被墙）

公司层（Phase 6C）：

- ``get_stock_universe``：``ak.stock_info_a_code_name()``
- ``get_company_daily_snapshot``：per-code ``ak.stock_zh_a_daily(symbol, ...)``（新浪源，§15.9 改用 per-code 抓取，东方财富实时快照被墙）
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
- THS 成分股抓取使用 ``requests`` + ``BeautifulSoup``（akshare 依赖），惰性导入。
  测试中可通过 fake akshare 提供 ``stock_board_industry_cons_ths`` 方法注入。
- 返回行使用 ``snake_case``；缺失值用 ``None``，不用 ``0`` 占位。
- ``source`` / ``fetch_run_id`` / ``created_at`` / ``updated_at`` 由同步层 enricher
  填充，本数据源只提供 ``source_updated_at``（取抓取时间）和业务字段。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..lineage import now_cn, now_cn_isoformat

# 产品默认口径：同花顺行业板块。本地实测显示同花顺接口在稳定性、可靠性和响应
# 速度上优于东方财富。东方财富保留为开发对照源。
SUPPORTED_CLASSIFICATION_SYSTEM: str = "ths_industry"

# 本数据源支持的全部口径。
SUPPORTED_CLASSIFICATION_SYSTEMS: tuple = ("ths_industry", "em_industry")

# benchmark 名称 -> 东方财富指数代码。Phase 6B 只要求 hs300；其余为后续便利预留。
DEFAULT_BENCHMARK_SYMBOLS: Dict[str, str] = {
    "hs300": "000300",      # 沪深300
    "sse": "000001",        # 上证综指
    "szse": "399001",       # 深证成指
    "chinext": "399006",    # 创业板指
    "star50": "000688",     # 科创50
}


class AkShareFundamentalDataSource:
    """基于 AkShare 的行业板块数据源，默认使用同花顺口径（``ths_industry``）。

    参数：
        akshare: 可选的 akshare 模块注入。默认 ``None`` 时在首次调用方法时惰性
            ``import akshare``。测试中可传入实现相同函数签名的 fake 对象，避免联网。
            fake 对象还可提供 ``stock_board_industry_cons_ths`` 方法用于注入 THS
            成分股数据（真实路径通过抓取同花顺详情页获取）。
        benchmark_symbols: 可选的 ``{benchmark_name: 指数代码}`` 覆盖默认映射。
        today: 可选的"今日"日期 ``YYYY-MM-DD``，用于 ``get_company_daily_snapshot``
            的点-in-time 校验。默认 ``None`` 时取 ``now_cn().date()``。测试中注入
            固定值以避免依赖系统时钟。
        name: 数据源名称，写入 SQLite 的 ``source`` 列。默认 ``"akshare_ths"``。
            注意：``sync_all`` 会按实际 ``classification_system`` 派生 source name
            并覆盖此值；仅在不经 sync_all 直接使用数据源时此值生效。

    ``name`` 默认 ``"akshare_ths"``，会作为 SQLite 采集表的 ``source`` 列写入。
    ``sync_all`` 会按 ``classification_system`` 重新派生，避免 lineage 误标。
    """

    def __init__(
        self,
        akshare: Optional[Any] = None,
        benchmark_symbols: Optional[Dict[str, str]] = None,
        today: Optional[str] = None,
        name: str = "akshare_ths",
    ) -> None:
        self._akshare = akshare
        self._benchmark_symbols = dict(DEFAULT_BENCHMARK_SYMBOLS)
        if benchmark_symbols:
            self._benchmark_symbols.update(benchmark_symbols)
        self._today = today
        self.name = name
        # THS 板块代码 -> 板块名称缓存，供 get_sector_daily 将 sector_id(代码)
        # 映射为 stock_board_industry_index_ths 所需的板块名称。
        self._ths_name_cache: Dict[str, str] = {}

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
                    "sector sync (ths_industry / em_industry)."
                ) from exc
            self._akshare = ak
        return self._akshare

    # ------------------------------------------------------------------
    # 板块层
    # ------------------------------------------------------------------

    def list_sectors(self, classification_system: str) -> List[Dict[str, Any]]:
        if classification_system == "ths_industry":
            return self._list_ths_sectors()
        if classification_system == "em_industry":
            return self._list_em_sectors()
        return []

    def _list_ths_sectors(self) -> List[Dict[str, Any]]:
        """同花顺行业板块列表：``ak.stock_board_industry_name_ths()``。

        返回 ``['name', 'code']``，约 90 个板块。``code``（如 ``881121``）作为
        ``sector_id``，``name``（如 ``半导体``）作为 ``sector_name``。同时缓存
        code→name 映射，供 ``get_sector_daily`` 使用。
        """
        df = self._ak.stock_board_industry_name_ths()
        if df is None or len(df) == 0:
            return []
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in df.to_dict(orient="records"):
            sector_id = _to_str(r.get("code"))
            sector_name = _to_str(r.get("name"))
            if not sector_id:
                continue
            if sector_name:
                self._ths_name_cache[sector_id] = sector_name
            rows.append(
                {
                    "sector_id": sector_id,
                    "sector_name": sector_name,
                    "classification_system": "ths_industry",
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def _list_em_sectors(self) -> List[Dict[str, Any]]:
        """东方财富行业板块列表：``ak.stock_board_industry_name_em()``。"""
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
                    "classification_system": "em_industry",
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def get_sector_constituents(
        self, sector_id: str, classification_system: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        if classification_system == "ths_industry":
            return self._get_ths_constituents(sector_id, as_of_date)
        if classification_system == "em_industry":
            return self._get_em_constituents(sector_id, as_of_date)
        return []

    def _get_ths_constituents(
        self, sector_id: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        """同花顺板块成分股。

        akshare 无直接 THS 成分接口。优先使用注入的 ``stock_board_industry_cons_ths``
        方法（测试注入）；否则抓取同花顺板块详情页 ``q.10jqka.com.cn/thshy/detail``
        解析成分股表格（含 AJAX 分页）。
        """
        if not sector_id:
            return []
        cons_fn = getattr(self._ak, "stock_board_industry_cons_ths", None)
        if cons_fn is not None:
            df = cons_fn(symbol=sector_id)
            if df is None or len(df) == 0:
                return []
            records = df.to_dict(orient="records")
        else:
            records = self._scrape_ths_constituents(sector_id)
            if not records:
                return []
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            code = _to_str(r.get("代码"))
            if not code:
                continue
            rows.append(
                {
                    "sector_id": sector_id,
                    "classification_system": "ths_industry",
                    "code": code,
                    "as_of_date": as_of_date,
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def _get_em_constituents(
        self, sector_id: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        """东方财富板块成分股：``ak.stock_board_industry_cons_em(symbol=BK 代码)``。"""
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
                    "classification_system": "em_industry",
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
        if classification_system == "ths_industry":
            return self._get_ths_sector_daily(sector_id, start_date, end_date)
        if classification_system == "em_industry":
            return self._get_em_sector_daily(sector_id, start_date, end_date)
        return []

    def _get_ths_sector_daily(
        self, sector_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """同花顺板块日线：``ak.stock_board_industry_index_ths(symbol=板块名称, ...)``。

        THS 指数接口接受板块**名称**（如 ``半导体``），而非代码（如 ``881121``）。
        通过 ``_ths_name_cache`` 或按需调用 ``stock_board_industry_name_ths()``
        将 sector_id(代码) 映射为名称。

        返回列：``日期 / 开盘价 / 最高价 / 最低价 / 收盘价 / 成交量 / 成交额``。
        rising_count / total_count THS 指数不提供，留空。
        """
        if not sector_id:
            return []
        sector_name = self._get_ths_sector_name(sector_id)
        if not sector_name:
            return []
        df = self._ak.stock_board_industry_index_ths(
            symbol=sector_name,
            start_date=_compact_date(start_date),
            end_date=_compact_date(end_date),
        )
        if df is None or len(df) == 0:
            return []
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in df.to_dict(orient="records"):
            trade_date = _normalize_date(r.get("日期"))
            if not trade_date:
                continue
            rows.append(
                {
                    "sector_id": sector_id,
                    "classification_system": "ths_industry",
                    "trade_date": trade_date,
                    "open": _to_float(r.get("开盘价")),
                    "high": _to_float(r.get("最高价")),
                    "low": _to_float(r.get("最低价")),
                    "close": _to_float(r.get("收盘价")),
                    "turnover_amount": _to_float(r.get("成交额")),
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    def _get_em_sector_daily(
        self, sector_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """东方财富板块日线：``ak.stock_board_industry_hist_em(symbol=BK 代码, ...)``。"""
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
                    "classification_system": "em_industry",
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
        # 新浪 ``stock_zh_index_daily`` 返回全量历史（无起止参数），需在内存按
        # ``[start_date, end_date]`` 过滤。东方财富 ``index_zh_a_hist``（push2 主机）
        # 在本环境被墙不可用，改用新浪指数日线源（已验证可用）。
        sina_symbol = _to_sina_index_symbol(symbol)
        df = self._ak.stock_zh_index_daily(symbol=sina_symbol)
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for r in records:
            trade_date = _normalize_date(r.get("date"))
            if not trade_date:
                continue
            if trade_date < start_date or trade_date > end_date:
                continue
            rows.append(
                {
                    "benchmark": benchmark,
                    "trade_date": trade_date,
                    "open": _to_float(r.get("open")),
                    "high": _to_float(r.get("high")),
                    "low": _to_float(r.get("low")),
                    "close": _to_float(r.get("close")),
                    # 新浪指数日线不提供成交额，留 None（benchmark 主要用于相对收益，
                    # 依赖 close，不依赖 turnover_amount）。
                    "turnover_amount": None,
                    "source_updated_at": fetched_at,
                }
            )
        return rows

    # ------------------------------------------------------------------
    # THS 板块辅助
    # ------------------------------------------------------------------

    def _get_ths_sector_name(self, sector_id: str) -> Optional[str]:
        """将 THS 板块代码映射为板块名称。

        ``stock_board_industry_index_ths`` 接受板块名称而非代码，因此需要此映射。
        优先使用 ``list_sectors`` 时填充的缓存；缓存未命中时按需调用
        ``stock_board_industry_name_ths()`` 获取全量映射。
        """
        if sector_id in self._ths_name_cache:
            return self._ths_name_cache[sector_id]
        df = self._ak.stock_board_industry_name_ths()
        if df is None or len(df) == 0:
            return None
        for r in df.to_dict(orient="records"):
            code = _to_str(r.get("code"))
            name = _to_str(r.get("name"))
            if code and name:
                self._ths_name_cache[code] = name
        return self._ths_name_cache.get(sector_id)

    def _scrape_ths_constituents(self, sector_id: str) -> List[Dict[str, Any]]:
        """抓取同花顺板块详情页获取成分股代码列表。

        页面结构：``http://q.10jqka.com.cn/thshy/detail/code/{code}/``
        每页 20 只股票，通过 AJAX 分页：
        ``http://q.10jqka.com.cn/thshy/detail/code/{code}/page/{n}/ajax/1/``

        返回 ``[{"代码": "300077", "名称": "国民技术"}, ...]``。
        ``requests`` / ``BeautifulSoup`` 惰性导入（akshare 依赖）。
        """
        import requests  # type: ignore[import-not-found]
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36"
            )
        }
        all_records: List[Dict[str, Any]] = []

        # 第一页：含成分股表格 + 分页信息
        main_url = f"http://q.10jqka.com.cn/thshy/detail/code/{sector_id}/"
        resp = requests.get(main_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, features="lxml")
        all_records.extend(self._parse_ths_stock_table(soup))
        total_pages = self._parse_ths_total_pages(soup)

        # 后续页：AJAX 端点返回纯表格 HTML
        for page in range(2, total_pages + 1):
            ajax_url = (
                f"http://q.10jqka.com.cn/thshy/detail/code/{sector_id}/"
                f"page/{page}/ajax/1/"
            )
            resp = requests.get(ajax_url, headers=headers, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, features="lxml")
            all_records.extend(self._parse_ths_stock_table(soup))

        return all_records

    @staticmethod
    def _parse_ths_stock_table(soup: Any) -> List[Dict[str, Any]]:
        """从 BeautifulSoup 解析的页面中提取成分股代码和名称。

        THS 板块详情页表格结构：``序号 | 代码 | 名称 | 现价 | ...``
        第二列是股票代码，第三列是股票名称。
        """
        records: List[Dict[str, Any]] = []
        for table in soup.find_all("table"):
            tbody = table.find("tbody")
            if not tbody:
                continue
            for tr in tbody.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 3:
                    code = tds[1].text.strip()
                    name = tds[2].text.strip()
                    if code and code[0].isdigit():
                        records.append({"代码": code, "名称": name})
        return records

    @staticmethod
    def _parse_ths_total_pages(soup: Any) -> int:
        """从分页元素解析总页数。

        THS 分页文本格式：``1  2  3  4  5  下一页尾页1/9``
        末尾 ``1/9`` 表示当前第 1 页，共 9 页。
        """
        pager = soup.find(class_="m-pager")
        if pager:
            match = re.search(r"/(\d+)", pager.text)
            if match:
                return int(match.group(1))
        return 1

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

    def get_company_daily_snapshot(
        self, trade_date: str, codes: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """公司日度行情快照（per-code，新浪源 ``stock_zh_a_daily``）。

        东方财富 ``stock_zh_a_spot_em``（push2 主机）在本环境被墙不可用，改用新浪
        ``stock_zh_a_daily`` 逐只抓取（已验证可用，返回 date/open/high/low/close/
        volume/amount/outstanding_share/turnover）。

        - ``codes=None``：全市场，从 ``stock_info_a_code_name()`` 取全量 code 逐只
          抓取（向后兼容，量级约 5500 只，慢；按需加载路径应显式传 ``codes``）。
        - ``codes`` 非空：仅抓指定 codes。
        - 每只取 ``trade_date`` 当日（或最近一个 <= trade_date 的交易日）的行情；
          ``market_cap = close × outstanding_share``；``change_pct`` 由相邻两日收盘
          价计算（首日为 ``None``）。支持历史日期，不再限制 ``trade_date == today``。
        - 百分比字段（``turnover``）新浪返回即为小数比率，直接使用。
        """
        if codes is None:
            codes = self._all_codes()
        fetched_at = now_cn_isoformat()
        rows: List[Dict[str, Any]] = []
        for code in codes:
            if not code:
                continue
            daily = self._fetch_code_daily(code, trade_date)
            if not daily:
                continue
            rows.append(daily | {"code": code, "source_updated_at": fetched_at})
        return rows

    def _all_codes(self) -> List[str]:
        """从 ``stock_info_a_code_name()`` 取全量 A 股代码（``codes=None`` 兜底）。"""

        df = self._ak.stock_info_a_code_name()
        if df is None or len(df) == 0:
            return []
        return [_to_str(r.get("code")) for r in df.to_dict(orient="records") if _to_str(r.get("code"))]

    def _fetch_code_daily(
        self, code: str, trade_date: str
    ) -> Optional[Dict[str, Any]]:
        """抓取单只股票在 ``trade_date`` 的日度快照。

        取 ``[trade_date-12d, trade_date]`` 窗口（覆盖周末 + 节假日），取最近一条
        ``<= trade_date`` 的行作为当日快照；若有前一日收盘则计算 ``change_pct``。
        """

        from datetime import date as _date, timedelta as _td

        end = _date.fromisoformat(trade_date)
        start = (end - _td(days=12)).isoformat()
        symbol = _to_sina_symbol(code)
        df = self._ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=_compact_date(start),
            end_date=_compact_date(trade_date),
            adjust="",
        )
        if df is None or len(df) == 0:
            return None
        records = [
            r for r in df.to_dict(orient="records")
            if _normalize_date(r.get("date")) and _normalize_date(r.get("date")) <= trade_date
        ]
        if not records:
            return None
        records.sort(key=lambda r: _normalize_date(r.get("date")))  # type: ignore[arg-type]
        latest = records[-1]
        close = _to_float(latest.get("close"))
        outstanding = _to_float(latest.get("outstanding_share"))
        market_cap = close * outstanding if (close is not None and outstanding is not None) else None
        change_pct: Optional[float] = None
        if len(records) >= 2:
            prev_close = _to_float(records[-2].get("close"))
            if prev_close:
                change_pct = (close - prev_close) / prev_close if close is not None else None
        return {
            "trade_date": _normalize_date(latest.get("date")),
            "close": close,
            "turnover_amount": _to_float(latest.get("amount")),
            "turnover_rate": _to_float(latest.get("turnover")),
            "market_cap": market_cap,
            "change_pct": change_pct,
        }

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
    """从 6 位代码推导交易所：``6`` → SH，``0/3`` → SZ，``4/8/920`` → BJ。"""
    if not code or len(code) < 1:
        return None
    if code.startswith("920"):  # 北交所 920 段
        return "BJ"
    first = code[0]
    if first == "6":
        return "SH"
    if first in ("0", "3"):
        return "SZ"
    if first in ("4", "8"):
        return "BJ"
    return None


def _to_sina_symbol(code: str) -> str:
    """6 位股票代码 → 新浪行情 symbol（``sh600001`` / ``sz002371`` / ``bj830799``）。

    新浪 ``stock_zh_a_daily`` 接受带交易所前缀的小写 symbol。
    """

    market = _derive_market(code)
    prefix = (market or "sz").lower()
    return f"{prefix}{code}"


def _to_sina_index_symbol(code: str) -> str:
    """指数代码 → 新浪指数 symbol：``000xxx`` → ``sh000xxx``，``399xxx`` → ``sz399xxx``。"""

    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


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
    "SUPPORTED_CLASSIFICATION_SYSTEMS",
]
