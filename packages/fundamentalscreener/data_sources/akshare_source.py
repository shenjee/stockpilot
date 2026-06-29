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

from typing import Any, Dict, List, Optional

from ..lineage import now_cn_isoformat
from .akshare_benchmark import get_benchmark_daily as _get_benchmark_daily_impl
from .akshare_company import (
    all_codes as _all_codes_impl,
    fetch_baidu_indicator as _fetch_baidu_indicator_impl,
    fetch_code_daily as _fetch_code_daily_impl,
    get_company_daily_snapshot as _get_company_daily_snapshot_impl,
    get_company_valuation_history as _get_company_valuation_history_impl,
    get_financial_metrics as _get_financial_metrics_impl,
    get_stock_universe as _get_stock_universe_impl,
)
from .akshare_normalize import (
    compact_date as _compact_date_impl,
    derive_market as _derive_market_impl,
    derive_period_type as _derive_period_type_impl,
    derive_report_period as _derive_report_period_impl,
    estimate_disclosure_date as _estimate_disclosure_date_impl,
    normalize_date as _normalize_date_impl,
    pct_to_ratio as _pct_to_ratio_impl,
    to_float as _to_float_impl,
    to_sina_index_symbol as _to_sina_index_symbol_impl,
    to_sina_symbol as _to_sina_symbol_impl,
    to_str as _to_str_impl,
)
from .akshare_sector_em import (
    get_em_constituents as _get_em_constituents_impl,
    get_em_sector_daily as _get_em_sector_daily_impl,
    list_em_sectors as _list_em_sectors_impl,
)
from .akshare_sector_ths import (
    get_ths_constituents as _get_ths_constituents_impl,
    get_ths_sector_daily as _get_ths_sector_daily_impl,
    get_ths_sector_name as _get_ths_sector_name_impl,
    list_ths_sectors as _list_ths_sectors_impl,
)
from .akshare_ths_scraper import (
    parse_ths_stock_table as _parse_ths_stock_table_impl,
    parse_ths_total_pages as _parse_ths_total_pages_impl,
    scrape_ths_constituents as _scrape_ths_constituents_impl,
)

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
        return _list_ths_sectors_impl(
            ak=self._ak,
            now_isoformat=now_cn_isoformat,
            to_str=_to_str,
            name_cache=self._ths_name_cache,
        )

    def _list_em_sectors(self) -> List[Dict[str, Any]]:
        return _list_em_sectors_impl(ak=self._ak, now_isoformat=now_cn_isoformat, to_str=_to_str)

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
        return _get_ths_constituents_impl(
            ak=self._ak,
            sector_id=sector_id,
            as_of_date=as_of_date,
            now_isoformat=now_cn_isoformat,
            to_str=_to_str,
            scrape_ths_constituents=self._scrape_ths_constituents,
        )

    def _get_em_constituents(
        self, sector_id: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        return _get_em_constituents_impl(
            ak=self._ak,
            sector_id=sector_id,
            as_of_date=as_of_date,
            now_isoformat=now_cn_isoformat,
            to_str=_to_str,
        )

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
        return _get_ths_sector_daily_impl(
            ak=self._ak,
            sector_id=sector_id,
            start_date=start_date,
            end_date=end_date,
            now_isoformat=now_cn_isoformat,
            compact_date=_compact_date,
            normalize_date=_normalize_date,
            to_float=_to_float,
            get_ths_sector_name=self._get_ths_sector_name,
        )

    def _get_em_sector_daily(
        self, sector_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return _get_em_sector_daily_impl(
            ak=self._ak,
            sector_id=sector_id,
            start_date=start_date,
            end_date=end_date,
            now_isoformat=now_cn_isoformat,
            compact_date=_compact_date,
            normalize_date=_normalize_date,
            to_float=_to_float,
        )

    def get_benchmark_daily(
        self, benchmark: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return _get_benchmark_daily_impl(
            ak=self._ak,
            benchmark=benchmark,
            start_date=start_date,
            end_date=end_date,
            benchmark_symbols=self._benchmark_symbols,
            now_isoformat=now_cn_isoformat,
            normalize_date=_normalize_date,
            to_float=_to_float,
            to_sina_index_symbol=_to_sina_index_symbol,
        )

    # ------------------------------------------------------------------
    # THS 板块辅助
    # ------------------------------------------------------------------

    def _get_ths_sector_name(self, sector_id: str) -> Optional[str]:
        return _get_ths_sector_name_impl(
            ak=self._ak,
            sector_id=sector_id,
            to_str=_to_str,
            name_cache=self._ths_name_cache,
        )

    def _scrape_ths_constituents(self, sector_id: str) -> List[Dict[str, Any]]:
        return _scrape_ths_constituents_impl(
            sector_id,
            parse_stock_table=self._parse_ths_stock_table,
            parse_total_pages=self._parse_ths_total_pages,
        )

    @staticmethod
    def _parse_ths_stock_table(soup: Any) -> List[Dict[str, Any]]:
        return _parse_ths_stock_table_impl(soup)

    @staticmethod
    def _parse_ths_total_pages(soup: Any) -> int:
        return _parse_ths_total_pages_impl(soup)

    # ------------------------------------------------------------------
    # 公司层（Phase 6C）
    # ------------------------------------------------------------------

    def get_stock_universe(self, as_of_date: str) -> List[Dict[str, Any]]:
        return _get_stock_universe_impl(
            ak=self._ak,
            as_of_date=as_of_date,
            now_isoformat=now_cn_isoformat,
            to_str=_to_str,
            derive_market=_derive_market,
        )

    def get_company_daily_snapshot(
        self, trade_date: str, codes: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        return _get_company_daily_snapshot_impl(
            ak=self._ak,
            trade_date=trade_date,
            codes=codes,
            now_isoformat=now_cn_isoformat,
            all_codes=self._all_codes,
            fetch_code_daily=self._fetch_code_daily,
        )

    def _all_codes(self) -> List[str]:
        return _all_codes_impl(ak=self._ak, to_str=_to_str)

    def _fetch_code_daily(
        self, code: str, trade_date: str
    ) -> Optional[Dict[str, Any]]:
        return _fetch_code_daily_impl(
            ak=self._ak,
            code=code,
            trade_date=trade_date,
            compact_date=_compact_date,
            normalize_date=_normalize_date,
            to_float=_to_float,
            to_sina_symbol=_to_sina_symbol,
        )

    def get_company_valuation_history(
        self, codes: List[str], start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return _get_company_valuation_history_impl(
            ak=self._ak,
            codes=codes,
            start_date=start_date,
            end_date=end_date,
            now_isoformat=now_cn_isoformat,
            fetch_baidu_indicator=self._fetch_baidu_indicator,
            derive_market=_derive_market,
        )

    def get_financial_metrics(
        self, codes: List[str], as_of_date: str
    ) -> List[Dict[str, Any]]:
        return _get_financial_metrics_impl(
            ak=self._ak,
            codes=codes,
            as_of_date=as_of_date,
            now_isoformat=now_cn_isoformat,
            normalize_date=_normalize_date,
            estimate_disclosure_date=_estimate_disclosure_date,
            derive_report_period=_derive_report_period,
            derive_period_type=_derive_period_type,
            pct_to_ratio=_pct_to_ratio,
        )

    # ------------------------------------------------------------------
    # 公司层辅助
    # ------------------------------------------------------------------

    def _fetch_baidu_indicator(
        self, code: str, indicator: str
    ) -> Dict[str, Optional[float]]:
        return _fetch_baidu_indicator_impl(
            ak=self._ak,
            code=code,
            indicator=indicator,
            normalize_date=_normalize_date,
            to_float=_to_float,
        )


# ---------------------------------------------------------------------------
# 标准化辅助
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> Optional[float]:
    return _to_float_impl(value)


def _pct_to_ratio(value: Any) -> Optional[float]:
    return _pct_to_ratio_impl(value)


def _to_str(value: Any) -> Optional[str]:
    return _to_str_impl(value)


def _compact_date(value: str) -> str:
    return _compact_date_impl(value)


def _normalize_date(value: Any) -> Optional[str]:
    return _normalize_date_impl(value)


def _derive_market(code: str) -> Optional[str]:
    return _derive_market_impl(code)


def _to_sina_symbol(code: str) -> str:
    return _to_sina_symbol_impl(code)


def _to_sina_index_symbol(code: str) -> str:
    return _to_sina_index_symbol_impl(code)


def _derive_period_type(period_end_date: str) -> str:
    return _derive_period_type_impl(period_end_date)


def _derive_report_period(period_end_date: str) -> str:
    return _derive_report_period_impl(period_end_date)


def _estimate_disclosure_date(period_end_date: str) -> str:
    return _estimate_disclosure_date_impl(period_end_date)


__all__ = [
    "AkShareFundamentalDataSource",
    "DEFAULT_BENCHMARK_SYMBOLS",
    "SUPPORTED_CLASSIFICATION_SYSTEM",
    "SUPPORTED_CLASSIFICATION_SYSTEMS",
]
