"""Read-time derived metrics for daily market review."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .schema import DailyMarketReviewAtoms, LadderStockRecord


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _format_ratio(left: int, right: int) -> str | None:
    if left is None or right is None:  # type: ignore[comparison-overlap]
        return None
    if left == 0 and right == 0:
        return None
    if left == 0:
        return f"0:{right}"
    if right == 0:
        return f"{left}:0"
    if left == right:
        return "1:1"
    if left < right:
        ratio = Decimal(right) / Decimal(left)
        return f"1:{_round2(float(ratio))}"
    ratio = Decimal(left) / Decimal(right)
    return f"{_round2(float(ratio))}:1"


def _index_change(close: float | None, prev_close: float | None) -> tuple[float | None, float | None]:
    if close is None or prev_close is None:
        return None, None
    points = _round2(close - prev_close)
    if prev_close == 0:
        return points, None
    pct = _round4((close - prev_close) / prev_close)
    return points, pct


def _sum_optional(*values: float | None) -> float | None:
    if any(value is None for value in values):
        return None
    return _round2(sum(values))  # type: ignore[arg-type]


def _round4(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _limit_up_failure_rate(failed: int | None, effective: int | None) -> float | None:
    if failed is None or effective is None:
        return None
    denominator = failed + effective
    if denominator == 0:
        return None
    return _round4(failed / denominator)


def _ladder_board_counts(stocks: list[LadderStockRecord]) -> dict[str, Any]:
    counts: dict[int, int] = {}
    for stock in stocks:
        counts[stock.streak_height] = counts.get(stock.streak_height, 0) + 1
    board_counts = {str(height): count for height, count in sorted(counts.items())}
    result: dict[str, Any] = {"board_counts": board_counts}
    for height in range(2, 11):
        result[f"board_{height}"] = counts.get(height, 0)
    result["board_11_plus"] = sum(count for height, count in counts.items() if height >= 11)
    return result


def compute_review_metrics(
    atoms: DailyMarketReviewAtoms,
    ladder_stocks: list[LadderStockRecord],
    *,
    previous_effective_limit_up: int | None,
) -> dict[str, Any]:
    sh_points, sh_pct = _index_change(atoms.sh_index_close, atoms.sh_index_prev_close)
    sz_points, sz_pct = _index_change(atoms.sz_index_close, atoms.sz_index_prev_close)
    cy_points, cy_pct = _index_change(atoms.cy_index_close, atoms.cy_index_prev_close)

    limit_ratio = None
    if atoms.effective_limit_up is not None and atoms.closed_limit_down is not None:
        limit_ratio = _format_ratio(atoms.effective_limit_up, atoms.closed_limit_down)

    ladder_count = len(ladder_stocks)
    if ladder_stocks:
        highest_board = max(stock.streak_height for stock in ladder_stocks)
        highest_board_representatives = [
            {
                "market": stock.market,
                "code": stock.code,
                "name": stock.name,
                "streak_height": stock.streak_height,
            }
            for stock in ladder_stocks
            if stock.streak_height == highest_board
        ]
    else:
        highest_board = 0
        highest_board_representatives = []

    streak_rate: float | None = None
    if previous_effective_limit_up is not None and previous_effective_limit_up > 0:
        streak_rate = _round4(ladder_count / previous_effective_limit_up)

    return {
        "limit_up_failure_rate": _limit_up_failure_rate(
            atoms.limit_up_failed,
            atoms.effective_limit_up,
        ),
        "limit_up_down_ratio": limit_ratio,
        "margin_balance_total": _sum_optional(
            atoms.margin_balance_sh,
            atoms.margin_balance_sz,
            atoms.margin_balance_bj,
        ),
        "turnover_amount_total": _sum_optional(
            atoms.turnover_amount_sh,
            atoms.turnover_amount_sz,
            atoms.turnover_amount_bj,
        ),
        "sh_index_change_points": sh_points,
        "sh_index_change_pct": sh_pct,
        "sz_index_change_points": sz_points,
        "sz_index_change_pct": sz_pct,
        "cy_index_change_points": cy_points,
        "cy_index_change_pct": cy_pct,
        "streak_count": ladder_count,
        "streak_rate": streak_rate,
        "highest_board": highest_board,
        "highest_board_representatives": highest_board_representatives,
        **_ladder_board_counts(ladder_stocks),
        "atoms": asdict(atoms),
    }
