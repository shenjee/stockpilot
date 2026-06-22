"""AkShare 真实数据源实现（Phase 6B）。

第一版只支持 ``classification_system = "em_industry"``（东方财富行业板块），覆盖：

- ``list_sectors``：``ak.stock_board_industry_name_em()``
- ``get_sector_constituents``：``ak.stock_board_industry_cons_em(symbol=BK 代码)``
- ``get_sector_daily``：``ak.stock_board_industry_hist_em(symbol=BK 代码, ...)``
- ``get_benchmark_daily``：``ak.index_zh_a_hist(symbol=指数代码, ...)``，至少支持 ``hs300``

公司层方法（股票池、公司日度快照、估值历史、财务指标）在 Phase 6B 暂未实现，
返回空列表，保证 ``sync_all`` 的 8 个子任务都能跑完而不阻塞板块层采集。

设计原则：
- 只负责读取外部数据并返回标准化前的 ``List[Dict]``，不写 SQLite、不做质量检查。
- ``akshare`` 采用**惰性导入**：模块导入不触发 ``import akshare``，只有在真正调用
  方法且未注入 ``akshare`` 时才导入。这样在没有安装 akshare 的环境下也能 import
  本模块、运行注入式测试。
- 支持**依赖注入**：构造时传入 ``akshare=<fake>`` 即可替换真实 akshare 模块，便于
  在无网络环境对转换逻辑做单元测试。真实网络同步作为手动 smoke，不是单元测试阻塞项。
- 返回行使用 ``snake_case``；缺失值用 ``None``，不用 ``0`` 占位；百分比保持小数
  （本 Phase 板块层只写 OHLC + 成交额，不涉及百分比转换）。
- ``source`` / ``fetch_run_id`` / ``created_at`` / ``updated_at`` 由同步层 enricher
  填充，本数据源只提供 ``source_updated_at``（取抓取时间）和业务字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..lineage import now_cn_isoformat

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

    ``name`` 固定为 ``"akshare_em"``，会作为 SQLite 采集表的 ``source`` 列写入。
    """

    name: str = "akshare_em"

    def __init__(
        self,
        akshare: Optional[Any] = None,
        benchmark_symbols: Optional[Dict[str, str]] = None,
    ) -> None:
        self._akshare = akshare
        self._benchmark_symbols = dict(DEFAULT_BENCHMARK_SYMBOLS)
        if benchmark_symbols:
            self._benchmark_symbols.update(benchmark_symbols)

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
    # 公司层（Phase 6C 落地，Phase 6B 暂返回空列表）
    # ------------------------------------------------------------------
    # 返回空列表而非抛错：sync_all 的 8 个子任务都能以"成功 0 行"完成，板块层采集
    # 不被公司层未实现阻塞。Phase 6C 接入后再替换为真实实现。

    def get_stock_universe(self, as_of_date: str) -> List[Dict[str, Any]]:
        return []

    def get_company_daily_snapshot(self, trade_date: str) -> List[Dict[str, Any]]:
        return []

    def get_company_valuation_history(
        self, codes: List[str], start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return []

    def get_financial_metrics(
        self, codes: List[str], as_of_date: str
    ) -> List[Dict[str, Any]]:
        return []


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


__all__ = [
    "AkShareFundamentalDataSource",
    "DEFAULT_BENCHMARK_SYMBOLS",
    "SUPPORTED_CLASSIFICATION_SYSTEM",
]
