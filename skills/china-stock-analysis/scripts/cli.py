import argparse
import sys
from pathlib import Path

from report_orchestrator import ReportOrchestrator
from runtime_paths import RuntimePaths


WATCHLIST_TEMPLATE = """# 自选股配置
# 请在下方添加你关注的股票

watchlist:
  # 示例：
  # - code: "600519"
  #   name: "贵州茅台"
  #   float_shares: 1000000000  # 流通股本（股，可选）；配置后可计算换手率
  #   tags: ["白酒", "核心资产"]
  # - code: "300750"
  #   name: "宁德时代"
  #   tags: ["新能源", "创业板"]
"""

PORTFOLIO_TEMPLATE = """# 持仓配置
# 请在下方添加你的持仓记录

portfolio:
  # 示例：
  # - code: "600111"
  #   name: "北方稀土"
  #   position: 1000        # 持仓数量（股）
  #   cost_price: 25.50     # 成本价（元）
  #   float_shares: 100000000  # 流通股本（股，可选）；配置后可计算换手率
  #   target_weight: 0.15   # 目标仓位占比（可选）
"""

STRATEGY_TEMPLATE = """# 经验策略规则
# status: active/testing/deprecated/conflict
# check 为可选字段，用于日报中的事实检查，不生成买卖建议。

strategies:
  - name: "20日均线趋势过滤"
    category: "均线"
    status: "active"
    check: "below_ma20"
    text: "不上20日均线，不确认趋势条件"
  - name: "60日均线长期过滤"
    category: "均线"
    status: "active"
    check: "below_ma60"
    text: "长期位于60日均线下方的标的保持谨慎观察"
  - name: "5日均线短线风险"
    category: "均线"
    status: "testing"
    check: "break_below_ma5"
    text: "跌破5日均线时记录短线风险变化"
  - name: "量能观察"
    category: "成交量"
    status: "testing"
    check: "volume_above_20ma"
    text: "成交量高于20日均量时记录量能变化"
"""


def ensure_example_configs(paths: RuntimePaths) -> None:
    templates = {
        paths.config_dir / "watchlist.yaml": WATCHLIST_TEMPLATE,
        paths.config_dir / "portfolio.yaml": PORTFOLIO_TEMPLATE,
        paths.config_dir / "strategy_rules.yaml": STRATEGY_TEMPLATE,
    }
    for path, template in templates.items():
        if not path.exists():
            with open(path, "w", encoding="utf-8") as f:
                f.write(template)


def main() -> None:
    parser = argparse.ArgumentParser(description="A股个股分析与行情报告生成器")
    parser.add_argument("--type", choices=["close", "review"], default="close", help="报告类型：close=收盘简报, review=复盘报告")
    parser.add_argument("--date", "-d", help="指定日期，格式：2025-05-22（默认今天）")
    parser.add_argument("--output", "-o", help="输出文件路径（默认保存到reports目录）")
    parser.add_argument(
        "--config",
        help="运行配置JSON路径；默认查找 CHINA_STOCK_ANALYSIS_CONFIG、CHINA_STOCK_DAILY_TRACKER_CONFIG、当前目录 china-stock-analysis.local.json、china-stock-analysis.json 及旧版 china-stock-daily-tracker*.json",
    )
    parser.add_argument("--force", action="store_true", help="强制生成报告（忽略交易日检查）")
    args = parser.parse_args()

    paths = RuntimePaths(config_file=args.config)
    paths.ensure_dirs()
    ensure_example_configs(paths)

    orchestrator = ReportOrchestrator(target_date=args.date, paths=paths)
    if not args.force and not orchestrator.is_trading_day():
        print(f"[{orchestrator.date_display}] 非交易日，跳过报告生成（使用 --force 强制生成）")
        sys.exit(0)

    print(f"[{orchestrator.date_display}] 正在生成{('收盘简报' if args.type == 'close' else '复盘报告')}...")
    report = orchestrator.generate_report(args.type, force=args.force)
    if not report:
        print("报告生成失败")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已保存: {output_path}")
    else:
        filepath = orchestrator.save_report(report, args.type)
        print(f"报告已保存: {filepath}")

    print("\n" + "=" * 50)
    print(report)
    print("=" * 50)


if __name__ == "__main__":
    main()
