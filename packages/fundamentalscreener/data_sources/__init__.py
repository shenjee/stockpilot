"""Fundamental Screener 数据源抽象包。

真实数据源只负责读取外部数据并返回标准化前的结构化结果；SQLite 写入、
质量检查和 ``MarketSnapshot`` 组装属于同步与 repository 层。

提供：
- ``FundamentalDataSource`` 协议接口。
- ``FakeFundamentalDataSource``：内存中的可控数据源，供单元测试使用。
- ``AkShareFundamentalDataSource``：行业板块真实实现，默认使用同花顺行业板块
  （``ths_industry`` 口径），东方财富（``em_industry``）作为对照源。
  ``akshare`` 惰性导入，支持依赖注入便于无网络测试。
"""

from .akshare_source import AkShareFundamentalDataSource
from .base import FundamentalDataSource
from .fake_source import FakeFundamentalDataSource

__all__ = [
    "AkShareFundamentalDataSource",
    "FakeFundamentalDataSource",
    "FundamentalDataSource",
]
