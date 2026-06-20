"""输出格式化层。

Phase 0 只实现 JSON 输出（``format_json``）。Markdown 和 CSV 接口先以占位形式
存在，由 Phase 1+ 真正实现，避免提前承诺业务列。

约定：
- formatting 不做任何业务计算，只接收已构造好的 payload 字典或 dataclass。
- ``format_output`` 是 CLI 唯一的格式化入口，根据 ``fmt`` 选择具体实现。
"""

from __future__ import annotations

import json
from typing import Any, Dict


def format_json(payload: Dict[str, Any]) -> str:
    """将 payload 字典序列化为稳定的 JSON 字符串。"""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def format_markdown(payload: Dict[str, Any]) -> str:
    """Markdown 输出占位。Phase 1 起按命令逐个实现。"""

    command = payload.get("command", "")
    date = payload.get("date", "")
    return (
        f"# fundamental-screener: {command}\n\n"
        f"date: {date}\n\n"
        "Markdown output is not implemented in Phase 0.\n"
    )


def format_csv(payload: Dict[str, Any]) -> str:
    """CSV 输出占位。Phase 1 起按命令逐个实现。"""

    command = payload.get("command", "")
    return f"# csv output not implemented for command={command} in Phase 0\n"


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
