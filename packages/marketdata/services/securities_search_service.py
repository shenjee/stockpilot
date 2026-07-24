"""T+0 证券搜索服务。

仓储层继续由 :class:`SecuritiesStore` 负责代码、名称和拼音匹配；本模块只负责
把仓储记录收窄到 T+0 当前支持的沪深 A 股与场内 ETF，并映射为冻结的标准证券
身份。这样调用方无需理解 ``SecuritiesStore`` 的 ``type`` 或 ``pinyin`` 字段。
"""

from __future__ import annotations

from collections.abc import Mapping

from ..repositories.securities_store import SecuritiesStore
from ..t0_schema import T0_MARKETS, standardize_security_identity


DEFAULT_SEARCH_LIMIT = 50

# SecuritiesStore 当前不支持按证券类型过滤或游标翻页。服务层需要先取得完整候选
# 再过滤，否则同代码的指数记录可能占用 limit，导致合法股票无法返回。该值覆盖
# bundled master 的当前规模，并仍由仓储执行实际匹配与排序。
_STORE_CANDIDATE_LIMIT = 10_000
_SECURITY_TYPE_MAP = {
    "stock": "a_share",
    "etf": "etf",
}


class SecuritiesSearchService:
    """复用证券主数据仓储并返回 T+0 标准证券身份。"""

    def __init__(self, store: SecuritiesStore):
        self.store = store

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> list[dict[str, str]]:
        """按代码、名称或拼音搜索沪深 A 股和场内 ETF。

        结果顺序沿用 :meth:`SecuritiesStore.search` 的精确、前缀和子串优先级；
        ``limit`` 在排除指数、北交所和港股记录后应用。
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        records = self.store.search(
            query,
            limit=max(_STORE_CANDIDATE_LIMIT, limit),
        )
        results: list[dict[str, str]] = []
        for record in records:
            identity = _to_supported_identity(record)
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
    ) -> dict[str, str] | None:
        """按代码取得一个标准证券身份；主数据中不支持的证券返回 ``None``。

        未显式提供 ``market`` 时复用 T+0 的代码市场规则进行推断，避免同代码的
        指数记录先于股票记录被仓储选中。无效代码或非沪深市场沿用标准 Schema
        的校验错误。
        """

        normalized = standardize_security_identity(code, market)
        record = self.store.get(normalized["code"], normalized["market"])
        if record is None:
            return None
        return _to_supported_identity(record)


def _to_supported_identity(
    record: Mapping[str, object],
) -> dict[str, str] | None:
    market = str(record.get("market", "")).lower()
    source_type = str(record.get("type", "")).lower()
    if market not in T0_MARKETS or source_type not in _SECURITY_TYPE_MAP:
        return None

    identity = standardize_security_identity(str(record.get("code", "")), market)
    name = str(record.get("name", "")).strip()
    if not name:
        raise ValueError("security name must be non-empty")
    return {
        "symbol": identity["symbol"],
        "code": identity["code"],
        "market": identity["market"],
        "name": name,
        "security_type": _SECURITY_TYPE_MAP[source_type],
    }
