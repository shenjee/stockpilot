"""Standalone copy of the shared local-first K-line data service."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
import logging

from ..provider_request_queue import (
    ProviderQueueClosedError,
    ProviderQueueFullError,
    ProviderRequestPriority,
    ProviderRequestQueue,
    get_shared_provider_request_queue,
)
from ..provider_result import MarketDataResult, ProviderIssue


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


DEFAULT_LOOKBACK_DAYS = 140
DEFAULT_MIN_LOCAL_COUNT = 60
MINUTE_TIMEFRAMES = {"1m", "5m", "30m", "60m"}


class KLineDataService:
    """Keep standalone skill behavior aligned with ``packages.marketdata``."""

    def __init__(
        self,
        provider,
        store,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        min_local_count: int = DEFAULT_MIN_LOCAL_COUNT,
        *,
        market_context=None,
        provider_queue: ProviderRequestQueue | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.provider = provider
        self.store = store
        self.lookback_days = lookback_days
        self.min_local_count = min_local_count
        self.market_context = market_context
        self.provider_queue = provider_queue or get_shared_provider_request_queue()
        self.clock = clock or datetime.now

    def ensure_local_klines(
        self,
        code: str,
        end_date: str,
        market: str | None = None,
        timeframe: str = "day",
        start_date: str | None = None,
        min_local_count: int | None = None,
        security_type: str | None = None,
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator: Callable[[], bool] | None = None,
        provider_max_attempts: int = 1,
    ) -> None:
        self.ensure_local_klines_result(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
            request_priority=request_priority,
            session_validator=session_validator,
            provider_max_attempts=provider_max_attempts,
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
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator: Callable[[], bool] | None = None,
        provider_max_attempts: int = 1,
    ) -> MarketDataResult[None]:
        start_date = start_date or self._default_start_date(end_date)
        self._validate_date_range(start_date, end_date)
        missing_ranges = self.identify_missing_ranges(
            code=code,
            start_date=start_date,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
        )
        if not missing_ranges:
            return MarketDataResult(success=True, data=None)

        issues: list[ProviderIssue] = []
        success = True
        for missing_start, missing_end in missing_ranges:
            result = self._fetch_remote_klines_result(
                code=code,
                start_date=missing_start,
                end_date=missing_end,
                ktype=timeframe,
                market=market,
                security_type=security_type,
                request_priority=request_priority,
                session_validator=session_validator,
                provider_max_attempts=provider_max_attempts,
            )
            issues.extend(result.issues)
            success = success and result.success
            self._log_error_issues(
                result.issues,
                code=code,
                start_date=missing_start,
                end_date=missing_end,
                market=market,
                timeframe=timeframe,
                security_type=security_type,
            )
            if result.data:
                self.store.upsert_many(
                    code,
                    market,
                    result.data,
                    source=self.provider.provider_id,
                    timeframe=timeframe,
                )
            provider_completed_successfully = result.success or (
                any(
                    issue.reason_code == "session_retired"
                    for issue in result.issues
                )
                and not any(
                    issue.level == "error"
                    and issue.reason_code != "session_retired"
                    for issue in result.issues
                )
            )
            if provider_completed_successfully:
                historical_end = min(
                    date.fromisoformat(missing_end),
                    self.clock().date() - timedelta(days=1),
                )
                if date.fromisoformat(missing_start) <= historical_end:
                    self.store.mark_coverage(
                        code,
                        market,
                        missing_start,
                        historical_end.isoformat(),
                        source=self.provider.provider_id,
                        timeframe=timeframe,
                    )
        return MarketDataResult(success=success, data=None, issues=issues)

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
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator: Callable[[], bool] | None = None,
        provider_max_attempts: int = 1,
    ) -> list:
        query_end = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        self.ensure_local_klines(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
            request_priority=request_priority,
            session_validator=session_validator,
            provider_max_attempts=provider_max_attempts,
        )
        return self.store.get_klines(
            code,
            query_end,
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
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator: Callable[[], bool] | None = None,
        provider_max_attempts: int = 1,
    ) -> MarketDataResult[list]:
        query_end = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        sync_result = self.ensure_local_klines_result(
            code=code,
            end_date=end_date,
            market=market,
            timeframe=timeframe,
            start_date=start_date,
            min_local_count=min_local_count,
            security_type=security_type,
            request_priority=request_priority,
            session_validator=session_validator,
            provider_max_attempts=provider_max_attempts,
        )
        rows = self.store.get_klines(
            code,
            query_end,
            market=market,
            limit=limit,
            timeframe=timeframe,
            start_date=start_date,
        )
        return MarketDataResult(
            success=True if rows else sync_result.success,
            data=rows,
            issues=sync_result.issues,
        )

    def identify_missing_ranges(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None,
        timeframe: str,
    ) -> list[tuple[str, str]]:
        self._validate_date_range(start_date, end_date)
        timestamps = self.store.timestamps_between(
            code,
            start_date,
            end_date,
            market=market,
            timeframe=timeframe,
        )
        coverage = self.store.coverage_ranges(
            code,
            start_date,
            end_date,
            market=market,
            timeframe=timeframe,
        )
        covered_dates = _dates_from_ranges(coverage, start_date, end_date)
        active_date = self.clock().date().isoformat()
        covered_dates.discard(active_date)
        query_end = end_date if timeframe == "day" else f"{end_date} 23:59:59"
        invalid_dates = self.store.negative_price_dates(
            code,
            start_date,
            market=market,
            timeframe=timeframe,
            end_date=query_end,
        )
        if timeframe == "day":
            covered_dates.update(
                timestamp[:10]
                for timestamp in timestamps
                if timestamp[:10] != active_date
            )
        else:
            covered_dates.update(
                self._complete_minute_dates(timestamps, timeframe)
            )
        covered_dates.difference_update(invalid_dates)
        required_dates = self._required_dates(
            start_date=start_date,
            end_date=end_date,
            market=market,
        )
        missing_dates = [
            value for value in required_dates if value not in covered_dates
        ]
        return _group_missing_dates(missing_dates, required_dates)

    def _required_dates(
        self,
        *,
        start_date: str,
        end_date: str,
        market: str | None,
    ) -> list[str]:
        if self.market_context is not None and market is not None:
            return [
                value.isoformat()
                for value in self.market_context.trading_days_between(
                    start_date,
                    end_date,
                    market,
                )
            ]
        return [
            value.isoformat()
            for value in _date_range(
                date.fromisoformat(start_date),
                date.fromisoformat(end_date),
            )
            if value.weekday() < 5
        ]

    @staticmethod
    def _complete_minute_dates(
        timestamps: Sequence[str],
        timeframe: str,
    ) -> set[str]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for timestamp in timestamps:
            grouped[timestamp[:10]].append(timestamp)
        bars_per_day = {
            "1m": 240,
            "5m": 48,
            "30m": 8,
            "60m": 4,
        }.get(timeframe)
        if bars_per_day is None:
            return set()
        return {
            day
            for day, values in grouped.items()
            if len(values) >= bars_per_day
            and max(values).endswith("15:00:00")
        }

    def _default_start_date(self, end_date: str) -> str:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        return (end_dt - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")

    def _fetch_remote_klines_result(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        ktype: str,
        market: str | None,
        security_type: str | None,
        request_priority: ProviderRequestPriority,
        session_validator: Callable[[], bool] | None,
        provider_max_attempts: int,
    ) -> MarketDataResult[list]:
        def operation() -> MarketDataResult[list]:
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

        request_key = (
            getattr(self.provider, "provider_id", type(self.provider).__name__),
            id(self.provider),
            "get_kline",
            code,
            start_date,
            end_date,
            ktype,
            market,
            security_type,
        )
        try:
            outcome = self.provider_queue.execute(
                request_key,
                operation,
                priority=request_priority,
                session_validator=session_validator,
                max_attempts=provider_max_attempts,
            )
        except (ProviderQueueFullError, ProviderQueueClosedError) as exc:
            reason_code = (
                "provider_queue_full"
                if isinstance(exc, ProviderQueueFullError)
                else "provider_queue_closed"
            )
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code=reason_code,
                        message="provider request could not be scheduled",
                        context={"operation": "get_kline"},
                        exception_type=type(exc).__name__,
                    )
                ],
            )
        if not outcome.executed:
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code="session_retired",
                        message="provider request skipped for retired session",
                    )
                ],
            )
        if not outcome.session_valid:
            provider_result = outcome.result
            return MarketDataResult(
                success=False,
                data=provider_result.data,
                issues=[
                    *provider_result.issues,
                    ProviderIssue(
                        level="error",
                        reason_code="session_retired",
                        message="provider result belongs to a retired session",
                    ),
                ],
            )
        return outcome.result

    @staticmethod
    def _validate_date_range(start_date: str, end_date: str) -> None:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("start_date must not exceed end_date")

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


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _dates_from_ranges(
    ranges: Sequence[tuple[str, str]],
    requested_start: str,
    requested_end: str,
) -> set[str]:
    lower = date.fromisoformat(requested_start)
    upper = date.fromisoformat(requested_end)
    values: set[str] = set()
    for start_value, end_value in ranges:
        start = max(date.fromisoformat(start_value), lower)
        end = min(date.fromisoformat(end_value), upper)
        values.update(item.isoformat() for item in _date_range(start, end))
    return values


def _group_missing_dates(
    missing_dates: Sequence[str],
    required_dates: Sequence[str],
) -> list[tuple[str, str]]:
    if not missing_dates:
        return []
    missing = set(missing_dates)
    groups: list[tuple[str, str]] = []
    group_start: str | None = None
    previous: str | None = None
    for value in required_dates:
        if value in missing:
            group_start = group_start or value
            previous = value
        elif group_start is not None:
            groups.append((group_start, previous or group_start))
            group_start = None
            previous = None
    if group_start is not None:
        groups.append((group_start, previous or group_start))
    return groups
