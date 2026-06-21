"""Fundamental Screener 数据源抽象包（Phase 6A 骨架）。

真实数据源只负责读取外部数据并返回标准化前的结构化结果；SQLite 写入、
质量检查和 ``MarketSnapshot`` 组装属于同步与 repository 层。

Phase 6A 仅提供：
- ``FundamentalDataSource`` 协议接口。
- ``FakeFundamentalDataSource``：内存中的可控数据源，供单元测试使用。

Phase 6B 起会加入 ``AkShareFundamentalDataSource`` 真实实现。
"""

from .base import FundamentalDataSource
from .fake_source import FakeFundamentalDataSource

__all__ = [
    "FakeFundamentalDataSource",
    "FundamentalDataSource",
]
