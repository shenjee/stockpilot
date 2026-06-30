from __future__ import annotations

from datetime import datetime, timedelta
import logging

from ..provider_result import MarketDataResult, ProviderIssue


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


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
        security_type: str | None = None,
    ) -> None:
        self.ensure_local_klines_result(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
        )

    def ensure_local_klines_result(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        min_local_count: int | None = None,
        security_type: str | None = None,
    ) -> MarketDataResult[None]:
        start_date = start_date or self._default_start_date(end_date)
        required_local_count = min_local_count or self._required_local_count(timeframe, start_date, end_date)
        required_latest = self._required_latest_timestamp(end_date, timeframe)
        query_end = end_date if timeframe == "day" else f"{end_date} 23:59:59"

        latest = self.store.latest_date(code, market, timeframe=timeframe)
        earliest = self.store.earliest_timestamp(code, market, timeframe=timeframe)
        local_count = self.store.count_since(code, start_date, market, timeframe=timeframe, end_date=query_end)
        if (
            latest
            and earliest
            and latest >= required_latest
            and self._covers_start_date(earliest, start_date, timeframe)
            and local_count >= required_local_count
        ):
            return MarketDataResult(success=True, data=None)

        result = self._fetch_remote_klines_result(
            code=code,
            start_date=start_date,
            end_date=end_date,
            ktype=timeframe,
            market=market,
            security_type=security_type,
        )
        self._log_error_issues(
            result.issues,
            code=code,
            start_date=start_date,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            security_type=security_type,
        )
        if result.data:
            self.store.upsert_many(code, market, result.data, source=self.provider.provider_id, timeframe=timeframe)
        return MarketDataResult(success=result.success, data=None, issues=result.issues)

    def get_klines(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        limit: int = 120,
        min_local_count: int | None = None,
        security_type: str | None = None,
    ) -> list:
        query_end_date = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        self.ensure_local_klines(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
        )
        return self.store.get_klines(
            code,
            query_end_date,
            market=market,
            limit=limit,
            timeframe=timeframe,
            start_date=start_date,
        )

    def get_klines_result(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        limit: int = 120,
        min_local_count: int | None = None,
        security_type: str | None = None,
    ) -> MarketDataResult[list]:
        query_end_date = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        sync_result = self.ensure_local_klines_result(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
        )
        rows = self.store.get_klines(
            code,
            query_end_date,
            market=market,
            limit=limit,
            timeframe=timeframe,
            start_date=start_date,
        )
        success = True if rows else sync_result.success
        return MarketDataResult(success=success, data=rows, issues=sync_result.issues)

    def _default_start_date(self, end_date: str) -> str:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        return (end_dt - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

    def _required_local_count(self, timeframe: str, start_date: str | None = None, end_date: str | None = None) -> int:
        if timeframe in MINUTE_TIMEFRAMES:
            bars_per_day = {
                "1m": 240,
                "5m": 48,
                "30m": 8,
                "60m": 4,
            }.get(timeframe, 1)
            if start_date and end_date:
                start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
                day_count = max((end_day - start_day).days + 1, 1)
                # Calendar days overestimate trading days (~67%); use a conservative
                # factor so the cache check triggers a refetch when coverage is insufficient.
                return max(int(day_count * bars_per_day * 0.6), bars_per_day)
            return bars_per_day
        return self.min_local_count

    def _required_latest_timestamp(self, end_date: str, timeframe: str) -> str:
        if timeframe in MINUTE_TIMEFRAMES:
            return f"{end_date} 15:00:00"
        return end_date

    @staticmethod
    def _covers_start_date(earliest: str, start_date: str, timeframe: str) -> bool:
        """Check whether the earliest local bar covers the requested start_date.

        For minute timeframes the stored timestamp includes a time component
        (``YYYY-MM-DD HH:MM:SS``), so only the date prefix is compared against
        the ``YYYY-MM-DD`` start_date.
        """
        if timeframe in MINUTE_TIMEFRAMES:
            return earliest[:10] <= start_date
        return earliest <= start_date

    def _fetch_remote_klines_result(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        ktype: str,
        market: str | None,
        security_type: str | None,
    ) -> MarketDataResult[list]:
        result_func = getattr(self.provider, "get_kline_result", None)
        if callable(result_func):
            return result_func(
                code=code,
                start_date=start_date,
                end_date=end_date,
                ktype=ktype,
                market=market,
                security_type=security_type,
            )
        rows = self.provider.get_kline(
            code=code,
            start_date=start_date,
            end_date=end_date,
            ktype=ktype,
            market=market,
            security_type=security_type,
        )
        return MarketDataResult(success=True, data=rows, issues=[])

    def _log_error_issues(
        self,
        issues: list[ProviderIssue],
        *,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None,
        timeframe: str,
        security_type: str | None,
    ) -> None:
        provider_id = getattr(self.provider, "provider_id", "")
        for issue in issues:
            if issue.level != "error":
                continue
            logger.warning(
                issue.message,
                extra={
                    "provider_id": provider_id,
                    "reason_code": issue.reason_code,
                    "code": code,
                    "market": market,
                    "timeframe": timeframe,
                    "start_date": start_date,
                    "end_date": end_date,
                    "security_type": security_type,
                    **(issue.context or {}),
                },
            )
