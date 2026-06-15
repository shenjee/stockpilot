#!/usr/bin/env python3
"""
中国A股个股分析与行情报告 - 兼容入口
生成事实型行情报告，不做买卖建议
支持实时数据和历史数据
"""

from cli import main
from report_orchestrator import ReportGenerator, ReportOrchestrator
from repositories.kline_store import KLineStore, resolve_market_data_db_path
from runtime_paths import RuntimePaths
from services.indicator_service import IndicatorCalculator
from services.kline_data_service import KLineDataService
from services.report_data_service import ReportDataService
from services.rule_evaluator import RuleEvaluator

__all__ = [
    "IndicatorCalculator",
    "KLineDataService",
    "KLineStore",
    "ReportDataService",
    "ReportGenerator",
    "ReportOrchestrator",
    "RuleEvaluator",
    "RuntimePaths",
    "main",
    "resolve_market_data_db_path",
]


if __name__ == "__main__":
    main()
