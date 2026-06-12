"""
Market data providers for china-stock-daily-tracker.

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
    """Return exchange prefix, preferring explicit config over code inference."""
    if market:
        return market
    if code.startswith("6"):
        return "sh"
    if code.startswith("0") or code.startswith("3"):
        return "sz"
    if code.startswith("8") or code.startswith("4"):
        return "bj"
    return "sh"


class MarketDataProvider:
    """Provider contract used by the report generator."""

    provider_id = "market-data"
    name = "market-data"

    def realtime(self, codes, markets=None):
        raise NotImplementedError

    def get_kline(self, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None) -> list:
        raise NotImplementedError

    def get_daily_quote(self, code: str, trade_date: str, autype: str = "qfq", market: str = None) -> dict:
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
    def get_kline(cls, code: str, start_date: str, end_date: str, ktype: str = "day", autype: str = "qfq", market: str = None) -> list:
        if ktype in cls.MINUTE_KTYPES:
            return cls.get_minute_kline(
                code=code,
                start_date=start_date,
                end_date=end_date,
                ktype=ktype,
                market=market,
            )

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
        klines = data["data"].get(f"{prefix}{code}", {}).get(key, [])

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
        count = cls._estimate_minute_bar_count(start_date=start_date, end_date=end_date, ktype=ktype)
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},{tx_ktype},,{count}"

        try:
            data = json.loads(cls._fetch_with_retry(url, decode="utf-8"))
        except Exception as e:
            print(f"[ERROR] 获取分钟K线数据失败 {code}: {e}")
            return []

        if data.get("code") != 0:
            return []

        raw_items = data.get("data", {}).get(f"{prefix}{code}", {}).get(tx_ktype, [])
        start_day = cls._parse_date(start_date)
        end_day = cls._parse_date(end_date)
        results = []
        for item in raw_items:
            if len(item) < 6:
                continue
            timestamp = cls._format_minute_timestamp(str(item[0]))
            if not timestamp:
                continue
            row_day = datetime.strptime(timestamp[:10], "%Y-%m-%d").date()
            if row_day < start_day or row_day > end_day:
                continue
            results.append({
                "date": timestamp,
                "open": round(float(item[1]), 2),
                "close": round(float(item[2]), 2),
                "high": round(float(item[3]), 2),
                "low": round(float(item[4]), 2),
                "volume": int(float(item[5])),
            })

        return results

    @classmethod
    def _estimate_minute_bar_count(cls, start_date: str, end_date: str, ktype: str) -> int:
        start_day = cls._parse_date(start_date)
        end_day = cls._parse_date(end_date)
        day_count = max((end_day - start_day).days + 1, 1)
        bars_per_day = {
            "1m": 240,
            "5m": 48,
            "30m": 8,
            "60m": 4,
        }.get(ktype, 240)
        return min(max(day_count * bars_per_day, 10), 5000)

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
    def get_daily_quote(cls, code: str, trade_date: str, autype: str = "qfq", market: str = None) -> dict:
        data_for_date = cls.get_kline(code, trade_date, trade_date, ktype="day", autype=autype, market=market)
        if not data_for_date:
            return None

        data = data_for_date[0]
        prev_date = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        prev_data = cls.get_kline(code, prev_date, prev_date, ktype="day", autype=autype, market=market)

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
