"""Public shapes for daily market review."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

LadderStatus = Literal["missing", "complete"]
LadderWriteMode = Literal["snapshot_replace", "item_patch", "reset_missing"]
MarketCode = Literal["sh", "sz", "bj"]
AcquisitionMode = Literal["auto", "manual"]

ATOMIC_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "effective_limit_up",
        "limit_up_20pct",
        "opened_limit_down",
        "closed_limit_down",
        "limit_up_failed",
        "pullback_count",
        "median_change_pct",
        "advancing_count",
        "declining_count",
        "margin_balance_sh",
        "margin_balance_sz",
        "margin_balance_bj",
        "sh_index_close",
        "sh_index_prev_close",
        "sz_index_close",
        "sz_index_prev_close",
        "cy_index_close",
        "cy_index_prev_close",
        "turnover_amount_sh",
        "turnover_amount_sz",
        "turnover_amount_cy",
        "turnover_amount_bj",
        "total_market_cap",
        "float_market_cap",
        "pe_sh",
        "pe_sz",
        "pe_cy",
        "pe_all",
        "avg_stock_price",
    }
)


@dataclass(frozen=True)
class MetricProvenance:
    source: str
    source_as_of: str | None
    retrieved_at: str
    acquisition_mode: AcquisitionMode


@dataclass(frozen=True)
class LadderStockInput:
    market: MarketCode
    code: str
    name: str
    streak_height: int
    is_st: bool


@dataclass(frozen=True)
class LadderStockRecord:
    trade_date: str
    market: MarketCode
    code: str
    name: str
    streak_height: int
    is_st: bool = False

    @property
    def identity(self) -> str:
        return f"{self.market}.{self.code}"


@dataclass
class LadderSnapshotReplace:
    mode: Literal["snapshot_replace"] = "snapshot_replace"
    ladder_status: Literal["complete"] = "complete"
    stocks: list[LadderStockInput] = field(default_factory=list)


@dataclass
class LadderItemPatch:
    mode: Literal["item_patch"] = "item_patch"
    upserts: list[LadderStockInput] = field(default_factory=list)
    deletes: list[tuple[MarketCode, str]] = field(default_factory=list)


@dataclass
class LadderResetMissing:
    mode: Literal["reset_missing"] = "reset_missing"


LadderOperation = LadderSnapshotReplace | LadderItemPatch | LadderResetMissing


@dataclass
class DailyMarketReviewAtoms:
    trade_date: str
    ladder_status: LadderStatus = "missing"
    effective_limit_up: int | None = None
    limit_up_20pct: int | None = None
    opened_limit_down: int | None = None
    closed_limit_down: int | None = None
    limit_up_failed: int | None = None
    pullback_count: int | None = None
    median_change_pct: float | None = None
    advancing_count: int | None = None
    declining_count: int | None = None
    margin_balance_sh: float | None = None
    margin_balance_sz: float | None = None
    margin_balance_bj: float | None = None
    sh_index_close: float | None = None
    sh_index_prev_close: float | None = None
    sz_index_close: float | None = None
    sz_index_prev_close: float | None = None
    cy_index_close: float | None = None
    cy_index_prev_close: float | None = None
    turnover_amount_sh: float | None = None
    turnover_amount_sz: float | None = None
    turnover_amount_cy: float | None = None
    turnover_amount_bj: float | None = None
    total_market_cap: float | None = None
    float_market_cap: float | None = None
    pe_sh: float | None = None
    pe_sz: float | None = None
    pe_cy: float | None = None
    pe_all: float | None = None
    avg_stock_price: float | None = None


@dataclass
class DailyMarketReviewView:
    atoms: DailyMarketReviewAtoms
    ladder_stocks: list[LadderStockRecord]
    computed: dict[str, Any]
    provenance: dict[str, MetricProvenance]
