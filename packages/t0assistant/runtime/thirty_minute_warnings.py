"""Frozen 30-minute warning payloads for Live / Replay / Historical snapshots."""

from __future__ import annotations

from typing import Any, Mapping


THIRTY_MINUTE_OFFICIAL_DELAYED: Mapping[str, Any] = {
    "warning_code": "thirty_minute_official_delayed",
    "severity": "warning",
    "message": "正式 30 分钟 K 尚未到达，仍显示临时值",
    "affected_capability": "thirty_minute_chart",
    "affected_field": "market.bars_30m",
    "details": {},
}

THIRTY_MINUTE_MARKET_DATA_UNAVAILABLE: Mapping[str, Any] = {
    "warning_code": "thirty_minute_market_data_unavailable",
    "severity": "warning",
    "message": "30 分钟行情不可用，5 分钟与分时不受影响",
    "affected_capability": "thirty_minute_chart",
    "affected_field": "market.bars_30m",
    "details": {},
}

THIRTY_MINUTE_INDICATORS_UNAVAILABLE: Mapping[str, Any] = {
    "warning_code": "thirty_minute_indicators_unavailable",
    "severity": "warning",
    "message": "30 分钟指标不可用，已保留空指标结构",
    "affected_capability": "thirty_minute_chart",
    "affected_field": "indicators.thirty_minute",
    "details": {},
}

THIRTY_MINUTE_CHAN_ANALYSIS_UNAVAILABLE: Mapping[str, Any] = {
    "warning_code": "thirty_minute_chan_analysis_unavailable",
    "severity": "warning",
    "message": "30 分钟缠论分析不可用，已保留空分析结果",
    "affected_capability": "thirty_minute_chart",
    "affected_field": "chan_analysis_30m",
    "details": {},
}


def warning_dict(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a mutable copy suitable for PipelineResult.warnings."""

    return {
        "warning_code": payload["warning_code"],
        "severity": payload["severity"],
        "message": payload["message"],
        "affected_capability": payload["affected_capability"],
        "affected_field": payload["affected_field"],
        "details": dict(payload.get("details") or {}),
    }
