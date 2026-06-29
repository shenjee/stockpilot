from __future__ import annotations

from typing import Any, Callable, Dict, List, Sequence

from .sync_persistence import (
    _BENCHMARK_COLUMNS,
    _COMPANY_DAILY_COLUMNS,
    _COMPANY_VAL_COLUMNS,
    _FINANCIAL_COLUMNS,
    _SECTOR_COLUMNS,
    _SECTOR_CONSTITUENTS_COLUMNS,
    _SECTOR_DAILY_COLUMNS,
    _STOCKS_COLUMNS,
    _PersistResult,
    _lineage_columns,
    _persist_with_validation,
)

PersistFn = Callable[[List[Dict[str, Any]]], _PersistResult]
RowMapper = Callable[[Dict[str, Any]], Dict[str, Any]]


def _build_persist(
    conn,
    *,
    table: str,
    pk_columns: Sequence[str],
    column_order: Sequence[str],
    source_name: str,
    fetch_run_id: str,
    row_mapper: RowMapper,
) -> PersistFn:
    def _persist(rows: List[Dict[str, Any]]) -> _PersistResult:
        return _persist_with_validation(
            conn,
            table=table,
            rows=rows,
            pk_columns=pk_columns,
            column_order=column_order,
            enrich=lambda row: _lineage_columns(
                row_mapper(row),
                source_name,
                fetch_run_id,
            ),
        )

    return _persist


def build_sectors_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    classification_system: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="sectors",
        pk_columns=("sector_id", "classification_system"),
        column_order=_SECTOR_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "sector_id": str(row.get("sector_id", "")),
            "classification_system": str(
                row.get("classification_system", classification_system)
            ),
            "sector_name": row.get("sector_name"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_sector_constituents_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    classification_system: str,
    analysis_date: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="sector_constituents",
        pk_columns=("sector_id", "classification_system", "code", "as_of_date"),
        column_order=_SECTOR_CONSTITUENTS_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "sector_id": str(row.get("sector_id", "")),
            "classification_system": str(
                row.get("classification_system", classification_system)
            ),
            "code": str(row.get("code", "")),
            "as_of_date": str(row.get("as_of_date", analysis_date)),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_sector_daily_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    classification_system: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="sector_daily_bars",
        pk_columns=("sector_id", "classification_system", "trade_date"),
        column_order=_SECTOR_DAILY_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "sector_id": str(row.get("sector_id", "")),
            "classification_system": str(
                row.get("classification_system", classification_system)
            ),
            "trade_date": str(row.get("trade_date", "")),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "turnover_amount": row.get("turnover_amount"),
            "rising_count": row.get("rising_count"),
            "total_count": row.get("total_count"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_benchmark_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    benchmark: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="benchmark_daily_bars",
        pk_columns=("benchmark", "trade_date"),
        column_order=_BENCHMARK_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "benchmark": str(row.get("benchmark", benchmark)),
            "trade_date": str(row.get("trade_date", "")),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "turnover_amount": row.get("turnover_amount"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_stock_universe_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    analysis_date: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="stocks",
        pk_columns=("code",),
        column_order=_STOCKS_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "code": str(row.get("code", "")),
            "name": row.get("name"),
            "market": row.get("market"),
            "listing_status": row.get("listing_status"),
            "delisted_at": row.get("delisted_at"),
            "as_of_date": str(row.get("as_of_date", analysis_date)),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_company_daily_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    analysis_date: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="company_daily_snapshot",
        pk_columns=("code", "trade_date"),
        column_order=_COMPANY_DAILY_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "code": str(row.get("code", "")),
            "trade_date": str(row.get("trade_date", analysis_date)),
            "close": row.get("close"),
            "turnover_amount": row.get("turnover_amount"),
            "turnover_rate": row.get("turnover_rate"),
            "market_cap": row.get("market_cap"),
            "change_pct": row.get("change_pct"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_company_valuation_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="company_valuation_history",
        pk_columns=("code", "trade_date"),
        column_order=_COMPANY_VAL_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "code": str(row.get("code", "")),
            "trade_date": str(row.get("trade_date", "")),
            "market": row.get("market"),
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "ps": row.get("ps"),
            "dividend_yield": row.get("dividend_yield"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )


def build_financial_metrics_persist(
    conn,
    *,
    source_name: str,
    fetch_run_id: str,
    analysis_date: str,
) -> PersistFn:
    return _build_persist(
        conn,
        table="financial_metrics",
        pk_columns=("code", "report_period", "period_type", "disclosure_date"),
        column_order=_FINANCIAL_COLUMNS,
        source_name=source_name,
        fetch_run_id=fetch_run_id,
        row_mapper=lambda row: {
            "code": str(row.get("code", "")),
            "report_period": str(row.get("report_period", "")),
            "period_end_date": str(row.get("period_end_date", "")),
            "disclosure_date": str(row.get("disclosure_date", "")),
            "period_type": str(row.get("period_type", "annual")),
            "as_of_date": str(row.get("as_of_date", analysis_date)),
            "revenue_yoy": row.get("revenue_yoy"),
            "net_profit_yoy": row.get("net_profit_yoy"),
            "deducted_net_profit_yoy": row.get("deducted_net_profit_yoy"),
            "gross_margin": row.get("gross_margin"),
            "net_margin": row.get("net_margin"),
            "roe": row.get("roe"),
            "operating_cashflow_to_profit": row.get("operating_cashflow_to_profit"),
            "free_cashflow": row.get("free_cashflow"),
            "debt_to_asset": row.get("debt_to_asset"),
            "interest_bearing_debt_ratio": row.get("interest_bearing_debt_ratio"),
            "accounts_receivable_yoy": row.get("accounts_receivable_yoy"),
            "inventory_yoy": row.get("inventory_yoy"),
            "gross_margin_yoy_change": row.get("gross_margin_yoy_change"),
            "source_updated_at": row.get("source_updated_at"),
        },
    )
