"""Repository 层接口与 fixture 实现。

Phase 0 仅提供 ``FixtureRepository``，从 JSON fixture 文件读取板块、公司和基准
数据，供 CLI 在不接真实数据库的情况下完成 schema/契约验证。

约定：
- Repository 不写业务评分逻辑。
- ``FixtureRepository`` 只做加载与简单查询（按 sector_id / 公司 code）。
- 真实 SQLite/数据库实现留给后续 Phase。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class DailyBar:
    """fixture 中的单日行情。"""

    date: str
    close: float
    turnover_amount: float
    turnover_rate: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DailyBar":
        return cls(
            date=str(raw["date"]),
            close=float(raw["close"]),
            turnover_amount=float(raw.get("turnover_amount", 0.0)),
            turnover_rate=(
                float(raw["turnover_rate"]) if raw.get("turnover_rate") is not None else None
            ),
        )


@dataclass
class BenchmarkData:
    """fixture 中的基准指数。"""

    id: str
    name: str
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class SectorData:
    """fixture 中的板块。"""

    sector_id: str
    sector_name: str
    constituents: List[str] = field(default_factory=list)
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class CompanyData:
    """fixture 中的公司。"""

    code: str
    name: str
    sector_id: Optional[str]
    market_cap: Optional[float]
    daily: List[DailyBar] = field(default_factory=list)


@dataclass
class MarketSnapshot:
    """fixture 加载后的市场快照。"""

    date: str
    classification_system: str
    benchmark: BenchmarkData
    sectors: List[SectorData] = field(default_factory=list)
    companies: List[CompanyData] = field(default_factory=list)


class Repository:
    """Repository 接口。Phase 0 仅声明最小读取方法。"""

    def load_snapshot(self) -> MarketSnapshot:  # pragma: no cover - 抽象接口
        raise NotImplementedError

    def list_sectors(self) -> List[SectorData]:  # pragma: no cover
        raise NotImplementedError

    def list_companies(self, sector_id: Optional[str] = None) -> List[CompanyData]:  # pragma: no cover
        raise NotImplementedError

    def get_companies_by_codes(self, codes: Iterable[str]) -> List[CompanyData]:  # pragma: no cover
        raise NotImplementedError


class FixtureRepository(Repository):
    """从 JSON fixture 加载市场快照的 Repository。"""

    def __init__(self, fixture_path: Path | str) -> None:
        self.fixture_path = Path(fixture_path)
        self._snapshot: Optional[MarketSnapshot] = None

    # -------------------------- 加载 --------------------------

    def load_snapshot(self) -> MarketSnapshot:
        if self._snapshot is not None:
            return self._snapshot

        if not self.fixture_path.exists():
            raise FileNotFoundError(f"fixture not found: {self.fixture_path}")

        with self.fixture_path.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)

        benchmark_raw = raw.get("benchmark") or {}
        benchmark = BenchmarkData(
            id=str(benchmark_raw.get("id", "")),
            name=str(benchmark_raw.get("name", "")),
            daily=[DailyBar.from_dict(d) for d in benchmark_raw.get("daily", [])],
        )

        sectors: List[SectorData] = []
        for s in raw.get("sectors", []):
            sectors.append(
                SectorData(
                    sector_id=str(s.get("sector_id", "")),
                    sector_name=str(s.get("sector_name", "")),
                    constituents=[str(c) for c in s.get("constituents", [])],
                    daily=[DailyBar.from_dict(d) for d in s.get("daily", [])],
                )
            )

        companies: List[CompanyData] = []
        for c in raw.get("companies", []):
            companies.append(
                CompanyData(
                    code=str(c.get("code", "")),
                    name=str(c.get("name", "")),
                    sector_id=(str(c["sector_id"]) if c.get("sector_id") is not None else None),
                    market_cap=(
                        float(c["market_cap"]) if c.get("market_cap") is not None else None
                    ),
                    daily=[DailyBar.from_dict(d) for d in c.get("daily", [])],
                )
            )

        snapshot = MarketSnapshot(
            date=str(raw.get("date", "")),
            classification_system=str(raw.get("classification_system", "concept")),
            benchmark=benchmark,
            sectors=sectors,
            companies=companies,
        )
        self._snapshot = snapshot
        return snapshot

    # -------------------------- 查询 --------------------------

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


__all__ = [
    "BenchmarkData",
    "CompanyData",
    "DailyBar",
    "FixtureRepository",
    "MarketSnapshot",
    "Repository",
    "SectorData",
]
