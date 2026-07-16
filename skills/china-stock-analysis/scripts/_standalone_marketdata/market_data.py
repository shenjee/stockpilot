"""Shared market data providers for StockPilot."""

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .provider_result import MarketDataResult, ProviderIssue


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


INDICES = {
    "sh000001": ("上证指数", "SH"),
    "sz399001": ("深证成指", "SZ"),
    "sz399006": ("创业板指", "SZ"),
    "sh000016": ("上证50", "SH"),
    "sh000300": ("沪深300", "SH"),
    "sh000905": ("中证500", "SH"),
    "sh000852": ("中证1000", "SH"),
    "sh000688": ("科创50", "SH"),
    "sz899050": ("北证50", "BJ"),
}


def get_market_prefix(code: str, market: str = None) -> str:
    """Return exchange prefix, preferring explicit config over code inference.

    按 6 位代码首字符推断沪深京三市场：
    - 6 / 5 -> sh（沪市股票 6xxxxx、沪市 ETF 5xxxxx，含 510/511/512/513/588 等）
    - 0 / 3 / 1 -> sz（深市股票 0/3 开头、深市 ETF 159xxx 与分级 150xxx）
    - 4 / 8 / 9 -> bj（北交所股票 8/4 开头，以及 920xxx 新股以 9 开头）

    港股需调用方显式传入 market="hk"，本函数不做代码推断。

    其余兜底 sh，保持向后兼容。注意：指数代码（如 000001 上证指数）首字符是 0，
    会被推断成 sz，因此指数必须由调用方显式传入 market，不能依赖本函数。
    """
    if market:
        return market
    if code.startswith(("6", "5")):
        return "sh"
    if code.startswith(("0", "3", "1")):
        return "sz"
    if code.startswith(("8", "4", "9")):
        return "bj"
    return "sh"


def normalize_hk_code(code: str) -> str:
    """将港股代码补零到 5 位数字。

    腾讯财经 API 要求港股代码为 5 位数字（如 "00175"、"03896"）。
    用户常以 4 位形式输入（如 "0175"、"3896"），需要补前导零到 5 位。

    已经是 5 位数字的代码原样返回。非纯数字或超过 5 位的代码原样返回，
    交由调用方/上游 API 处理。
    """
    if code.isdigit() and len(code) < 5:
        return code.zfill(5)
    return code


class MarketDataProvider:
    """Provider contract used by the report generator."""

    provider_id = "market-data"
    name = "market-data"

    def realtime(self, codes, markets=None):
        raise NotImplementedError

    def get_kline(self, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None, security_type: str | None = None) -> list:
        raise NotImplementedError

    def get_daily_quote(self, code: str, trade_date: str, autype: str = "qfq", market: str = None, security_type: str | None = None) -> dict:
        raise NotImplementedError


class TencentStockDataProvider(MarketDataProvider):
    """Tencent Finance stock data provider using only Python standard library."""

    provider_id = "tencent"
    name = "腾讯财经"
    TIMEOUT = 10
    MAX_RETRIES = 3
    MINUTE_KTYPES = {
        "1m": "m1",
        "5m": "m5",
        "30m": "m30",
        "60m": "m60",
    }
    MINUTE_KLINE_PAGE_SIZE = 800
    MINUTE_KLINE_MAX_PAGES = 100

    @staticmethod
    def _make_issue(
        *,
        level: str,
        reason_code: str,
        message: str,
        context: dict | None = None,
        exc: Exception | None = None,
    ) -> ProviderIssue:
        return ProviderIssue(
            level=level,
            reason_code=reason_code,
            message=message,
            context=dict(context or {}),
            exception_type=exc.__class__.__name__ if exc else "",
        )

    @staticmethod
    def _with_context(issue: ProviderIssue, extra: dict) -> ProviderIssue:
        merged = dict(issue.context or {})
        merged.update(extra)
        return ProviderIssue(
            level=issue.level,
            reason_code=issue.reason_code,
            message=issue.message,
            context=merged,
            exception_type=issue.exception_type,
        )

    @classmethod
    def _log_errors(cls, issues: list[ProviderIssue]) -> None:
        for issue in issues:
            if issue.level != "error":
                continue
            logger.warning(
                issue.message,
                extra={
                    "provider_id": cls.provider_id,
                    "reason_code": issue.reason_code,
                    **(issue.context or {}),
                },
            )

    @staticmethod
    def _get_prefix(code: str, market: str = None) -> str:
        return get_market_prefix(code, market)

    @staticmethod
    def _normalize_code(code: str, market: str = None) -> str:
        """归一化证券代码。

        港股代码补零到 5 位数字，其余市场原样返回。
        """
        if market == "hk":
            return normalize_hk_code(code)
        return code

    @classmethod
    def _fetch_with_retry(cls, url: str, decode: str = "gbk") -> str:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
        })

        last_error = None
        for attempt in range(cls.MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=cls.TIMEOUT) as resp:
                    return resp.read().decode(decode)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                last_error = e
                if attempt < cls.MAX_RETRIES - 1:
                    time.sleep(1 * (attempt + 1))
                continue

        raise last_error or Exception("Request failed after retries")

    @classmethod
    def realtime_result(cls, codes, markets=None) -> MarketDataResult[list | dict]:
        requested_single = isinstance(codes, str) or (
            isinstance(codes, list) and len(codes) == 1
        )
        if isinstance(codes, str):
            codes = [codes]

        code_str_parts = []
        for i, code_item in enumerate(codes):
            if isinstance(code_item, tuple):
                code, market = code_item
                norm = cls._normalize_code(code, market)
                code_str_parts.append(f"{cls._get_prefix(norm, market)}{norm}")
            elif code_item.startswith("hk"):
                # hk 前缀需拆分后补零：hk0175 -> hk00175
                prefix = "hk"
                raw = code_item[2:]
                norm = cls._normalize_code(raw, "hk")
                code_str_parts.append(f"{prefix}{norm}")
            elif code_item.startswith(("sh", "sz", "bj")):
                code_str_parts.append(code_item)
            else:
                market = markets[i] if markets else None
                norm = cls._normalize_code(code_item, market)
                code_str_parts.append(f"{cls._get_prefix(norm, market)}{norm}")

        url = f"https://qt.gtimg.cn/q={','.join(code_str_parts)}"

        try:
            data = cls._fetch_with_retry(url, decode="gbk")
        except Exception as exc:
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="request_failed",
                    message="tencent realtime request failed",
                    context={"operation": "realtime", "requested": len(code_str_parts)},
                    exc=exc,
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        results = []
        parse_failures = 0
        for line in data.strip().split(";"):
            if "v_" not in line or '"' not in line:
                continue
            try:
                parts = line.split('"')[1].split("~")
                if len(parts) < 35:
                    continue

                price = float(parts[3])
                pre_close = float(parts[4])
                change = price - pre_close
                change_pct = (change / pre_close * 100) if pre_close > 0 else 0

                results.append(
                    {
                        "name": parts[1],
                        "code": parts[2],
                        "price": price,
                        "pre_close": pre_close,
                        "open": float(parts[5]),
                        "high": float(parts[33]),
                        "low": float(parts[34]),
                        "volume": int(float(parts[6])),
                        "amount": float(parts[37]) if len(parts) > 37 else 0,
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                    }
                )
            except Exception:
                parse_failures += 1
                continue

        issues: list[ProviderIssue] = []
        if parse_failures:
            issues.append(
                cls._make_issue(
                    level="warning",
                    reason_code="parse_failed",
                    message="tencent realtime parse failed",
                    context={
                        "operation": "realtime",
                        "failed_count": parse_failures,
                        "parsed_count": len(results),
                    },
                )
            )

        if not results:
            issues.append(
                cls._make_issue(
                    level="warning",
                    reason_code="no_data",
                    message="tencent realtime returned no data",
                    context={"operation": "realtime", "requested": len(code_str_parts)},
                )
            )
            return MarketDataResult(success=True, data=[], issues=issues)

        payload = results[0] if requested_single and len(results) == 1 else results
        return MarketDataResult(success=True, data=payload, issues=issues)

    @classmethod
    def realtime(cls, codes, markets=None):
        result = cls.realtime_result(codes, markets=markets)
        return result.data

    @classmethod
    def get_kline_result(
        cls,
        code: str,
        start_date: str,
        end_date: str,
        ktype: str = "day",
        autype: str = "qfq",
        market: str = None,
        security_type: str | None = None,
    ) -> MarketDataResult[list]:
        if ktype in cls.MINUTE_KTYPES:
            return cls.get_minute_kline_result(
                code=code,
                start_date=start_date,
                end_date=end_date,
                ktype=ktype,
                market=market,
            )

        if security_type == "index":
            autype = ""

        norm = cls._normalize_code(code, market)
        prefix = cls._get_prefix(norm, market)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{norm},{ktype},{start_date},{end_date},500,{autype}"

        try:
            data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
        except Exception as exc:
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="request_failed",
                    message="tencent kline request failed",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "security_type": security_type,
                    },
                    exc=exc,
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        if not isinstance(data, dict):
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="unexpected_response_shape",
                    message="tencent kline response is not a dict",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "security_type": security_type,
                        "response_type": type(data).__name__,
                    },
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        provider_code = data.get("code")
        if provider_code != 0:
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="provider_nonzero_code",
                    message="tencent kline provider returned nonzero code",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "provider_code": provider_code,
                    },
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        key = f"{autype}day" if ktype == "day" else f"{autype}{ktype}"
        base_key = "day" if ktype == "day" else ktype
        payload = data.get("data", {})
        if not isinstance(payload, dict):
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="unexpected_response_shape",
                    message="tencent kline payload is not a dict",
                    context={"operation": "get_kline", "code": code, "market": market},
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        symbol_payload = payload.get(f"{prefix}{norm}", {})
        if not isinstance(symbol_payload, dict):
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="unexpected_response_shape",
                    message="tencent kline symbol payload is not a dict",
                    context={"operation": "get_kline", "code": code, "market": market},
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        klines = symbol_payload.get(key, [])
        fallback_to_base = False
        # 港股不论 autype 均返回 base_key（如 "day"），在 autype 键缺失时回退
        if not klines and autype and base_key != key and market == "hk":
            klines = symbol_payload.get(base_key, [])
            fallback_to_base = True
        if not isinstance(klines, list):
            issues = [
                cls._make_issue(
                    level="error",
                    reason_code="unexpected_response_shape",
                    message="tencent kline series is not a list",
                    context={"operation": "get_kline", "code": code, "market": market},
                )
            ]
            cls._log_errors(issues)
            return MarketDataResult(success=False, data=[], issues=issues)

        if not klines:
            issues = [
                cls._make_issue(
                    level="warning",
                    reason_code="no_data",
                    message="tencent kline returned no data",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "security_type": security_type,
                    },
                )
            ]
            return MarketDataResult(success=True, data=[], issues=issues)

        results = []
        parse_failures = 0
        for item in klines:
            try:
                results.append(
                    {
                        "date": item[0],
                        "open": round(float(item[1]), 2),
                        "close": round(float(item[2]), 2),
                        "high": round(float(item[3]), 2),
                        "low": round(float(item[4]), 2),
                        "volume": int(float(item[5])),
                    }
                )
            except Exception:
                parse_failures += 1
                continue

        issues: list[ProviderIssue] = []
        if fallback_to_base:
            issues.append(
                cls._make_issue(
                    level="warning",
                    reason_code="adjustment_unavailable",
                    message="tencent qfq kline key not found, using base day series",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "requested_key": key,
                        "fallback_key": base_key,
                    },
                )
            )
        if parse_failures:
            issues.append(
                cls._make_issue(
                    level="warning" if results else "error",
                    reason_code="parse_failed",
                    message="tencent kline parse failed",
                    context={
                        "operation": "get_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "failed_count": parse_failures,
                        "raw_count": len(klines),
                        "parsed_count": len(results),
                    },
                )
            )

        if autype == "qfq" and results:
            price_values = [
                value
                for row in results
                for value in (row["open"], row["close"], row["high"], row["low"])
            ]
            if price_values and max(price_values) < 0:
                for row in results:
                    row["open"] = abs(float(row["open"]))
                    row["close"] = abs(float(row["close"]))
                    row["high"] = abs(float(row["high"]))
                    row["low"] = abs(float(row["low"]))
                issues.append(
                    cls._make_issue(
                        level="warning",
                        reason_code="qfq_negative_prices",
                        message="tencent qfq kline returned negative prices; normalized to absolute values",
                        context={
                            "operation": "get_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                            "security_type": security_type,
                        },
                    )
                )

        success = not any(issue.level == "error" for issue in issues)
        if not success:
            cls._log_errors(issues)
        return MarketDataResult(success=success, data=results, issues=issues)

    @classmethod
    def get_kline(cls, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None, security_type: str | None = None) -> list:
        result = cls.get_kline_result(
            code=code,
            start_date=start_date,
            end_date=end_date,
            ktype=ktype,
            autype=autype,
            market=market,
            security_type=security_type,
        )
        return result.data

    @classmethod
    def get_minute_kline_result(
        cls,
        code: str,
        start_date: str,
        end_date: str,
        ktype: str = "1m",
        market: str = None,
    ) -> MarketDataResult[list]:
        tx_ktype = cls.MINUTE_KTYPES.get(ktype)
        if not tx_ktype:
            issues = [
                cls._make_issue(
                    level="warning",
                    reason_code="no_data",
                    message="tencent minute kline unsupported ktype",
                    context={"operation": "get_minute_kline", "ktype": ktype},
                )
            ]
            return MarketDataResult(success=True, data=[], issues=issues)

        norm = cls._normalize_code(code, market)
        prefix = cls._get_prefix(norm, market)
        start_day = cls._parse_date(start_date)
        end_day = cls._parse_date(end_date)

        merged = {}
        ref = ""
        issues: list[ProviderIssue] = []
        parse_failures = 0
        first_page = True
        for _ in range(cls.MINUTE_KLINE_MAX_PAGES):
            url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{norm},{tx_ktype},{ref},{cls.MINUTE_KLINE_PAGE_SIZE}"
            try:
                data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
            except Exception as exc:
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="request_failed",
                        message="tencent minute kline request failed",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                            "ref": ref,
                        },
                        exc=exc,
                    )
                )
                break

            if not isinstance(data, dict):
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="unexpected_response_shape",
                        message="tencent minute kline response is not a dict",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                            "ref": ref,
                            "response_type": type(data).__name__,
                        },
                    )
                )
                break

            provider_code = data.get("code")
            if provider_code != 0:
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="provider_nonzero_code",
                        message="tencent minute kline provider returned nonzero code",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                            "provider_code": provider_code,
                        },
                    )
                )
                break

            payload = data.get("data", {})
            if not isinstance(payload, dict):
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="unexpected_response_shape",
                        message="tencent minute kline payload is not a dict",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                        },
                    )
                )
                break
            symbol_payload = payload.get(f"{prefix}{norm}", {})
            if not isinstance(symbol_payload, dict):
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="unexpected_response_shape",
                        message="tencent minute kline symbol payload is not a dict",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                        },
                    )
                )
                break
            raw_items = symbol_payload.get(tx_ktype, [])
            if not isinstance(raw_items, list):
                issues.append(
                    cls._make_issue(
                        level="error",
                        reason_code="unexpected_response_shape",
                        message="tencent minute kline series is not a list",
                        context={
                            "operation": "get_minute_kline",
                            "code": code,
                            "market": market,
                            "ktype": ktype,
                        },
                    )
                )
                break
            if not raw_items:
                if first_page and not merged:
                    issues.append(
                        cls._make_issue(
                            level="warning",
                            reason_code="no_data",
                            message="tencent minute kline returned no data",
                            context={
                                "operation": "get_minute_kline",
                                "code": code,
                                "market": market,
                                "ktype": ktype,
                            },
                        )
                    )
                break

            page_oldest_raw = None
            added_in_page = 0
            for item in raw_items:
                if len(item) < 6:
                    parse_failures += 1
                    continue
                raw_ts = str(item[0])
                timestamp = cls._format_minute_timestamp(raw_ts)
                if not timestamp or timestamp in merged:
                    if not timestamp:
                        parse_failures += 1
                    continue
                try:
                    merged[timestamp] = {
                        "date": timestamp,
                        "open": round(float(item[1]), 2),
                        "close": round(float(item[2]), 2),
                        "high": round(float(item[3]), 2),
                        "low": round(float(item[4]), 2),
                        "volume": int(float(item[5])),
                    }
                except Exception:
                    parse_failures += 1
                    continue
                added_in_page += 1
                if page_oldest_raw is None or raw_ts < page_oldest_raw:
                    page_oldest_raw = raw_ts

            if added_in_page == 0 or page_oldest_raw is None:
                break

            oldest_day = datetime.strptime(page_oldest_raw[:8], "%Y%m%d").date()
            if oldest_day <= start_day:
                break

            ref = page_oldest_raw
            first_page = False

        results = []
        for timestamp in sorted(merged.keys()):
            row_day = datetime.strptime(timestamp[:10], "%Y-%m-%d").date()
            if row_day < start_day or row_day > end_day:
                continue
            results.append(merged[timestamp])

        if parse_failures:
            issues.append(
                cls._make_issue(
                    level="warning" if results else "error",
                    reason_code="parse_failed",
                    message="tencent minute kline parse failed",
                    context={
                        "operation": "get_minute_kline",
                        "code": code,
                        "market": market,
                        "ktype": ktype,
                        "failed_count": parse_failures,
                        "parsed_count": len(results),
                    },
                )
            )

        success = not any(issue.level == "error" for issue in issues)
        if not success:
            cls._log_errors(issues)
        return MarketDataResult(success=success, data=results, issues=issues)

    @classmethod
    def get_minute_kline(cls, code: str, start_date: str, end_date: str, ktype: str = "1m", market: str = None) -> list:
        result = cls.get_minute_kline_result(
            code=code,
            start_date=start_date,
            end_date=end_date,
            ktype=ktype,
            market=market,
        )
        return result.data

    @staticmethod
    def _parse_date(value: str):
        return datetime.strptime(value, "%Y-%m-%d").date()

    @staticmethod
    def _format_minute_timestamp(value: str) -> str:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M:00")
        except ValueError:
            return ""

    @classmethod
    def get_daily_quote_result(
        cls,
        code: str,
        trade_date: str,
        autype: str = "qfq",
        market: str = None,
        security_type: str | None = None,
    ) -> MarketDataResult[dict | None]:
        target = cls.get_kline_result(
            code=code,
            start_date=trade_date,
            end_date=trade_date,
            ktype="day",
            autype=autype,
            market=market,
            security_type=security_type,
        )
        issues: list[ProviderIssue] = [
            cls._with_context(issue, {"stage": "target"}) for issue in target.issues
        ]
        if not target.data:
            if not issues:
                issues.append(
                    cls._make_issue(
                        level="warning",
                        reason_code="no_data",
                        message="tencent daily quote returned no data",
                        context={
                            "operation": "get_daily_quote",
                            "code": code,
                            "market": market,
                        },
                    )
                )
            success = not any(issue.level == "error" for issue in issues)
            if not success:
                cls._log_errors(issues)
            return MarketDataResult(success=success, data=None, issues=issues)

        data = target.data[0]
        prev_date = (
            datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        prev = cls.get_kline_result(
            code=code,
            start_date=prev_date,
            end_date=prev_date,
            ktype="day",
            autype=autype,
            market=market,
            security_type=security_type,
        )
        issues.extend(cls._with_context(issue, {"stage": "prev"}) for issue in prev.issues)

        if prev.data:
            pre_close = prev.data[0]["close"]
        else:
            pre_close = data["open"]

        change = data["close"] - pre_close
        change_pct = (change / pre_close * 100) if pre_close > 0 else 0
        payload = {
            "date": data["date"],
            "open": data["open"],
            "close": data["close"],
            "high": data["high"],
            "low": data["low"],
            "volume": data["volume"],
            "pre_close": pre_close,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
        }

        success = target.success and prev.success and not any(
            issue.level == "error" for issue in issues
        )
        if not success:
            cls._log_errors(issues)
        return MarketDataResult(success=success, data=payload, issues=issues)

    @classmethod
    def get_daily_quote(cls, code: str, trade_date: str, autype: str = "qfq", market: str = None, security_type: str | None = None) -> dict:
        result = cls.get_daily_quote_result(
            code=code,
            trade_date=trade_date,
            autype=autype,
            market=market,
            security_type=security_type,
        )
        return result.data


def create_market_data_provider(name: str = None) -> MarketDataProvider:
    provider_name = (name or "tencent").lower()
    if provider_name in ("tencent", "tx", "qq"):
        return TencentStockDataProvider()
    raise ValueError(f"Unsupported market data provider: {name}")
