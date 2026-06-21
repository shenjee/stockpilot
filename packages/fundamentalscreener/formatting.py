"""输出格式化层。

Phase 0 提供 JSON。Phase 1 起补充 ``sectors`` / ``sector-detail`` 的 Markdown 输出。
Phase 2 起补充 ``companies`` 的 Markdown 与 CSV 输出。其余命令的 Markdown / CSV
仍为占位，后续 Phase 按需实现。

约定：
- formatting 不做任何业务计算，只接收已构造好的 payload 字典或 dataclass。
- ``format_output`` 是 CLI 唯一的格式化入口，根据 ``fmt`` 选择具体实现。
"""

from __future__ import annotations

import csv
import io
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


def _format_companies_markdown(payload: Dict[str, Any]) -> str:
    date = payload.get("date", "")
    classification = payload.get("classification_system", "")
    sector_id = payload.get("sector_id")
    sector_name = payload.get("sector_name")
    sort_field = payload.get("sort", "")
    companies = payload.get("companies") or []
    warnings = payload.get("warnings") or []

    lines: List[str] = []
    lines.append("# fundamental-screener: companies")
    lines.append("")
    lines.append(f"- date: `{date}`")
    lines.append(f"- classification_system: `{classification}`")
    lines.append(f"- sector_id: `{_fmt_str(sector_id)}`")
    lines.append(f"- sector_name: `{_fmt_str(sector_name)}`")
    lines.append(f"- sort: `{sort_field}`")
    lines.append("")

    if companies:
        headers = [
            "code",
            "name",
            "market_cap",
            "turnover_amount",
            "turnover_rate",
            "sector_return_rank",
            "leader_score",
            "attention_score",
            "financial_quality_score",
            "valuation_score",
            "combined_score",
            "group",
        ]
        rows: List[List[str]] = []
        for c in companies:
            rows.append(
                [
                    _fmt_str(c.get("code")),
                    _fmt_str(c.get("name")),
                    _fmt_float(c.get("market_cap"), 0),
                    _fmt_float(c.get("turnover_amount"), 0),
                    _fmt_ratio(c.get("turnover_rate")),
                    _fmt_int(c.get("sector_return_rank")),
                    _fmt_float(c.get("leader_score"), 2),
                    _fmt_float(c.get("attention_score"), 2),
                    _fmt_float(c.get("financial_quality_score"), 2),
                    _fmt_float(c.get("valuation_score"), 2),
                    _fmt_float(c.get("combined_score"), 2),
                    _fmt_str(c.get("group")),
                ]
            )
        lines.append(_md_table(headers, rows))
        lines.append("")
    else:
        lines.append("_no companies_")
        lines.append("")

    if warnings:
        lines.append("## warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_companies_csv(payload: Dict[str, Any]) -> str:
    """companies CSV：每行一家公司，字段顺序与 schema 一致。

    flags / warnings 用 ``;`` 拼接为字符串列，缺失值留空，便于 Excel/Numbers
    直接打开。CSV 不输出顶层元信息，元信息从 JSON 输出取。
    """

    companies = payload.get("companies") or []
    headers = [
        "code",
        "name",
        "market_cap",
        "turnover_amount",
        "turnover_rate",
        "sector_return_rank",
        "leader_score",
        "attention_score",
        "financial_quality_score",
        "valuation_score",
        "combined_score",
        "group",
        "flags",
        "warnings",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for c in companies:
        writer.writerow(
            [
                _csv_value(c.get("code")),
                _csv_value(c.get("name")),
                _csv_value(c.get("market_cap")),
                _csv_value(c.get("turnover_amount")),
                _csv_value(c.get("turnover_rate")),
                _csv_value(c.get("sector_return_rank")),
                _csv_value(c.get("leader_score")),
                _csv_value(c.get("attention_score")),
                _csv_value(c.get("financial_quality_score")),
                _csv_value(c.get("valuation_score")),
                _csv_value(c.get("combined_score")),
                _csv_value(c.get("group")),
                ";".join(c.get("flags") or []),
                ";".join(c.get("warnings") or []),
            ]
        )
    return buf.getvalue()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _format_financials_markdown(payload: Dict[str, Any]) -> str:
    date = payload.get("date", "")
    companies = payload.get("companies") or []
    warnings = payload.get("warnings") or []

    lines: List[str] = []
    lines.append("# fundamental-screener: financials")
    lines.append("")
    lines.append(f"- date: `{date}`")
    lines.append("")

    if companies:
        headers = [
            "code",
            "name",
            "rev_yoy",
            "np_yoy",
            "deducted_np_yoy",
            "gross_margin",
            "net_margin",
            "roe",
            "ocf/profit",
            "fcf",
            "debt/asset",
            "ib_debt_ratio",
            "ar_yoy",
            "inv_yoy",
            "score",
            "abnormal_flags",
        ]
        rows: List[List[str]] = []
        for c in companies:
            rows.append(
                [
                    _fmt_str(c.get("code")),
                    _fmt_str(c.get("name")),
                    _fmt_pct(c.get("revenue_yoy")),
                    _fmt_pct(c.get("net_profit_yoy")),
                    _fmt_pct(c.get("deducted_net_profit_yoy")),
                    _fmt_pct(c.get("gross_margin")),
                    _fmt_pct(c.get("net_margin")),
                    _fmt_pct(c.get("roe")),
                    _fmt_float(c.get("operating_cashflow_to_profit"), 2),
                    _fmt_float(c.get("free_cashflow"), 0),
                    _fmt_pct(c.get("debt_to_asset")),
                    _fmt_pct(c.get("interest_bearing_debt_ratio")),
                    _fmt_pct(c.get("accounts_receivable_yoy")),
                    _fmt_pct(c.get("inventory_yoy")),
                    _fmt_float(c.get("score"), 2),
                    ", ".join(c.get("abnormal_flags") or []) or "-",
                ]
            )
        lines.append(_md_table(headers, rows))
        lines.append("")
    else:
        lines.append("_no companies_")
        lines.append("")

    if warnings:
        lines.append("## warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_financials_csv(payload: Dict[str, Any]) -> str:
    """financials CSV：每行一家公司。

    列顺序与 ``schema.FinancialEntry`` 一致；``abnormal_flags`` / ``warnings``
    用 ``;`` 拼接为字符串列，缺失值留空，便于 Excel/Numbers 直接打开。CSV 不
    输出顶层元信息，元信息从 JSON 输出取。
    """

    companies = payload.get("companies") or []
    headers = [
        "code",
        "name",
        "revenue_yoy",
        "net_profit_yoy",
        "deducted_net_profit_yoy",
        "gross_margin",
        "net_margin",
        "roe",
        "operating_cashflow_to_profit",
        "free_cashflow",
        "debt_to_asset",
        "interest_bearing_debt_ratio",
        "accounts_receivable_yoy",
        "inventory_yoy",
        "score",
        "abnormal_flags",
        "warnings",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for c in companies:
        writer.writerow(
            [
                _csv_value(c.get("code")),
                _csv_value(c.get("name")),
                _csv_value(c.get("revenue_yoy")),
                _csv_value(c.get("net_profit_yoy")),
                _csv_value(c.get("deducted_net_profit_yoy")),
                _csv_value(c.get("gross_margin")),
                _csv_value(c.get("net_margin")),
                _csv_value(c.get("roe")),
                _csv_value(c.get("operating_cashflow_to_profit")),
                _csv_value(c.get("free_cashflow")),
                _csv_value(c.get("debt_to_asset")),
                _csv_value(c.get("interest_bearing_debt_ratio")),
                _csv_value(c.get("accounts_receivable_yoy")),
                _csv_value(c.get("inventory_yoy")),
                _csv_value(c.get("score")),
                ";".join(c.get("abnormal_flags") or []),
                ";".join(c.get("warnings") or []),
            ]
        )
    return buf.getvalue()


def _format_valuations_markdown(payload: Dict[str, Any]) -> str:
    date = payload.get("date", "")
    companies = payload.get("companies") or []
    warnings = payload.get("warnings") or []

    lines: List[str] = []
    lines.append("# fundamental-screener: valuations")
    lines.append("")
    lines.append(f"- date: `{date}`")
    lines.append("")

    if companies:
        headers = [
            "code",
            "name",
            "pe",
            "pb",
            "ps",
            "peg",
            "div_yield",
            "pe_pct",
            "pb_pct",
            "industry_pos",
            "score",
            "label",
        ]
        rows: List[List[str]] = []
        for c in companies:
            rows.append(
                [
                    _fmt_str(c.get("code")),
                    _fmt_str(c.get("name")),
                    _fmt_float(c.get("pe"), 2),
                    _fmt_float(c.get("pb"), 2),
                    _fmt_float(c.get("ps"), 2),
                    _fmt_float(c.get("peg"), 2),
                    _fmt_pct(c.get("dividend_yield")),
                    _fmt_pct(c.get("pe_percentile")),
                    _fmt_pct(c.get("pb_percentile")),
                    _fmt_str(c.get("industry_valuation_position")),
                    _fmt_float(c.get("score"), 2),
                    _fmt_str(c.get("label")),
                ]
            )
        lines.append(_md_table(headers, rows))
        lines.append("")
    else:
        lines.append("_no companies_")
        lines.append("")

    if warnings:
        lines.append("## warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_valuations_csv(payload: Dict[str, Any]) -> str:
    """valuations CSV：每行一家公司。列顺序与 ``schema.ValuationEntry`` 一致。"""

    companies = payload.get("companies") or []
    headers = [
        "code",
        "name",
        "pe",
        "pb",
        "ps",
        "peg",
        "dividend_yield",
        "pe_percentile",
        "pb_percentile",
        "industry_valuation_position",
        "score",
        "label",
        "warnings",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for c in companies:
        writer.writerow(
            [
                _csv_value(c.get("code")),
                _csv_value(c.get("name")),
                _csv_value(c.get("pe")),
                _csv_value(c.get("pb")),
                _csv_value(c.get("ps")),
                _csv_value(c.get("peg")),
                _csv_value(c.get("dividend_yield")),
                _csv_value(c.get("pe_percentile")),
                _csv_value(c.get("pb_percentile")),
                _csv_value(c.get("industry_valuation_position")),
                _csv_value(c.get("score")),
                _csv_value(c.get("label")),
                ";".join(c.get("warnings") or []),
            ]
        )
    return buf.getvalue()


def format_markdown(payload: Dict[str, Any]) -> str:
    """根据 payload 的 command 选择合适的 Markdown 渲染。"""

    command = payload.get("command", "")
    if command in ("sectors", "sector-detail"):
        return _format_sectors_markdown(payload)
    if command == "companies":
        return _format_companies_markdown(payload)
    if command == "financials":
        return _format_financials_markdown(payload)
    if command == "valuations":
        return _format_valuations_markdown(payload)
    date = payload.get("date", "")
    return (
        f"# fundamental-screener: {command}\n\n"
        f"date: {date}\n\n"
        "Markdown output is not implemented for this command yet.\n"
    )


def format_csv(payload: Dict[str, Any]) -> str:
    """CSV 输出。Phase 2 起为 ``companies``、Phase 3 起为 ``financials``、
    Phase 4 起为 ``valuations`` 命令提供完整列表，其余命令仍占位。"""

    command = payload.get("command", "")
    if command == "companies":
        return _format_companies_csv(payload)
    if command == "financials":
        return _format_financials_csv(payload)
    if command == "valuations":
        return _format_valuations_csv(payload)
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
