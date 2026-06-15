from __future__ import annotations

from datetime import datetime
from pathlib import Path

from market_data import create_market_data_provider
from repositories.kline_store import KLineStore, resolve_market_data_db_path
from renderers.markdown_report_renderer import MarkdownReportRenderer
from runtime_paths import RuntimePaths
from services.kline_data_service import KLineDataService
from services.report_data_service import ReportDataService
from services.rule_evaluator import RuleEvaluator


class ReportOrchestrator:
    """报告业务编排器。"""

    def __init__(self, target_date: str | None = None, paths: RuntimePaths | None = None, market_data_provider=None):
        self.paths = paths or RuntimePaths()
        self.market_data = market_data_provider or create_market_data_provider(self.paths.market_data_provider)
        db_path = resolve_market_data_db_path(self.paths.db_dir)
        self.kline_store = KLineStore(db_path)

        if target_date:
            self.target_date = datetime.strptime(target_date, "%Y-%m-%d")
            self.is_historical = True
        else:
            self.target_date = datetime.now()
            self.is_historical = False

        self.date_str = self.target_date.strftime("%Y%m%d")
        self.date_display = self.target_date.strftime("%Y-%m-%d")
        self.kline_data_service = KLineDataService(self.market_data, self.kline_store)
        self.data_service = ReportDataService(
            paths=self.paths,
            market_data=self.market_data,
            kline_data_service=self.kline_data_service,
            target_date=self.target_date,
            is_historical=self.is_historical,
        )
        self.renderer = MarkdownReportRenderer()

    def is_trading_day(self) -> bool:
        return self.target_date.weekday() < 5

    def generate_report(self, report_type: str = "close", force: bool = False) -> str | None:
        if not force and not self.is_trading_day():
            return None

        index_data = self.data_service.get_index_data()
        watchlist_data = self.data_service.get_watchlist_data()
        portfolio_data = self.data_service.get_portfolio_data()
        strategy_rules = self.data_service.load_strategy_rules()
        active_rules = [rule for rule in strategy_rules if rule.get("status", "active") in ("active", "testing")]
        triggered_rules = RuleEvaluator.evaluate_strategy_facts(
            active_rules,
            self.data_service.dedupe_stock_rows(portfolio_data + watchlist_data),
        )

        return self.renderer.render_report(
            report_type=report_type,
            date_display=self.date_display,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data_source_name=self.market_data.name,
            is_historical=self.is_historical,
            index_data=index_data,
            watchlist_data=watchlist_data,
            portfolio_data=portfolio_data,
            strategy_rules=strategy_rules,
            triggered_rules=triggered_rules,
        )

    def save_report(self, content: str, report_type: str = "close") -> Path:
        self.paths.report_dir.mkdir(parents=True, exist_ok=True)
        suffix = "close" if report_type == "close" else "review"
        filename = f"daily_report_{self.date_str}_{suffix}.md"
        filepath = self.paths.report_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath


ReportGenerator = ReportOrchestrator
