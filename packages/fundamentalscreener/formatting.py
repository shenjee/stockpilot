"""输出格式化层。

Phase 0 提供 JSON。Phase 1 起补充 ``sectors`` / ``sector-detail`` 的 Markdown 输出；
其余命令的 Markdown / CSV 仍为占位，后续 Phase 按需实现。

约定：
- formatting 不做任何业务计算，只接收已构造好的 payload 字典或 dataclass。
- ``format_output`` 是 CLI 唯一的格式化入口，根据 ``fmt`` 选择具体实现。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def format_json(payload: Dict[str, Any]) -> str:
    """将 payload 字典序列化为稳定的 JSON 字符串。"""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def _fmt_ratio(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def _fmt_int(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return str(value)


def _fmt_float(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_str(value: Optional[str]) -> str:
    if not value:
        return "-"
    return str(value)


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    if not headers:
        return ""
    sep = "| " + " | ".join(headers) + " |"
    line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join([sep, line, *body])


def _format_sectors_markdown(payload: Dict[str, Any]) -> str:
    command = payload.get("command", "sectors")
    date = payload.get("date", "")
    classification = payload.get("classification_system", "")
    benchmark = payload.get("benchmark", "")
    sort_field = payload.get("sort", "")
    periods = payload.get("periods", [])
    sectors = payload.get("sectors") or []
    warnings = payload.get("warnings") or []

    lines: List[str] = []
    title = "fundamental-screener: sectors"
    if command == "sector-detail":
        title = "fundamental-screener: sector-detail"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- date: `{date}`")
    lines.append(f"- classification_system: `{classification}`")
    lines.append(f"- benchmark: `{benchmark}`")
    lines.append(f"- sort: `{sort_field}`")
    lines.append(f"- periods: `{periods}`")
    lines.append("")

    if sectors:
        headers = [
            "sector_id",
            "sector_name",
            "1d",
            "5d",
            "20d",
            "60d",
            "rel",
            "turn_chg",
            "mkt_share",
            "rise_ratio",
            "rank_chg_5d",
            "state",
            "score",
        ]
        rows: List[List[str]] = []
        for s in sectors:
            rows.append(
                [
                    _fmt_str(s.get("sector_id")),
                    _fmt_str(s.get("sector_name")),
                    _fmt_pct(s.get("return_1d")),
                    _fmt_pct(s.get("return_5d")),
                    _fmt_pct(s.get("return_20d")),
                    _fmt_pct(s.get("return_60d")),
                    _fmt_pct(s.get("relative_return")),
                    _fmt_pct(s.get("turnover_amount_change")),
                    _fmt_ratio(s.get("market_turnover_share")),
                    _fmt_ratio(s.get("rising_stock_ratio")),
                    _fmt_int(s.get("rank_change_5d")),
                    _fmt_str(s.get("state")),
                    _fmt_float(s.get("score"), 2),
                ]
            )
        lines.append(_md_table(headers, rows))
        lines.append("")
    else:
        lines.append("_no sectors_")
        lines.append("")

    if warnings:
        lines.append("## warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_markdown(payload: Dict[str, Any]) -> str:
    """根据 payload 的 command 选择合适的 Markdown 渲染。"""

    command = payload.get("command", "")
    if command in ("sectors", "sector-detail"):
        return _format_sectors_markdown(payload)
    date = payload.get("date", "")
    return (
        f"# fundamental-screener: {command}\n\n"
        f"date: {date}\n\n"
        "Markdown output is not implemented for this command yet.\n"
    )


def format_csv(payload: Dict[str, Any]) -> str:
    """CSV 输出占位。Phase 2 起按命令逐个实现。"""

    command = payload.get("command", "")
    return f"# csv output not implemented for command={command} yet\n"


def format_output(payload: Dict[str, Any], fmt: str) -> str:
    """按 ``fmt`` 选择格式化实现。"""

    if fmt == "json":
        return format_json(payload)
    if fmt == "markdown":
        return format_markdown(payload)
    if fmt == "csv":
        return format_csv(payload)
    raise ValueError(f"unsupported format: {fmt}")


__all__ = [
    "format_csv",
    "format_json",
    "format_markdown",
    "format_output",
]
