"""数据源协议接口（Phase 6A）。

真实数据源（Phase 6B+）必须实现这里声明的方法集合，但 Phase 6A 只锁定签名，
不强制每个方法都被实现——``FakeFundamentalDataSource`` 可以按需 override 其中
任意一部分。所有方法返回轻量 Python 结构（dict/list），不能直接写 SQLite。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class FundamentalDataSource(Protocol):
    """基本面数据源协议。

    方法返回值约定：
    - 列表元素都是 dict，字段名使用 ``snake_case``。
    - 每条记录至少包含数据源相关字段（``source``、``source_updated_at``），
      ``fetch_run_id`` 由同步层写入，不由数据源决定。
    - 缺失字段使用 ``None``，不要用 ``0`` 占位。
    """

    name: str

    def list_sectors(self, classification_system: str) -> List[Dict[str, Any]]:
        ...

    def get_sector_constituents(
        self, sector_id: str, classification_system: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        ...

    def get_sector_daily(
        self,
        sector_id: str,
        classification_system: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        ...

    def get_benchmark_daily(
        self, benchmark: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        ...

    def get_stock_universe(self, as_of_date: str) -> List[Dict[str, Any]]:
        ...

    def get_company_daily_snapshot(self, trade_date: str) -> List[Dict[str, Any]]:
        ...

    def get_company_valuation_history(
        self, codes: List[str], start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        ...

    def get_financial_metrics(
        self, codes: List[str], as_of_date: str
    ) -> List[Dict[str, Any]]:
        ...


__all__ = ["FundamentalDataSource"]
