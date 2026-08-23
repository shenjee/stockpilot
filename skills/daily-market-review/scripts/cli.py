#!/usr/bin/env python3
"""CLI for daily market review skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.marketreview import (  # noqa: E402
    LadderItemPatch,
    LadderResetMissing,
    LadderSnapshotReplace,
    LadderStockInput,
    MarketReviewRepository,
    default_market_review_db_path,
)


def _load_patch_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise SystemExit("patch 文件必须是 YAML 对象")
    return payload


def _build_ladder_operation(payload: dict):
    mode = payload.get("ladder_mode")
    if mode is None and payload.get("ladder_status") == "complete":
        mode = "snapshot_replace"
    if mode == "reset_missing":
        return LadderResetMissing()
    if mode == "item_patch":
        upserts = [
            LadderStockInput(
                market=item["market"],
                code=str(item["code"]),
                name=item["name"],
                streak_height=int(item["streak_height"]),
                is_st=bool(item.get("is_st", False)),
            )
            for item in payload.get("ladder_upserts", [])
        ]
        deletes = [(item["market"], str(item["code"])) for item in payload.get("ladder_deletes", [])]
        return LadderItemPatch(upserts=upserts, deletes=deletes)
    if mode == "snapshot_replace" or payload.get("ladder_stocks") is not None:
        stocks = [
            LadderStockInput(
                market=item["market"],
                code=str(item["code"]),
                name=item["name"],
                streak_height=int(item["streak_height"]),
                is_st=bool(item.get("is_st", False)),
            )
            for item in payload.get("ladder_stocks", [])
        ]
        return LadderSnapshotReplace(stocks=stocks)
    return None


def cmd_show(args: argparse.Namespace) -> int:
    with MarketReviewRepository(args.db) as repo:
        view = repo.get_review(args.date)
    if view is None:
        print(f"未找到 {args.date} 的总体复盘")
        return 1
    print(json.dumps(
        {
            "trade_date": view.atoms.trade_date,
            "ladder_status": view.atoms.ladder_status,
            "atoms": {
                key: getattr(view.atoms, key)
                for key in view.atoms.__dataclass_fields__
                if key not in {"trade_date", "ladder_status"}
            },
            "computed": view.computed,
            "ladder_stocks": [
                {
                    "market": stock.market,
                    "code": stock.code,
                    "name": stock.name,
                    "streak_height": stock.streak_height,
                }
                for stock in view.ladder_stocks
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    payload = _load_patch_file(Path(args.file))
    trade_date = payload.get("trade_date")
    if not trade_date:
        raise SystemExit("patch 文件必须包含 trade_date")
    fields = payload.get("fields", {})
    ladder_operation = _build_ladder_operation(payload)
    with MarketReviewRepository(args.db) as repo:
        repo.patch_review(trade_date, fields=fields, ladder_operation=ladder_operation)
    print(f"已更新 {trade_date} 的总体复盘")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily market review CLI")
    parser.add_argument("--db", type=Path, default=default_market_review_db_path())
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="查看指定交易日复盘")
    show.add_argument("--date", required=True)
    show.set_defaults(func=cmd_show)

    patch = subparsers.add_parser("patch", help="按 YAML 文件 patch 复盘")
    patch.add_argument("--file", required=True)
    patch.set_defaults(func=cmd_patch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
