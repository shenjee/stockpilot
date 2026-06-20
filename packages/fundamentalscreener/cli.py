"""Fundamental Screener CLI 入口。

稳定调用方式（Phase 0）：

    python -m packages.fundamentalscreener.cli <command> [options]

支持的命令：sectors / sector-detail / companies / financials / valuations / screen。
Phase 0 仅返回稳定 JSON 框架，不实现真实板块/公司/财务/估值计算。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Sequence

from .config import (
    DEFAULT_BENCHMARK,
    DEFAULT_CLASSIFICATION_SYSTEM,
    DEFAULT_FORMAT,
    DEFAULT_PERIODS,
    DEFAULT_SECTOR_SORT,
    DEFAULT_TOP,
    SUPPORTED_CLASSIFICATION_SYSTEMS,
    SUPPORTED_FORMATS,
    SUPPORTED_SECTOR_SORTS,
)
from .formatting import format_output
from .repositories import FixtureRepository
from .schema import (
    CandidatesPayload,
    CompaniesPayload,
    FinancialsPayload,
    ScreenPayload,
    SectorsPayload,
    ValuationsPayload,
)
from .sector_rotation import compute_sector_rotation, sort_entries


# ---------------------------------------------------------------------------
# argparse 构造
# ---------------------------------------------------------------------------


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", default=None, help="分析日期，格式 YYYY-MM-DD。默认使用 fixture 中的 date。")
    parser.add_argument(
        "--format",
        dest="fmt",
        default=DEFAULT_FORMAT,
        choices=SUPPORTED_FORMATS,
        help="输出格式，默认 json。",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="使用 fixture JSON 文件作为数据源。Phase 0/1 必须支持。",
    )


def _add_classification_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--classification-system",
        dest="classification_system",
        default=DEFAULT_CLASSIFICATION_SYSTEM,
        choices=SUPPORTED_CLASSIFICATION_SYSTEMS,
        help="板块分类口径，Phase 0/1 仅作为字符串字段保留。",
    )


class CLIArgumentError(Exception):
    """CLI 参数语义错误。由 main() 统一映射到退出码 2 + stderr。"""


def _parse_periods(raw: Optional[str]) -> List[int]:
    """解析 ``--periods`` 为正整数列表。

    Skill/CLI 调用方不应在 stdout 上看到 Python traceback，因此非法值统一通过
    ``CLIArgumentError`` 抛出，并由 ``main()`` 转换成 ``rc=2`` + stderr。
    """

    if not raw:
        return list(DEFAULT_PERIODS)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[int] = []
    for p in parts:
        try:
            value = int(p)
        except ValueError as exc:
            raise CLIArgumentError(
                f"invalid --periods value: {p!r} (must be a positive integer)"
            ) from exc
        if value <= 0:
            raise CLIArgumentError(
                f"invalid --periods value: {p!r} (must be a positive integer)"
            )
        out.append(value)
    if not out:
        raise CLIArgumentError(
            "invalid --periods value: (must contain at least one positive integer)"
        )
    return out


def _parse_codes(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m packages.fundamentalscreener.cli",
        description="StockPilot Fundamental Screener CLI (Phase 0).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sectors
    p_sectors = sub.add_parser("sectors", help="板块轮动指标。")
    _add_common_args(p_sectors)
    _add_classification_arg(p_sectors)
    p_sectors.add_argument(
        "--benchmark",
        default=None,
        help="基准指数 ID。Phase 1 必须与 fixture 中 benchmark.id 一致；缺省时使用 fixture 值。",
    )
    p_sectors.add_argument("--periods", default=None, help="逗号分隔的整数周期列表，默认 1,5,20,60。")
    p_sectors.add_argument("--sort", default=DEFAULT_SECTOR_SORT, choices=SUPPORTED_SECTOR_SORTS)
    p_sectors.add_argument("--top", type=int, default=DEFAULT_TOP)

    # sector-detail
    p_detail = sub.add_parser("sector-detail", help="单板块详情。")
    _add_common_args(p_detail)
    _add_classification_arg(p_detail)
    p_detail.add_argument("--sector", required=True, help="板块 sector_id 或 sector_name。")
    p_detail.add_argument(
        "--benchmark",
        default=None,
        help="基准指数 ID。Phase 1 必须与 fixture 中 benchmark.id 一致；缺省时使用 fixture 值。",
    )
    p_detail.add_argument("--periods", default=None)

    # companies
    p_companies = sub.add_parser("companies", help="板块内公司排名。")
    _add_common_args(p_companies)
    _add_classification_arg(p_companies)
    p_companies.add_argument("--sector", required=True, help="板块 sector_id 或 sector_name。")
    p_companies.add_argument("--top", type=int, default=DEFAULT_TOP)
    p_companies.add_argument("--sort", default="combined_score")

    # financials
    p_fin = sub.add_parser("financials", help="财务质量横向对比。")
    _add_common_args(p_fin)
    p_fin.add_argument("--codes", default=None, help="逗号分隔的股票代码。")
    p_fin.add_argument("--sort", default="score")

    # valuations
    p_val = sub.add_parser("valuations", help="估值横向对比。")
    _add_common_args(p_val)
    p_val.add_argument("--codes", default=None, help="逗号分隔的股票代码。")
    p_val.add_argument("--sort", default="score")

    # screen
    p_screen = sub.add_parser("screen", help="完整筛选编排。")
    _add_common_args(p_screen)
    _add_classification_arg(p_screen)
    p_screen.add_argument(
        "--benchmark",
        default=None,
        help="基准指数 ID。Phase 1 必须与 fixture 中 benchmark.id 一致；缺省时使用 fixture 值。",
    )
    p_screen.add_argument("--sector-top", dest="sector_top", type=int, default=10)
    p_screen.add_argument("--company-top", dest="company_top", type=int, default=5)

    return parser


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_repo(fixture: Optional[str]) -> Optional[FixtureRepository]:
    """构造 FixtureRepository 并立即触发一次 ``load_snapshot()``。

    Phase 0 要求只要传了 ``--fixture``，文件必须被读取，而不是被某些 handler
    悄悄绕过。把加载放在这里能保证所有命令的行为一致：传错路径会立即抛
    ``FileNotFoundError``，而不是返回看起来正常的 JSON。
    """

    if not fixture:
        return None
    repo = FixtureRepository(Path(fixture))
    repo.load_snapshot()
    return repo


def _resolve_date(explicit: Optional[str], repo: Optional[FixtureRepository]) -> str:
    if explicit:
        return explicit
    if repo is not None:
        return repo.load_snapshot().date or ""
    return ""


def _resolve_classification(
    explicit: Optional[str], repo: Optional[FixtureRepository]
) -> str:
    if explicit:
        return explicit
    if repo is not None:
        return repo.load_snapshot().classification_system or DEFAULT_CLASSIFICATION_SYSTEM
    return DEFAULT_CLASSIFICATION_SYSTEM


class BenchmarkMismatchError(Exception):
    """显式 ``--benchmark`` 与 fixture 内 benchmark.id 不一致。"""


def _resolve_benchmark(
    explicit: Optional[str], repo: Optional[FixtureRepository]
) -> str:
    """决定权威 benchmark id，并阻止"显示一个基准、计算另一个"的不一致。

    Phase 1 只支持 fixture 内置的单个基准：
    - 没传 ``--benchmark`` 时，返回 fixture 的 ``benchmark.id``。
    - 传了 ``--benchmark`` 但 fixture 没有可用 ID 时，返回参数值，仅作为字段透传。
    - 传了 ``--benchmark`` 且与 fixture ID 不一致时，抛
      ``BenchmarkMismatchError``，由 ``main()`` 统一转成退出码 2。
    """

    fixture_id: Optional[str] = None
    if repo is not None:
        snap = repo.load_snapshot()
        fixture_id = snap.benchmark.id or None

    if not explicit:
        if fixture_id:
            return fixture_id
        return DEFAULT_BENCHMARK

    if fixture_id and explicit != fixture_id:
        raise BenchmarkMismatchError(
            f"benchmark_mismatch: --benchmark={explicit!r} but fixture benchmark.id={fixture_id!r}"
        )
    return explicit


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------


def _cmd_sectors(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    payload = SectorsPayload(
        command="sectors",
        date=_resolve_date(args.date, repo),
        classification_system=_resolve_classification(args.classification_system, repo),
        benchmark=_resolve_benchmark(args.benchmark, repo),
        sort=args.sort,
        periods=_parse_periods(args.periods),
        sectors=[],
        chart_series=[],
        warnings=[],
    )
    if repo is None:
        payload.warnings.append("no_data_source: pass --fixture to load market data")
    else:
        snapshot = repo.load_snapshot()
        result = compute_sector_rotation(snapshot, periods=tuple(payload.periods))
        ordered = sort_entries(result.sectors, args.sort)
        if args.top is not None and args.top >= 0:
            ordered = ordered[: args.top]
        payload.sectors = list(ordered)
        payload.chart_series = list(result.chart_series)
        payload.warnings.extend(result.warnings)
    return format_output(payload.to_dict(), args.fmt)


def _cmd_sector_detail(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    payload = SectorsPayload(
        command="sector-detail",
        date=_resolve_date(args.date, repo),
        classification_system=_resolve_classification(args.classification_system, repo),
        benchmark=_resolve_benchmark(args.benchmark, repo),
        sort="return_1d",
        periods=_parse_periods(args.periods),
        sectors=[],
        chart_series=[],
        warnings=[],
    )
    if repo is None:
        payload.warnings.append("no_data_source: pass --fixture to load market data")
        return format_output(payload.to_dict(), args.fmt)

    snapshot = repo.load_snapshot()
    target = repo.find_sector(args.sector)
    if target is None:
        payload.warnings.append(f"sector_not_found: {args.sector}")
        return format_output(payload.to_dict(), args.fmt)

    result = compute_sector_rotation(snapshot, periods=tuple(payload.periods))
    target_entries = [e for e in result.sectors if e.sector_id == target.sector_id]
    target_series = [
        s
        for s in result.chart_series
        if s.type == "benchmark" or s.series_id == target.sector_id
    ]
    payload.sectors = target_entries
    payload.chart_series = target_series
    payload.warnings.extend(result.warnings)
    return format_output(payload.to_dict(), args.fmt)


def _cmd_companies(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    sector_id: Optional[str] = None
    sector_name: Optional[str] = None
    warnings: List[str] = []
    if repo is None:
        warnings.append("no_data_source: pass --fixture to load market data")
    else:
        target = repo.find_sector(args.sector)
        if target is None:
            warnings.append(f"sector_not_found: {args.sector}")
        else:
            sector_id = target.sector_id
            sector_name = target.sector_name
    payload = CompaniesPayload(
        command="companies",
        date=_resolve_date(args.date, repo),
        classification_system=_resolve_classification(args.classification_system, repo),
        sector_id=sector_id,
        sector_name=sector_name,
        sort=args.sort,
        companies=[],
        warnings=warnings,
    )
    return format_output(payload.to_dict(), args.fmt)


def _cmd_financials(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    warnings: List[str] = []
    if repo is None:
        warnings.append("no_data_source: pass --fixture to load market data")
    codes = _parse_codes(args.codes)
    if not codes:
        warnings.append("no_codes_provided: pass --codes A,B,C")
    payload = FinancialsPayload(
        command="financials",
        date=_resolve_date(args.date, repo),
        companies=[],
        warnings=warnings,
    )
    return format_output(payload.to_dict(), args.fmt)


def _cmd_valuations(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    warnings: List[str] = []
    if repo is None:
        warnings.append("no_data_source: pass --fixture to load market data")
    codes = _parse_codes(args.codes)
    if not codes:
        warnings.append("no_codes_provided: pass --codes A,B,C")
    payload = ValuationsPayload(
        command="valuations",
        date=_resolve_date(args.date, repo),
        companies=[],
        warnings=warnings,
    )
    return format_output(payload.to_dict(), args.fmt)


def _cmd_screen(args: argparse.Namespace) -> str:
    repo = _load_repo(args.fixture)
    warnings: List[str] = []
    if repo is None:
        warnings.append("no_data_source: pass --fixture to load market data")
    payload = ScreenPayload(
        command="screen",
        date=_resolve_date(args.date, repo),
        classification_system=_resolve_classification(args.classification_system, repo),
        benchmark=_resolve_benchmark(args.benchmark, repo),
        selected_sectors=[],
        candidates=CandidatesPayload(),
        warnings=warnings,
        generated_at=_now_iso(),
    )
    return format_output(payload.to_dict(), args.fmt)


_HANDLERS = {
    "sectors": _cmd_sectors,
    "sector-detail": _cmd_sector_detail,
    "companies": _cmd_companies,
    "financials": _cmd_financials,
    "valuations": _cmd_valuations,
    "screen": _cmd_screen,
}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse 已限制
        parser.error(f"unknown command: {args.command}")
        return 2
    try:
        output = handler(args)
    except FileNotFoundError as exc:
        sys.stderr.write(f"fixture_not_found: {exc}\n")
        return 2
    except BenchmarkMismatchError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    except CLIArgumentError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
