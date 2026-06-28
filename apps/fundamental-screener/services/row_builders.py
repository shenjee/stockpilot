from __future__ import annotations

from typing import Any, Dict, List, Sequence

from fundamentalscreener.schema import (
    CompanyEntry,
    FinancialEntry,
    SectorEntry,
    ValuationEntry,
)


def sectors_to_rows(sectors: Sequence[SectorEntry]) -> List[Dict[str, Any]]:
    return [s.to_dict() for s in sectors]


def companies_to_rows(companies: Sequence[CompanyEntry]) -> List[Dict[str, Any]]:
    return [c.to_dict() for c in companies]


def financials_to_rows(items: Sequence[FinancialEntry]) -> List[Dict[str, Any]]:
    return [f.to_dict() for f in items]


def valuations_to_rows(items: Sequence[ValuationEntry]) -> List[Dict[str, Any]]:
    return [v.to_dict() for v in items]


def collect_company_flags(
    companies: Sequence[CompanyEntry],
    financials: Sequence[FinancialEntry],
    valuations: Sequence[ValuationEntry],
) -> List[Dict[str, Any]]:
    """汇总每家公司的异常 flags 和估值 label，便于 UI 单表展示。"""

    fin_index = {f.code: f for f in financials}
    val_index = {v.code: v for v in valuations}
    rows: List[Dict[str, Any]] = []
    for c in companies:
        fin = fin_index.get(c.code)
        val = val_index.get(c.code)
        rows.append(
            {
                "code": c.code,
                "name": c.name,
                "group": c.group,
                "company_flags": list(c.flags or []),
                "financial_flags": list(fin.abnormal_flags) if fin else [],
                "valuation_label": val.label if val else None,
                "warnings": list(c.warnings or []),
            }
        )
    return rows
