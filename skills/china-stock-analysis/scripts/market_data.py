"""
Market data providers for china-stock-analysis.

The report generator depends on this module's provider interface instead of
Tencent-specific HTTP details, so future data sources can be added without
rewriting report rendering and indicator logic.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta


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
    def _get_prefix(code: str, market: str = None) -> str:
        return get_market_prefix(code, market)

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
    def realtime(cls, codes, markets=None):
        if isinstance(codes, str):
            codes = [codes]

        code_str_parts = []
        for i, code_item in enumerate(codes):
            if isinstance(code_item, tuple):
                code, market = code_item
                code_str_parts.append(f"{market}{code}")
            elif code_item.startswith(("sh", "sz", "bj")):
                code_str_parts.append(code_item)
            else:
                market = markets[i] if markets else None
                code_str_parts.append(f"{cls._get_prefix(code_item, market)}{code_item}")

        url = f"https://qt.gtimg.cn/q={','.join(code_str_parts)}"

        try:
            data = cls._fetch_with_retry(url, decode="gbk")
        except Exception as e:
            print(f"[ERROR] 获取行情失败: {e}")
            return []

        results = []
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

                results.append({
                    "name": parts[1],
                    "code": parts[2],
                    "price": price,
                    "pre_close": pre_close,
                    "open": float(parts[5]),
                    "high": float(parts[33]),
                    "low": float(parts[34]),
                    "volume": int(parts[6]),
                    "amount": float(parts[37]) if len(parts) > 37 else 0,
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                })
            except Exception:
                continue

        return results[0] if len(results) == 1 and len(codes) == 1 else results

    @classmethod
    def get_kline(cls, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None, security_type: str | None = None) -> list:
        if ktype in cls.MINUTE_KTYPES:
            return cls.get_minute_kline(
                code=code,
                start_date=start_date,
                end_date=end_date,
                ktype=ktype,
                market=market,
            )

        # 指数没有复权概念：腾讯 fqkline 接口在 qfq/hfq 下对指数直接返回空，因此
        # 指数强制使用不复权（autype=""）。security_type 由调用方（证券主数据选中
        # 的标的类型）传入；未传时保持 qfq，向后兼容现有调用方。
        if security_type == "index":
            autype = ""

        prefix = cls._get_prefix(code, market)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},{ktype},{start_date},{end_date},500,{autype}"

        try:
            data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
        except Exception as e:
            print(f"[ERROR] 获取K线数据失败 {code}: {e}")
            return []

        if data.get("code") != 0:
            return []

        key = f"{autype}day" if ktype == "day" else f"{autype}{ktype}"
        # 腾讯财经 fqkline 接口返回结构在正常情况下是嵌套 dict：
        #   {"code": 0, "data": {"sh512480": {"qfqday": [[...], ...], "qt": {...}, ...}}}
        # 但在以下场景下，data["data"][symbol] 可能不是 dict：
        #   1. 限流 / 维护 / 路由异常时，腾讯偶尔会把内层节点替换为空 list（[]）或字符串；
        #   2. 个别 ETF / 指数 / 新上市标的，特定参数组合（如 autype 不匹配、日期范围越界）
        #      会让该字段直接降级成 list，没有预期的 "qfqday" 等子键；
        #   3. 接口偶发返回 code==0 但 data 字段缺失或为 null。
        # 若直接链式调用 data["data"].get(...).get(...)，遇到非 dict 节点会抛
        # AttributeError: 'list' object has no attribute 'get'。
        # 因此这里改成「先 isinstance 校验类型，再 .get 解构」，逐层兜底返回 []，
        # 让上层走「无数据」路径而不是崩溃。
        payload = data.get("data", {})
        if not isinstance(payload, dict):
            return []
        symbol_payload = payload.get(f"{prefix}{code}", {})
        if not isinstance(symbol_payload, dict):
            return []
        klines = symbol_payload.get(key, [])
        if not isinstance(klines, list):
            return []

        results = []
        for item in klines:
            results.append({
                "date": item[0],
                "open": round(float(item[1]), 2),
                "close": round(float(item[2]), 2),
                "high": round(float(item[3]), 2),
                "low": round(float(item[4]), 2),
                "volume": int(float(item[5])),
            })

        return results

    @classmethod
    def get_minute_kline(cls, code: str, start_date: str, end_date: str, ktype: str = "1m", market: str = None) -> list:
        tx_ktype = cls.MINUTE_KTYPES.get(ktype)
        if not tx_ktype:
            return []

        prefix = cls._get_prefix(code, market)
        start_day = cls._parse_date(start_date)
        end_day = cls._parse_date(end_date)

        # The mkline API caps each response at ~800 bars and silently falls back
        # to ~320 when a larger count is requested. Paginate backwards using the
        # oldest bar of each page as the ref (start_time) for the next request
        # until the requested start_date is covered or history is exhausted.
        merged = {}
        ref = ""
        for _ in range(cls.MINUTE_KLINE_MAX_PAGES):
            url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},{tx_ktype},{ref},{cls.MINUTE_KLINE_PAGE_SIZE}"
            try:
                data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
            except Exception as e:
                print(f"[ERROR] 获取分钟K线数据失败 {code}: {e}")
                break

            if data.get("code") != 0:
                break

            # 同 get_kline 的 fqkline 接口，mkline 分钟线接口也存在内层节点可能不是 dict
            # 的情况（限流/维护、个别 ETF/指数特殊响应、code==0 但 data 缺失等）。
            # 这里同样改为「先类型校验、再 .get 解构」，遇到异常结构直接 break 跳出分页循环，
            # 避免在 dict.get 链式调用上抛 AttributeError。
            payload = data.get("data", {})
            if not isinstance(payload, dict):
                break
            symbol_payload = payload.get(f"{prefix}{code}", {})
            if not isinstance(symbol_payload, dict):
                break
            raw_items = symbol_payload.get(tx_ktype, [])
            if not isinstance(raw_items, list) or not raw_items:
                break

            page_oldest_raw = None
            added_in_page = 0
            for item in raw_items:
                if len(item) < 6:
                    continue
                raw_ts = str(item[0])
                timestamp = cls._format_minute_timestamp(raw_ts)
                if not timestamp or timestamp in merged:
                    continue
                merged[timestamp] = {
                    "date": timestamp,
                    "open": round(float(item[1]), 2),
                    "close": round(float(item[2]), 2),
                    "high": round(float(item[3]), 2),
                    "low": round(float(item[4]), 2),
                    "volume": int(float(item[5])),
                }
                added_in_page += 1
                if page_oldest_raw is None or raw_ts < page_oldest_raw:
                    page_oldest_raw = raw_ts

            # Stop when no new bars are returned (avoids infinite loops).
            if added_in_page == 0 or page_oldest_raw is None:
                break

            # Stop once the oldest bar in this page reaches the requested start_date.
            oldest_day = datetime.strptime(page_oldest_raw[:8], "%Y%m%d").date()
            if oldest_day <= start_day:
                break

            ref = page_oldest_raw

        results = []
        for timestamp in sorted(merged.keys()):
            row_day = datetime.strptime(timestamp[:10], "%Y-%m-%d").date()
            if row_day < start_day or row_day > end_day:
                continue
            results.append(merged[timestamp])

        return results

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
    def get_daily_quote(cls, code: str, trade_date: str, autype: str = "qfq", market: str = None, security_type: str | None = None) -> dict:
        data_for_date = cls.get_kline(code, trade_date, trade_date, ktype="day", autype=autype, market=market, security_type=security_type)
        if not data_for_date:
            return None

        data = data_for_date[0]
        prev_date = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        prev_data = cls.get_kline(code, prev_date, prev_date, ktype="day", autype=autype, market=market, security_type=security_type)

        if prev_data:
            pre_close = prev_data[0]["close"]
            change = data["close"] - pre_close
            change_pct = (change / pre_close * 100) if pre_close > 0 else 0
        else:
            pre_close = data["open"]
            change = data["close"] - pre_close
            change_pct = (change / pre_close * 100) if pre_close > 0 else 0

        return {
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


def create_market_data_provider(name: str = None) -> MarketDataProvider:
    provider_name = (name or "tencent").lower()
    if provider_name in ("tencent", "tx", "qq"):
        return TencentStockDataProvider()
    raise ValueError(f"Unsupported market data provider: {name}")
