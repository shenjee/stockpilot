"""AkShare 数据源标准化辅助函数。

这里放置纯函数（string/float/date 规范化、市场前缀推导、财报披露日估算等），用于：
- 复用：sector / benchmark / company 层共享同一套转换逻辑
- 减重：保持 akshare_source.py 作为 facade，而非堆叠大量 util
"""

from __future__ import annotations

from typing import Any, Optional


def to_float(value: Any) -> Optional[float]:
    """把 akshare 返回值转成 float，``None`` / NaN / 非数都返回 ``None``。"""

    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def pct_to_ratio(value: Any) -> Optional[float]:
    """把百分比数值转成小数比率（docs §20: ``0.18`` 表示 18%）。"""

    f = to_float(value)
    if f is None:
        return None
    return f / 100.0


def to_str(value: Any) -> Optional[str]:
    """去除空白后的字符串；空串/None/NaN 都返回 ``None``。"""

    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lower() in ("nan", "<na>", "none"):
        return None
    return s


def compact_date(value: str) -> str:
    """``YYYY-MM-DD`` -> ``YYYYMMDD``（akshare 历史接口要求无分隔符）。"""

    return str(value).replace("-", "")


def normalize_date(value: Any) -> Optional[str]:
    """把 akshare 日期统一成 ``YYYY-MM-DD``。容忍 ``YYYY-MM-DD`` / ``YYYYMMDD`` / datetime。"""

    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "-" in s:
        return s[:10]
    if "/" in s:
        return s.replace("/", "-")[:10]
    if len(s) >= 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10] if len(s) >= 10 else None


def derive_market(code: str) -> Optional[str]:
    """从 6 位代码推导交易所：``6`` → SH，``0/3`` → SZ，``4/8/920`` → BJ。"""

    if not code or len(code) < 1:
        return None
    if code.startswith("920"):
        return "BJ"
    first = code[0]
    if first == "6":
        return "SH"
    if first in ("0", "3"):
        return "SZ"
    if first in ("4", "8"):
        return "BJ"
    return None


def to_sina_symbol(code: str) -> str:
    """6 位股票代码 → 新浪行情 symbol（``sh600001`` / ``sz002371`` / ``bj830799``）。"""

    market = derive_market(code)
    prefix = (market or "sz").lower()
    return f"{prefix}{code}"


def to_sina_index_symbol(code: str) -> str:
    """指数代码 → 新浪指数 symbol：``000xxx`` → ``sh000xxx``，``399xxx`` → ``sz399xxx``。"""

    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


def derive_period_type(period_end_date: str) -> str:
    """报告期末 → period_type：12 月 → annual，6 月 → semiannual，其余 → quarterly。"""

    month = period_end_date[5:7]
    if month == "12":
        return "annual"
    if month == "06":
        return "semiannual"
    return "quarterly"


def derive_report_period(period_end_date: str) -> str:
    """报告期末 → report_period：``2026-03-31`` → ``2026Q1``。"""

    year = period_end_date[:4]
    month = period_end_date[5:7]
    if month == "03":
        return f"{year}Q1"
    if month == "06":
        return f"{year}H1"
    if month == "09":
        return f"{year}Q3"
    if month == "12":
        return f"{year}A"
    return f"{year}-{month}"


def estimate_disclosure_date(period_end_date: str) -> str:
    """估算财报最晚披露日（监管截止日），用于 point-in-time 过滤。"""

    year = int(period_end_date[:4])
    month = period_end_date[5:7]
    if month == "03":
        return f"{year}-04-30"
    if month == "06":
        return f"{year}-08-31"
    if month == "09":
        return f"{year}-10-31"
    if month == "12":
        return f"{year + 1}-04-30"
    return period_end_date


__all__ = [
    "compact_date",
    "derive_market",
    "derive_period_type",
    "derive_report_period",
    "estimate_disclosure_date",
    "normalize_date",
    "pct_to_ratio",
    "to_float",
    "to_sina_index_symbol",
    "to_sina_symbol",
    "to_str",
]
