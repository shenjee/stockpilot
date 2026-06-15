from __future__ import annotations

from datetime import datetime, timedelta


DEFAULT_LOOKBACK_DAYS = 140
DEFAULT_MIN_LOCAL_COUNT = 60
MINUTE_TIMEFRAMES = {"1m", "5m", "30m", "60m"}


class KLineDataService:
    """统一的 K 线读取与同步流程。"""

    def __init__(self, provider, store, lookback_days: int = DEFAULT_LOOKBACK_DAYS, min_local_count: int = DEFAULT_MIN_LOCAL_COUNT):
        self.provider = provider
        self.store = store
        self.lookback_days = lookback_days
        self.min_local_count = min_local_count

    def ensure_local_klines(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        min_local_count: int | None = None,
    ) -> None:
        start_date = start_date or self._default_start_date(end_date)
        required_local_count = min_local_count or self._required_local_count(timeframe)
        required_latest = self._required_latest_timestamp(end_date, timeframe)

        latest = self.store.latest_date(code, market, timeframe=timeframe)
        local_count = self.store.count_since(code, start_date, market, timeframe=timeframe)
        if latest and latest >= required_latest and local_count >= required_local_count:
            return

        klines = self.provider.get_kline(
            code=code,
            start_date=start_date,
            end_date=end_date,
            ktype=timeframe,
            market=market,
        )
        if klines:
            self.store.upsert_many(code, market, klines, source=self.provider.provider_id, timeframe=timeframe)

    def get_klines(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        limit: int = 120,
        min_local_count: int | None = None,
    ) -> list:
        query_end_date = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        self.ensure_local_klines(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
        )
        return self.store.get_klines(
            code,
            query_end_date,
            market=market,
            limit=limit,
            timeframe=timeframe,
            start_date=start_date,
        )

    def _default_start_date(self, end_date: str) -> str:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        return (end_dt - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

    def _required_local_count(self, timeframe: str) -> int:
        if timeframe in MINUTE_TIMEFRAMES:
            return {
                "1m": 240,
                "5m": 48,
                "30m": 8,
                "60m": 4,
            }.get(timeframe, 1)
        return self.min_local_count

    def _required_latest_timestamp(self, end_date: str, timeframe: str) -> str:
        if timeframe in MINUTE_TIMEFRAMES:
            return f"{end_date} 15:00:00"
        return end_date
