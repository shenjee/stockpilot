"""Fundamental Screener package.

StockPilot 基本面量化筛选模块。Phase 0 仅冻结包骨架、schema、配置、CLI 命令
契约和 fixture repository，不实现真实板块/公司/财务/估值计算。
"""

# Direct sys.path users may expose only packages/, without the repository root.
try:
    from packages._import_aliases import initialize_package as _initialize_package
except ModuleNotFoundError as _exc:
    if _exc.name != "packages":
        raise
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.append(str(_Path(__file__).resolve().parents[2]))
    from packages._import_aliases import initialize_package as _initialize_package

if _initialize_package(__name__):
    from .config import (
        DEFAULT_BENCHMARK,
        DEFAULT_CLASSIFICATION_SYSTEM,
        DEFAULT_FORMAT,
        DEFAULT_PERIODS,
        DEFAULT_SECTOR_SORT,
        DEFAULT_TOP,
        SUPPORTED_CLASSIFICATION_SYSTEMS,
        SUPPORTED_FORMATS,
        SUPPORTED_SECTOR_SORTS,
    )
    from .schema import (
        CandidatesPayload,
        ChartSeries,
        ChartSeriesPoint,
        CompaniesPayload,
        CompanyEntry,
        FinancialEntry,
        FinancialsPayload,
        ScreenPayload,
        SectorEntry,
        SectorsPayload,
        ValuationEntry,
        ValuationsPayload,
    )

    __all__ = [
        "DEFAULT_BENCHMARK",
        "DEFAULT_CLASSIFICATION_SYSTEM",
        "DEFAULT_FORMAT",
        "DEFAULT_PERIODS",
        "DEFAULT_SECTOR_SORT",
        "DEFAULT_TOP",
        "SUPPORTED_CLASSIFICATION_SYSTEMS",
        "SUPPORTED_FORMATS",
        "SUPPORTED_SECTOR_SORTS",
        "CandidatesPayload",
        "ChartSeries",
        "ChartSeriesPoint",
        "CompaniesPayload",
        "CompanyEntry",
        "FinancialEntry",
        "FinancialsPayload",
        "ScreenPayload",
        "SectorEntry",
        "SectorsPayload",
        "ValuationEntry",
        "ValuationsPayload",
    ]
