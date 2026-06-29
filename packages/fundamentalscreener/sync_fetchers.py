from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .data_sources import FundamentalDataSource


def fetch_sectors(
    source: FundamentalDataSource,
    classification_system: str,
) -> List[Dict[str, Any]]:
    return source.list_sectors(classification_system)


def fetch_sector_constituents(
    source: FundamentalDataSource,
    *,
    sectors_rows: Sequence[Dict[str, Any]],
    classification_system: str,
    analysis_date: str,
    sector_ids: Optional[Sequence[str]],
) -> List[Dict[str, Any]]:
    # §15.9.5：sector_ids 非空时只遍历指定板块（按需加载），否则遍历全部
    # sectors_rows（向后兼容）。轻量层（list_sectors）始终全量，此处成分股属
    # 重量层。
    if sector_ids is not None:
        wanted = {str(sector_id) for sector_id in sector_ids}
        target_sectors = [
            sector
            for sector in sectors_rows
            if str(sector.get("sector_id", "")) in wanted
        ]
    else:
        target_sectors = list(sectors_rows)

    rows: List[Dict[str, Any]] = []
    for sector in target_sectors:
        sector_id = str(sector.get("sector_id", ""))
        if not sector_id:
            continue
        try:
            rows.extend(
                source.get_sector_constituents(
                    sector_id,
                    classification_system,
                    analysis_date,
                )
            )
        except Exception:
            continue

    # 目标板块非空但成分股总数为 0 → 几乎必然是数据源故障（反爬 403、空页、
    # API 结构变更），不能记成"成功写入 0 行"。抛错让 _run_task 标记 fetch_failed。
    # 注意：sector_ids 未命中任何板块时 target_sectors 为空，不抛错（graceful
    # no-op），避免按需加载指定了尚未出现在 sectors 表的板块时误判为故障。
    if target_sectors and not rows:
        raise RuntimeError(
            f"get_sector_constituents: {len(target_sectors)} sector(s) targeted but "
            f"0 constituents returned — likely a data source failure "
            f"(anti-crawl, HTTP error, or API structure change)."
        )

    return rows


def fetch_sector_daily(
    source: FundamentalDataSource,
    *,
    sectors_rows: Sequence[Dict[str, Any]],
    classification_system: str,
    start_date: str,
    analysis_date: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sector in sectors_rows:
        sector_id = str(sector.get("sector_id", ""))
        if not sector_id:
            continue
        try:
            rows.extend(
                source.get_sector_daily(
                    sector_id,
                    classification_system,
                    start_date,
                    analysis_date,
                )
            )
        except Exception:
            continue
    return rows


def fetch_benchmark_daily(
    source: FundamentalDataSource,
    *,
    benchmark: str,
    start_date: str,
    analysis_date: str,
) -> List[Dict[str, Any]]:
    return source.get_benchmark_daily(benchmark, start_date, analysis_date)


def fetch_stock_universe(
    source: FundamentalDataSource,
    *,
    analysis_date: str,
) -> List[Dict[str, Any]]:
    return source.get_stock_universe(analysis_date)


def derive_effective_company_codes(
    *,
    codes: Optional[Sequence[str]],
    sector_ids: Optional[Sequence[str]],
    constituents_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    # §15.9.5：确定 per-code 公司层任务（日线快照 + 估值 + 财务）的 code 集合。
    # - sector_ids 非空（按需加载）：从已抓取成分股派生 distinct codes；若 codes
    #   显式传入则取交集，否则直接用派生 codes。
    # - sector_ids=None（向后兼容）：codes 参数驱动 per-code 任务；未传则跳过。
    if sector_ids is not None:
        derived = sorted(
            {str(row.get("code", "")) for row in constituents_rows if row.get("code")}
        )
        if codes is not None:
            wanted = {str(code) for code in codes}
            return [code for code in derived if code in wanted]
        return derived
    return [code for code in (codes or []) if code]


def fetch_company_daily(
    source: FundamentalDataSource,
    *,
    analysis_date: str,
    sector_ids: Optional[Sequence[str]],
    effective_codes: Sequence[str],
) -> List[Dict[str, Any]]:
    # §15.9.5：sector_ids 非空时用派生 codes 驱动 per-code 日线快照；
    # sector_ids=None 时回退全市场（codes=None），保持向后兼容。
    if sector_ids is not None:
        return source.get_company_daily_snapshot(
            analysis_date,
            codes=list(effective_codes),
        )
    return source.get_company_daily_snapshot(analysis_date)


def fetch_company_valuation_history(
    source: FundamentalDataSource,
    *,
    effective_codes: Sequence[str],
    start_date: str,
    analysis_date: str,
) -> List[Dict[str, Any]]:
    return source.get_company_valuation_history(
        list(effective_codes),
        start_date,
        analysis_date,
    )


def fetch_financial_metrics(
    source: FundamentalDataSource,
    *,
    effective_codes: Sequence[str],
    analysis_date: str,
) -> List[Dict[str, Any]]:
    return source.get_financial_metrics(list(effective_codes), analysis_date)
