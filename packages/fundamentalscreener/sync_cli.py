from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m packages.fundamentalscreener.sync",
        description="Fundamental Screener 数据治理同步入口（Phase 6A）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="幂等初始化 SQLite schema。")
    p_init.add_argument("--db", required=True, help="SQLite 路径。")

    p_sync = sub.add_parser(
        "sync",
        help="运行同步任务。默认接入同花顺行业板块（ths_industry），东方财富（em_industry）为对照源。",
    )
    p_sync.add_argument("--db", required=True)
    p_sync.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD。")
    p_sync.add_argument(
        "--classification-system",
        dest="classification_system",
        default="ths_industry",
        help="板块分类口径，默认 ths_industry（同花顺）。em_industry 为东方财富对照源。",
    )
    p_sync.add_argument("--benchmark", default="hs300")
    p_sync.add_argument(
        "--history-days",
        dest="history_days",
        type=int,
        default=90,
        help="回采历史天数（自然日），需覆盖 60 个交易日以支持 60 日收益。",
    )
    p_sync.add_argument(
        "--codes",
        default="",
        help="逗号分隔的股票代码。未提供时跳过 per-code 公司层任务（估值历史 + "
        "财务指标），仅运行 batch 任务（股票池 + 日度快照）。",
    )
    p_sync.add_argument(
        "--sector-ids",
        dest="sector_ids",
        default="",
        help="逗号分隔的板块代码（§15.9.5 按需加载）。非空时只抓指定板块的成分股，"
        "并从成分股派生 codes 驱动个股层任务。未提供时回退全量行为。",
    )

    p_quality = sub.add_parser(
        "quality", help="读取 SQLite 并输出结构化质量报告（Phase 6D）。"
    )
    p_quality.add_argument("--db", required=True)
    p_quality.add_argument("--date", required=True)
    p_quality.add_argument(
        "--classification-system",
        dest="classification_system",
        default="ths_industry",
        help="板块分类口径，默认 ths_industry（同花顺）。",
    )
    p_quality.add_argument("--benchmark", default="hs300")

    return parser


def _parse_codes(raw: str) -> Optional[List[str]]:
    """解析 ``--codes`` 参数：逗号分隔的股票代码列表，空串返回 ``None``。"""

    if not raw:
        return None
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    return codes or None


def _parse_sector_ids(raw: str) -> Optional[List[str]]:
    """解析 ``--sector-ids`` 参数：逗号分隔的板块代码列表，空串返回 ``None``。"""

    if not raw:
        return None
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    return ids or None


def compute_sync_exit_code(
    tasks: Sequence[Dict[str, object]],
    failure_count: int,
    light_required_tasks: Sequence[str],
    detail_required_tasks: Sequence[str],
) -> int:
    """Return the CLI rc for a sync run based on required task success."""

    by_task = {str(task["task"]): task for task in tasks}
    required_tasks = list(light_required_tasks)
    required_tasks.extend(detail_required_tasks)
    required_ok = all(
        by_task.get(task_name, {}).get("success")
        and int(by_task.get(task_name, {}).get("row_count", 0) or 0) > 0
        for task_name in required_tasks
    )
    return 0 if (failure_count == 0 and required_ok) else 1
