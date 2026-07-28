from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
import logging

from ..provider_request_queue import (
    ProviderQueueClosedError,
    ProviderQueueFullError,
    ProviderWaitTimeoutError,
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
_RELIABILITY_COMPLETE = "complete"
_RELIABILITY_NO_DATA = "no_data"
_RELIABILITY_INCOMPLETE = "incomplete"
_RELIABILITY_UNKNOWN = "unknown"
_REPLAY_RELIABILITY_EVIDENCE_REASON = "replay_reliability_evidence"


class KLineDataService:
    """统一的 K 线读取与同步流程。"""

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
        request_timeout: float | None = None,
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
            request_timeout=request_timeout,
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
        request_timeout: float | None = None,
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
                request_timeout=request_timeout,
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
            if timeframe in MINUTE_TIMEFRAMES:
                minute_statuses = self._minute_reliability_statuses(
                    market=market,
                    timeframe=timeframe,
                    start_date=missing_start,
                    end_date=missing_end,
                    result=result,
                )
                self._record_replay_reliability(
                    code=code,
                    market=market,
                    timeframe=timeframe,
                    statuses=minute_statuses,
                )
                historical_end = min(
                    date.fromisoformat(missing_end),
                    self.clock().date() - timedelta(days=1),
                )
                if date.fromisoformat(missing_start) <= historical_end:
                    covered_days = [
                        trade_date
                        for trade_date, status in minute_statuses.items()
                        if trade_date <= historical_end.isoformat()
                        and status in {_RELIABILITY_COMPLETE, _RELIABILITY_NO_DATA}
                    ]
                    for covered_start, covered_end in _group_present_dates(covered_days):
                        self.store.mark_coverage(
                            code,
                            market,
                            covered_start,
                            covered_end,
                            source=self.provider.provider_id,
                            timeframe=timeframe,
                        )
            else:
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
        request_timeout: float | None = None,
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
            request_priority=request_priority,
            session_validator=session_validator,
            provider_max_attempts=provider_max_attempts,
            request_timeout=request_timeout,
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
        request_priority: ProviderRequestPriority = ProviderRequestPriority.LIVE,
        session_validator: Callable[[], bool] | None = None,
        provider_max_attempts: int = 1,
        request_timeout: float | None = None,
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
            request_priority=request_priority,
            session_validator=session_validator,
            provider_max_attempts=provider_max_attempts,
            request_timeout=request_timeout,
        )
        rows = self.store.get_klines(
            code,
            query_end_date,
            market=market,
            limit=limit,
            timeframe=timeframe,
            start_date=start_date,
        )
        # A retired Session invalidates publication of its remote result, not
        # already-persisted local facts.  Preserve the issue for the Session
        # caller while allowing consumers to use valid cached rows.
        success = True if rows else sync_result.success
        return MarketDataResult(success=success, data=rows, issues=sync_result.issues)

    def replay_reliability_evidence(
        self,
        *,
        code: str,
        trade_date: str,
        market: str | None,
        timeframe: str,
    ) -> bool | None:
        if (
            timeframe in MINUTE_TIMEFRAMES
            and trade_date == self.clock().date().isoformat()
        ):
            return False
        status = self.store.get_replay_reliability(
            code,
            trade_date,
            market=market,
            timeframe=timeframe,
        )
        if status == _RELIABILITY_COMPLETE:
            return True
        if status in {
            _RELIABILITY_NO_DATA,
            _RELIABILITY_INCOMPLETE,
            _RELIABILITY_UNKNOWN,
        }:
            return False
        return None

    def identify_missing_ranges(
        self,
        *,
        code: str,
        start_date: str,
        end_date: str,
        market: str | None,
        timeframe: str,
    ) -> list[tuple[str, str]]:
        """Return inclusive provider date ranges not proven complete locally.

        Successful no-data responses are represented by repository coverage,
        which prevents holidays or suspensions from being fetched forever.
        When an authoritative market calendar is injected, internal trading-day
        holes are found without guessing from weekdays.
        """

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
        # A successful intraday response only covers the bars available at that
        # moment; it must never suppress a later Live refresh for the same day.
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
            reliability = self.store.replay_reliability_between(
                code,
                start_date,
                end_date,
                market=market,
                timeframe=timeframe,
            )
            covered_dates.update(
                trade_date
                for trade_date, status in reliability.items()
                if status in {_RELIABILITY_COMPLETE, _RELIABILITY_NO_DATA}
            )
            covered_dates.difference_update(
                trade_date
                for trade_date, status in reliability.items()
                if status in {_RELIABILITY_INCOMPLETE, _RELIABILITY_UNKNOWN}
            )
            covered_dates.discard(active_date)
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
        request_timeout: float | None,
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
                timeout=request_timeout,
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
        except ProviderWaitTimeoutError:
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code="request_timeout",
                        message="provider request wait exceeded timeout",
                        context={"operation": "get_kline"},
                        exception_type="TimeoutError",
                    )
                ],
            )
        except Exception as exc:
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code="request_failed",
                        message="provider request failed during execution",
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
        if outcome.subscriber_detached:
            return MarketDataResult(
                success=False,
                data=[],
                issues=[
                    ProviderIssue(
                        level="error",
                        reason_code="session_retired",
                        message="provider request detached after session retired",
                        context={
                            "operation": "get_kline",
                            "executed": outcome.executed,
                        },
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

    def _minute_reliability_statuses(
        self,
        *,
        market: str | None,
        timeframe: str,
        start_date: str,
        end_date: str,
        result: MarketDataResult[list],
    ) -> dict[str, str]:
        requested_dates = self._required_dates(
            start_date=start_date,
            end_date=end_date,
            market=market,
        )
        if not requested_dates:
            return {}
        evidence = self._extract_minute_reliability_evidence(result)
        if evidence is None:
            issue_codes = {issue.reason_code for issue in result.issues}
            fallback = (
                _RELIABILITY_INCOMPLETE
                if result.errors() or "parse_failed" in issue_codes
                else _RELIABILITY_UNKNOWN
            )
            return {trade_date: fallback for trade_date in requested_dates}
        default_status = evidence.get("default_status", _RELIABILITY_UNKNOWN)
        explicit_statuses = evidence.get("trade_date_statuses", {})
        statuses = {
            trade_date: explicit_statuses.get(trade_date, default_status)
            for trade_date in requested_dates
        }
        active_date = self.clock().date().isoformat()
        if statuses.get(active_date) in {
            _RELIABILITY_COMPLETE,
            _RELIABILITY_NO_DATA,
        }:
            statuses[active_date] = _RELIABILITY_UNKNOWN
        return statuses

    @staticmethod
    def _extract_minute_reliability_evidence(
        result: MarketDataResult[list],
    ) -> dict[str, object] | None:
        for issue in result.issues:
            if issue.reason_code != _REPLAY_RELIABILITY_EVIDENCE_REASON:
                continue
            context = issue.context or {}
            explicit = context.get("trade_date_statuses", {})
            if not isinstance(explicit, dict):
                explicit = {}
            default_status = str(
                context.get("default_status", _RELIABILITY_UNKNOWN)
            )
            return {
                "default_status": default_status,
                "trade_date_statuses": {
                    str(trade_date): str(status)
                    for trade_date, status in explicit.items()
                },
            }
        return None

    def _record_replay_reliability(
        self,
        *,
        code: str,
        market: str | None,
        timeframe: str,
        statuses: dict[str, str],
    ) -> None:
        if timeframe not in MINUTE_TIMEFRAMES:
            return
        source = getattr(self.provider, "provider_id", "unknown")
        for trade_date, status in statuses.items():
            self.store.set_replay_reliability(
                code,
                market,
                trade_date,
                timeframe=timeframe,
                status=status,
                source=source,
            )

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


def _group_present_dates(
    present_dates: Sequence[str],
) -> list[tuple[str, str]]:
    if not present_dates:
        return []
    ordered = sorted(set(present_dates))
    groups: list[tuple[str, str]] = []
    group_start = ordered[0]
    previous = ordered[0]
    for value in ordered[1:]:
        if date.fromisoformat(value) == date.fromisoformat(previous) + timedelta(days=1):
            previous = value
            continue
        groups.append((group_start, previous))
        group_start = value
        previous = value
    groups.append((group_start, previous))
    return groups
