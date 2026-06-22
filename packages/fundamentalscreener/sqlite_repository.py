"""从 SQLite 组装 ``MarketSnapshot`` 的 Repository（Phase 6D）。

Phase 6D 目标：让真实数据通过 repository 进入现有 core 和 CLI/JSON 契约。

设计原则：
- 只读取 SQLite，不做采集/标准化（那些属于 sync 层）。
- 所有时变数据按 ``analysis_date`` 截断（docs §20）：
  - 行情/估值：``trade_date <= analysis_date``
  - 板块成分/股票池：``as_of_date <= analysis_date``
  - 财务：``disclosure_date <= analysis_date``（point-in-time）
- 质量状态为 ``invalid`` 时不生成 ``MarketSnapshot``，抛 ``QualityInvalidError``。
- ``metadata`` / ``quality_report`` 在 ``load_snapshot()`` 时一并生成，供 CLI
  透传到 JSON 顶层 ``snapshot`` 对象。
- 估值分位基于本地 ``company_valuation_history`` 计算，不信任外部返回的 precentile。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from .lineage import (
    DEFAULT_CONFIG_VERSION,
    DEFAULT_FORMULA_VERSION,
    SnapshotMetadata,
    SourceSet,
    now_cn,
)
from .percentile import compute_valuation_percentiles
from .quality import QualityReport, run_quality_checks
from .repositories import (
    BenchmarkData,
    CompanyData,
    DailyBar,
    FinancialData,
    MarketSnapshot,
    Repository,
    SectorData,
    ValuationData,
)
from .sqlite_schema import connect, init_db

PathLike = Union[str, Path, sqlite3.Connection]

# Financial dedup ORDER BY — shared by _load_financials, _extract_source_set,
# and _latest_fetch_run_id to ensure lineage describes exactly the rows selected.
_FINANCIAL_DEDUP_ORDER = (
    "period_end_date DESC,"
    " CASE period_type"
    "   WHEN 'annual' THEN 1"
    "   WHEN 'semiannual' THEN 2"
    "   WHEN 'quarterly' THEN 3"
    "   WHEN 'quarter' THEN 3"
    "   WHEN 'first_quarter' THEN 4"
    "   ELSE 5"
    " END,"
    " disclosure_date DESC,"
    " source_updated_at DESC"
)


class QualityInvalidError(Exception):
    """质量状态为 ``invalid``，不能生成 ``MarketSnapshot``。"""


class SqliteFundamentalRepository(Repository):
    """从 SQLite 组装 ``MarketSnapshot`` 的 Repository。

    参数：
        db_path: SQLite 文件路径，或已打开的 ``sqlite3.Connection``（便于测试）。
        analysis_date: 分析日期 ``YYYY-MM-DD``，所有时变数据按此截断。
        classification_system: 板块分类口径，默认 ``em_industry``。
        benchmark: 基准指数 ID，默认 ``hs300``。

    用法::

        repo = SqliteFundamentalRepository("fundamental_data.sqlite", "2026-06-19")
        snapshot = repo.load_snapshot()      # → MarketSnapshot
        meta = repo.metadata                  # → SnapshotMetadata
        report = repo.quality_report          # → QualityReport
    """

    def __init__(
        self,
        db_path: PathLike,
        analysis_date: str,
        classification_system: str = "em_industry",
        benchmark: str = "hs300",
    ) -> None:
        self._conn: Optional[sqlite3.Connection] = None
        if isinstance(db_path, sqlite3.Connection):
            self._conn = db_path
            self.db_path = Path(":memory:")
        else:
            self.db_path = Path(db_path)
        self.analysis_date = analysis_date
        self.classification_system = classification_system
        self.benchmark = benchmark
        self._snapshot: Optional[MarketSnapshot] = None
        self._metadata: Optional[SnapshotMetadata] = None
        self._quality_report: Optional[QualityReport] = None

    # ------------------------------------------------------------------
    # Repository 接口
    # ------------------------------------------------------------------

    def load_snapshot(self) -> MarketSnapshot:
        if self._snapshot is not None:
            return self._snapshot

        if self._conn is not None:
            return self._load_with_conn(self._conn)

        conn = connect(self.db_path)
        try:
            return self._load_with_conn(conn)
        finally:
            conn.close()

    def _load_with_conn(self, conn) -> MarketSnapshot:
        init_db(conn)

        # 质量检查
        self._quality_report = run_quality_checks(
            conn,
            analysis_date=self.analysis_date,
            classification_system=self.classification_system,
            benchmark=self.benchmark,
        )

        if self._quality_report.status == "invalid":
            raise QualityInvalidError(
                f"data_quality_status is 'invalid': "
                f"{self._quality_report.counts} issues found"
            )

        snapshot = self._assemble(conn)
        snapshot.data_quality_status = self._quality_report.status
        self._snapshot = snapshot

        # 元数据
        self._metadata = self._build_metadata(conn)
        return self._snapshot

    def list_sectors(self) -> List[SectorData]:
        return list(self.load_snapshot().sectors)

    def list_companies(self, sector_id: Optional[str] = None) -> List[CompanyData]:
        companies = self.load_snapshot().companies
        if sector_id is None:
            return list(companies)
        return [c for c in companies if c.sector_id == sector_id]

    def find_sector(self, sector_id_or_name: str) -> Optional[SectorData]:
        for s in self.load_snapshot().sectors:
            if s.sector_id == sector_id_or_name or s.sector_name == sector_id_or_name:
                return s
        return None

    def get_companies_by_codes(self, codes: Iterable[str]) -> List[CompanyData]:
        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {c.code: c for c in self.load_snapshot().companies}
        return [index[code] for code in wanted if code in index]

    def get_financials_by_codes(self, codes: Iterable[str]) -> List[FinancialData]:
        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {f.code: f for f in self.load_snapshot().financials}
        return [index[code] for code in wanted if code in index]

    def get_valuations_by_codes(self, codes: Iterable[str]) -> List[ValuationData]:
        wanted = [c.strip() for c in codes if c and c.strip()]
        if not wanted:
            return []
        index = {v.code: v for v in self.load_snapshot().valuations}
        return [index[code] for code in wanted if code in index]

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SnapshotMetadata:
        if self._metadata is None:
            self.load_snapshot()
        return self._metadata  # type: ignore[return-value]

    @property
    def quality_report(self) -> QualityReport:
        if self._quality_report is None:
            self.load_snapshot()
        return self._quality_report  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------

    def _assemble(self, conn) -> MarketSnapshot:
        benchmark = self._load_benchmark(conn)
        sectors = self._load_sectors(conn)
        companies = self._load_companies(conn, sectors)
        company_codes = {c.code for c in companies}
        financials = self._load_financials(conn, company_codes)
        valuations = self._load_valuations(conn, company_codes)

        return MarketSnapshot(
            date=self.analysis_date,
            classification_system=self.classification_system,
            benchmark=benchmark,
            sectors=sectors,
            companies=companies,
            financials=financials,
            valuations=valuations,
            data_quality_status=self._quality_report.status if self._quality_report else "ok",
        )

    def _load_benchmark(self, conn) -> BenchmarkData:
        rows = conn.execute(
            "SELECT trade_date, close, turnover_amount "
            "FROM benchmark_daily_bars "
            "WHERE benchmark = ? AND trade_date <= ? "
            "ORDER BY trade_date",
            (self.benchmark, self.analysis_date),
        ).fetchall()
        daily = [
            DailyBar(
                date=r[0],
                close=float(r[1]) if r[1] is not None else 0.0,
                turnover_amount=float(r[2]) if r[2] is not None else 0.0,
            )
            for r in rows
        ]
        return BenchmarkData(
            id=self.benchmark,
            name=self.benchmark,
            daily=daily,
        )

    def _load_sectors(self, conn) -> List[SectorData]:
        sector_rows = conn.execute(
            "SELECT sector_id, sector_name FROM sectors "
            "WHERE classification_system = ?",
            (self.classification_system,),
        ).fetchall()

        sectors: List[SectorData] = []
        for sr in sector_rows:
            sector_id = sr[0]
            sector_name = sr[1] or ""

            # 成分股（取 as_of_date <= analysis_date 的最新快照，而非累积所有历史）
            const_rows = conn.execute(
                "SELECT DISTINCT code FROM sector_constituents "
                "WHERE sector_id = ? AND classification_system = ? "
                "AND as_of_date = ("
                "  SELECT MAX(as_of_date) FROM sector_constituents "
                "  WHERE sector_id = ? AND classification_system = ? "
                "  AND as_of_date <= ?"
                ")",
                (sector_id, self.classification_system,
                 sector_id, self.classification_system, self.analysis_date),
            ).fetchall()
            constituents = [r[0] for r in const_rows if r[0]]

            # 板块日线
            daily_rows = conn.execute(
                "SELECT trade_date, close, turnover_amount "
                "FROM sector_daily_bars "
                "WHERE sector_id = ? AND classification_system = ? "
                "AND trade_date <= ? "
                "ORDER BY trade_date",
                (sector_id, self.classification_system, self.analysis_date),
            ).fetchall()
            daily = [
                DailyBar(
                    date=r[0],
                    close=float(r[1]) if r[1] is not None else 0.0,
                    turnover_amount=float(r[2]) if r[2] is not None else 0.0,
                )
                for r in daily_rows
            ]

            sectors.append(
                SectorData(
                    sector_id=sector_id,
                    sector_name=sector_name,
                    constituents=constituents,
                    daily=daily,
                )
            )
        return sectors

    def _load_companies(self, conn, sectors: List[SectorData]) -> List[CompanyData]:
        # 构建 code → sector_id 映射（取第一个匹配的板块）
        code_to_sector: Dict[str, str] = {}
        for s in sectors:
            for code in s.constituents:
                if code not in code_to_sector:
                    code_to_sector[code] = s.sector_id

        # 仅取板块成分股作为公司宇宙（docs §18: companies/financials/valuations 对齐）
        all_codes = set(code_to_sector.keys())

        companies: List[CompanyData] = []
        for code in sorted(all_codes):
            # 名称
            stock_row = conn.execute(
                "SELECT name FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            name = stock_row[0] if stock_row and stock_row[0] else code

            # 最新市值（latest trade_date <= analysis_date）
            cap_row = conn.execute(
                "SELECT market_cap FROM company_daily_snapshot "
                "WHERE code = ? AND trade_date <= ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (code, self.analysis_date),
            ).fetchone()
            market_cap = float(cap_row[0]) if cap_row and cap_row[0] is not None else None

            # 日线
            daily_rows = conn.execute(
                "SELECT trade_date, close, turnover_amount, turnover_rate "
                "FROM company_daily_snapshot "
                "WHERE code = ? AND trade_date <= ? "
                "ORDER BY trade_date",
                (code, self.analysis_date),
            ).fetchall()
            daily = [
                DailyBar(
                    date=r[0],
                    close=float(r[1]) if r[1] is not None else 0.0,
                    turnover_amount=float(r[2]) if r[2] is not None else 0.0,
                    turnover_rate=float(r[3]) if r[3] is not None else None,
                )
                for r in daily_rows
            ]

            companies.append(
                CompanyData(
                    code=code,
                    name=name,
                    sector_id=code_to_sector.get(code),
                    market_cap=market_cap,
                    daily=daily,
                )
            )
        return companies

    def _load_financials(self, conn, company_codes: set) -> List[FinancialData]:
        # 每个 code 取最新已披露的财报（point-in-time: disclosure_date <= analysis_date AND as_of_date <= analysis_date）。
        # 多源/多版本断优：使用 ROW_NUMBER() 按 period_end_date DESC（最新报告期）、
        # period_type 优先级（annual > semiannual > quarterly > first_quarter）、
        # disclosure_date DESC、source_updated_at DESC 确定性选一行。
        # 仅加载板块成分股（与 companies 宇宙对齐，docs §18）。
        rows = conn.execute(
            "SELECT code, revenue_yoy, net_profit_yoy, deducted_net_profit_yoy, "
            "gross_margin, net_margin, roe, operating_cashflow_to_profit, "
            "free_cashflow, debt_to_asset, interest_bearing_debt_ratio, "
            "accounts_receivable_yoy, inventory_yoy, gross_margin_yoy_change "
            "FROM ("
            "  SELECT *, ROW_NUMBER() OVER ("
            f"    PARTITION BY code ORDER BY {_FINANCIAL_DEDUP_ORDER}"
            "  ) AS rn"
            "  FROM financial_metrics"
            "  WHERE disclosure_date <= ? AND as_of_date <= ?"
            ") WHERE rn = 1",
            (self.analysis_date, self.analysis_date),
        ).fetchall()

        financials: List[FinancialData] = []
        for r in rows:
            if r[0] not in company_codes:
                continue
            financials.append(
                FinancialData(
                    code=r[0],
                    revenue_yoy=_opt_float(r[1]),
                    net_profit_yoy=_opt_float(r[2]),
                    deducted_net_profit_yoy=_opt_float(r[3]),
                    gross_margin=_opt_float(r[4]),
                    net_margin=_opt_float(r[5]),
                    roe=_opt_float(r[6]),
                    operating_cashflow_to_profit=_opt_float(r[7]),
                    free_cashflow=_opt_float(r[8]),
                    debt_to_asset=_opt_float(r[9]),
                    interest_bearing_debt_ratio=_opt_float(r[10]),
                    accounts_receivable_yoy=_opt_float(r[11]),
                    inventory_yoy=_opt_float(r[12]),
                    gross_margin_yoy_change=_opt_float(r[13]),
                )
            )
        return financials

    def _load_valuations(self, conn, company_codes: set) -> List[ValuationData]:
        # 获取所有有估值历史的 code，仅保留板块成分股（与 companies 宇宙对齐，docs §18）
        codes = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT code FROM company_valuation_history "
                "WHERE trade_date <= ?",
                (self.analysis_date,),
            ).fetchall()
            if r[0] in company_codes
        ]

        valuations: List[ValuationData] = []
        for code in codes:
            # 最新估值行
            row = conn.execute(
                "SELECT pe, pb, ps, dividend_yield "
                "FROM company_valuation_history "
                "WHERE code = ? AND trade_date <= ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (code, self.analysis_date),
            ).fetchone()
            if not row:
                continue

            # 计算历史分位
            pct_result = compute_valuation_percentiles(
                conn, code, self.analysis_date
            )

            # 名称
            stock_row = conn.execute(
                "SELECT name FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            name = stock_row[0] if stock_row and stock_row[0] else code

            valuations.append(
                ValuationData(
                    code=code,
                    pe=_opt_float(row[0]),
                    pb=_opt_float(row[1]),
                    ps=_opt_float(row[2]),
                    peg=None,  # 百度接口不提供，留 None
                    dividend_yield=_opt_float(row[3]),
                    pe_percentile=pct_result.pe_percentile,
                    pb_percentile=pct_result.pb_percentile,
                    industry_valuation_position=None,  # 需跨公司比较，留 None
                    name=name,
                )
            )
        return valuations

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------

    def _build_metadata(self, conn) -> SnapshotMetadata:
        source_set = self._extract_source_set(conn)
        fetch_run_id = self._latest_fetch_run_id(conn)
        quality_report_id = (
            self._quality_report.quality_report_id if self._quality_report else ""
        )
        status = (
            self._quality_report.status if self._quality_report else "ok"
        )

        return SnapshotMetadata.create(
            analysis_date=self.analysis_date,
            data_cutoff=self.analysis_date,
            source_set=source_set,
            fetch_run_id=fetch_run_id,
            quality_report_id=quality_report_id,
            data_quality_status=status,
        )

    def _extract_source_set(self, conn) -> SourceSet:
        """从 snapshot 实际使用的行中提取 ``{role: source_name}``。

        按 ``analysis_date``、``classification_system``、``benchmark`` 过滤，
        只取参与本次 snapshot 的行的 source，避免多 run 数据污染。
        当存在多个来源时，取行数最多的来源作为权威来源。
        """

        ss = SourceSet()
        # sector: from sectors table scoped by classification_system
        row = conn.execute(
            "SELECT source FROM sectors "
            "WHERE classification_system = ? "
            "GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1",
            (self.classification_system,),
        ).fetchone()
        if row and row[0]:
            ss.update("sector", row[0])
        # benchmark: scoped by benchmark id and trade_date
        row = conn.execute(
            "SELECT source FROM benchmark_daily_bars "
            "WHERE benchmark = ? AND trade_date <= ? "
            "GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1",
            (self.benchmark, self.analysis_date),
        ).fetchone()
        if row and row[0]:
            ss.update("benchmark", row[0])
        # quote: company_daily_snapshot scoped by sector constituents
        row = conn.execute(
            "SELECT source FROM company_daily_snapshot c "
            "WHERE c.trade_date <= ? "
            "AND EXISTS ("
            "  SELECT 1 FROM sector_constituents sc "
            "  WHERE sc.code = c.code AND sc.classification_system = ? "
            "  AND sc.as_of_date = ("
            "    SELECT MAX(as_of_date) FROM sector_constituents "
            "    WHERE sector_id = sc.sector_id AND classification_system = ? "
            "    AND as_of_date <= ?"
            "  )"
            ") GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1",
            (self.analysis_date, self.classification_system,
             self.classification_system, self.analysis_date),
        ).fetchone()
        if row and row[0]:
            ss.update("quote", row[0])
        # financial: scoped by point-in-time, sector constituents, and rn=1 (actual selected rows)
        row = conn.execute(
            "SELECT source FROM ("
            "  SELECT source, ROW_NUMBER() OVER ("
            f"    PARTITION BY code ORDER BY {_FINANCIAL_DEDUP_ORDER}"
            "  ) AS rn"
            "  FROM financial_metrics f"
            "  WHERE f.disclosure_date <= ? AND f.as_of_date <= ? "
            "  AND EXISTS ("
            "    SELECT 1 FROM sector_constituents sc "
            "    WHERE sc.code = f.code AND sc.classification_system = ? "
            "    AND sc.as_of_date = ("
            "      SELECT MAX(as_of_date) FROM sector_constituents "
            "      WHERE sector_id = sc.sector_id AND classification_system = ? "
            "      AND as_of_date <= ?"
            "    )"
            "  )"
            ") WHERE rn = 1 "
            "GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1",
            (self.analysis_date, self.analysis_date,
             self.classification_system, self.classification_system, self.analysis_date),
        ).fetchone()
        if row and row[0]:
            ss.update("financial", row[0])
        # valuation: scoped by sector constituents
        row = conn.execute(
            "SELECT source FROM company_valuation_history v "
            "WHERE v.trade_date <= ? "
            "AND EXISTS ("
            "  SELECT 1 FROM sector_constituents sc "
            "  WHERE sc.code = v.code AND sc.classification_system = ? "
            "  AND sc.as_of_date = ("
            "    SELECT MAX(as_of_date) FROM sector_constituents "
            "    WHERE sector_id = sc.sector_id AND classification_system = ? "
            "    AND as_of_date <= ?"
            "  )"
            ") GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1",
            (self.analysis_date, self.classification_system,
             self.classification_system, self.analysis_date),
        ).fetchone()
        if row and row[0]:
            ss.update("valuation", row[0])
        return ss

    def _latest_fetch_run_id(self, conn) -> str:
        """取与本次 snapshot 实际使用行相关的 ``fetch_run_id``。

        从 snapshot 各采集表中选择参与行的 ``fetch_run_id``，
        按行数降序取主导的 fetch_run_id。避免使用 ``data_fetch_log``
        的日期匹配或全局最新，确保血缘来自实际数据行。
        """

        # 从各表中收集 fetch_run_id 及其出现次数（quote/valuation/financial 按板块成分股过滤）
        # financial 仅计入 rn=1 的实际选中行，避免被淘汰行污染血缘
        _cs = self.classification_system
        _ad = self.analysis_date
        rows = conn.execute(
            "SELECT fetch_run_id, COUNT(*) AS cnt FROM ("
            f"  SELECT fetch_run_id FROM sectors WHERE classification_system = ? "
            f"  UNION ALL "
            f"  SELECT fetch_run_id FROM benchmark_daily_bars "
            f"  WHERE benchmark = ? AND trade_date <= ? "
            f"  UNION ALL "
            f"  SELECT c.fetch_run_id FROM company_daily_snapshot c "
            f"  WHERE c.trade_date <= ? "
            f"  AND EXISTS (SELECT 1 FROM sector_constituents sc "
            f"    WHERE sc.code = c.code AND sc.classification_system = ? "
            f"    AND sc.as_of_date = ("
            f"      SELECT MAX(as_of_date) FROM sector_constituents "
            f"      WHERE sector_id = sc.sector_id AND classification_system = ? "
            f"      AND as_of_date <= ?)"
            f") "
            f"  UNION ALL "
            f"  SELECT fetch_run_id FROM ("
            f"    SELECT fetch_run_id, ROW_NUMBER() OVER ("
            f"      PARTITION BY code ORDER BY {_FINANCIAL_DEDUP_ORDER}"
            f"    ) AS rn"
            f"    FROM financial_metrics f"
            f"    WHERE f.disclosure_date <= ? AND f.as_of_date <= ? "
            f"    AND EXISTS (SELECT 1 FROM sector_constituents sc "
            f"      WHERE sc.code = f.code AND sc.classification_system = ? "
            f"      AND sc.as_of_date = ("
            f"        SELECT MAX(as_of_date) FROM sector_constituents "
            f"        WHERE sector_id = sc.sector_id AND classification_system = ? "
            f"        AND as_of_date <= ?)"
            f"  )"
            f") WHERE rn = 1 "
            f"  UNION ALL "
            f"  SELECT v.fetch_run_id FROM company_valuation_history v "
            f"  WHERE v.trade_date <= ? "
            f"  AND EXISTS (SELECT 1 FROM sector_constituents sc "
            f"    WHERE sc.code = v.code AND sc.classification_system = ? "
            f"    AND sc.as_of_date = ("
            f"      SELECT MAX(as_of_date) FROM sector_constituents "
            f"      WHERE sector_id = sc.sector_id AND classification_system = ? "
            f"      AND as_of_date <= ?)"
            f") "
            ") GROUP BY fetch_run_id ORDER BY cnt DESC LIMIT 1",
            (
                _cs,
                self.benchmark, _ad,
                _ad, _cs, _cs, _ad,
                _ad, _ad, _cs, _cs, _ad,
                _ad, _cs, _cs, _ad,
            ),
        ).fetchone()
        if rows and rows[0]:
            return rows[0]
        return ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


__all__ = [
    "QualityInvalidError",
    "SqliteFundamentalRepository",
]
