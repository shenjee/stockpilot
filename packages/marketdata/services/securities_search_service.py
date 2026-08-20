"""T+0 证券搜索服务。

仓储层继续由 :class:`SecuritiesStore` 负责代码、名称和拼音匹配；本模块把
仓储记录映射为权威的 :class:`InstrumentIdentity`（客观证券身份），不再以
交易费用语义的 ``security_type`` 过滤结果。沪深 A 股、场内 ETF 和指数均会
返回；交易/费用资格由交易边界 (:class:`TradeService`) 独立校验，不在搜索层
耦合（issue #151）。
"""

from __future__ import annotations

from collections.abc import Mapping

from ..repositories.securities_store import SecuritiesStore
from ..t0_schema import (
    T0_MARKETS,
    InstrumentIdentity,
    InstrumentType,
    MarketDataSchemaError,
    _INSTRUMENT_TYPE_MAP,
    standardize_security_identity,
)


DEFAULT_SEARCH_LIMIT = 50

# SecuritiesStore 当前不支持按证券类型过滤或游标翻页。服务层需要先取得完整候选
# 再映射，否则同代码的指数记录可能占用 limit，导致合法股票无法返回。该值覆盖
# bundled master 的当前规模，并仍由仓储执行实际匹配与排序。
_STORE_CANDIDATE_LIMIT = 10_000


class SecuritiesSearchService:
    """复用证券主数据仓储并返回权威 :class:`InstrumentIdentity`。"""

    def __init__(self, store: SecuritiesStore):
        self.store = store

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[InstrumentIdentity]:
        """按代码、名称或拼音搜索沪深 A 股、场内 ETF 和指数。

        结果顺序沿用 :meth:`SecuritiesStore.search` 的精确、前缀和子串优先级；
        ``limit`` 在排除北交所和港股等非沪深记录后应用。指数不再被过滤——
        能否看行情与能否记录成交是独立的资格边界（issue #151）。
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        records = self.store.search(
            query,
            limit=max(_STORE_CANDIDATE_LIMIT, limit),
        )
        results: list[InstrumentIdentity] = []
        for record in records:
            identity = _to_instrument_identity(record)
            if identity is None:
                continue
            results.append(identity)
            if len(results) == limit:
                break
        return results

    def get(
        self,
        code: str,
        market: str | None = None,
    ) -> InstrumentIdentity | None:
        """按代码取得一个权威 :class:`InstrumentIdentity`；主数据中不支持的证券返回 ``None``。

        未显式提供 ``market`` 时复用 T+0 的代码市场规则进行推断，避免同代码的
        指数记录先于股票记录被仓储选中。无效代码或非沪深市场沿用标准 Schema
        的校验错误。
        """

        normalized = standardize_security_identity(code, market)
        record = self.store.get(normalized["code"], normalized["market"])
        if record is None:
            return None
        return _to_instrument_identity(record)

    def resolve(self, symbol: str) -> InstrumentIdentity | None:
        """按权威 symbol (``sh.600000``) 解析身份。

        这是 App/API 编排入口使用的窄端口方法：一次选择只解析一次，随后
        Session、Live、Replay 和 Historical 都使用同一份不可变身份。
        """

        normalized = standardize_security_identity(symbol)
        record = self.store.get(normalized["code"], normalized["market"])
        if record is None:
            return None
        return _to_instrument_identity(record)


def _to_instrument_identity(
    record: Mapping[str, object],
) -> InstrumentIdentity | None:
    market = str(record.get("market", "")).lower()
    source_type = str(record.get("type", "")).lower()
    if market not in T0_MARKETS or source_type not in _INSTRUMENT_TYPE_MAP:
        return None

    identity = standardize_security_identity(str(record.get("code", "")), market)
    name = str(record.get("name", "")).strip()
    if not name:
        raise ValueError("security name must be non-empty")
    return InstrumentIdentity(
        symbol=identity["symbol"],
        code=identity["code"],
        market=identity["market"],
        name=name,
        instrument_type=_INSTRUMENT_TYPE_MAP[source_type],
    )
