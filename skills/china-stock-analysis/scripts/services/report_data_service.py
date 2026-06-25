from __future__ import annotations

from datetime import datetime

from market_data import INDICES, get_market_prefix
from services.indicator_service import IndicatorCalculator
from services.rule_evaluator import RuleEvaluator


class ReportDataService:
    """负责报告所需的业务数据准备，不直接负责 SQLite 或 Markdown。"""

    def __init__(
        self,
        paths,
        market_data,
        kline_data_service,
        target_date: datetime,
        is_historical: bool,
    ):
        self.paths = paths
        self.market_data = market_data
        self.kline_data_service = kline_data_service
        self.target_date = target_date
        self.is_historical = is_historical
        self.date_display = target_date.strftime("%Y-%m-%d")

    def load_config(self, filename: str) -> dict:
        filepath = self.paths.config_dir / filename
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            try:
                import yaml

                data = yaml.safe_load(content)
                return data if isinstance(data, dict) else {}
            except ImportError:
                pass

            return self._parse_simple_yaml(content)
        except Exception as exc:
            print(f"[WARN] 加载配置失败 {filename}: {exc}")
            return {}

    def _parse_simple_yaml(self, content: str) -> dict:
        lines = []
        for raw_line in content.splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            lines.append((indent, raw_line.strip()))

        def parse_scalar(value: str):
            value = value.strip()
            if not value:
                return None
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                return value[1:-1]
            if value.startswith("[") and value.endswith("]"):
                result = []
                for item in value[1:-1].split(","):
                    parsed = parse_scalar(item)
                    if parsed is not None:
                        result.append(parsed)
                return result
            if value.lower() in {"true", "false"}:
                return value.lower() == "true"
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                return value

        def parse_block(index: int, indent: int):
            if index >= len(lines) or lines[index][0] < indent:
                return None, index
            if lines[index][0] == indent and lines[index][1].startswith("- "):
                return parse_list(index, indent)
            return parse_map(index, indent)

        def parse_list(index: int, indent: int):
            result = []
            while index < len(lines):
                line_indent, stripped = lines[index]
                if line_indent < indent or line_indent != indent or not stripped.startswith("- "):
                    break
                item_content = stripped[2:].strip()
                if not item_content:
                    item, index = parse_block(index + 1, indent + 2)
                    result.append(item)
                    continue
                if ":" not in item_content:
                    result.append(parse_scalar(item_content))
                    index += 1
                    continue

                item = {}
                key, value = item_content.split(":", 1)
                key = key.strip()
                value = value.strip()
                if value:
                    item[key] = parse_scalar(value)
                    index += 1
                else:
                    child, index = parse_block(index + 1, indent + 2)
                    item[key] = child if child is not None else []

                while index < len(lines):
                    prop_indent, prop_line = lines[index]
                    if prop_indent <= indent:
                        break
                    if prop_indent != indent + 2 or prop_line.startswith("- ") or ":" not in prop_line:
                        break
                    prop_key, prop_value = prop_line.split(":", 1)
                    prop_key = prop_key.strip()
                    prop_value = prop_value.strip()
                    if prop_value:
                        item[prop_key] = parse_scalar(prop_value)
                        index += 1
                    else:
                        child, index = parse_block(index + 1, prop_indent + 2)
                        item[prop_key] = child if child is not None else []
                result.append(item)
            return result, index

        def parse_map(index: int, indent: int):
            result = {}
            while index < len(lines):
                line_indent, stripped = lines[index]
                if line_indent < indent or line_indent != indent or stripped.startswith("- ") or ":" not in stripped:
                    break
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()
                if value:
                    result[key] = parse_scalar(value)
                    index += 1
                else:
                    child, index = parse_block(index + 1, indent + 2)
                    result[key] = child if child is not None else {}
            return result, index

        parsed, _ = parse_block(0, 0)
        return parsed if isinstance(parsed, dict) else {}

    def get_index_data(self) -> list:
        if self.is_historical:
            return self._get_historical_index_data()
        return self._get_realtime_index_data()

    def _get_realtime_index_data(self) -> list:
        codes = [(code[2:], code[:2]) for code in INDICES.keys()]
        data = self.market_data.realtime(codes)
        for item in data:
            market = get_market_prefix(item["code"])
            full_code = f"{market}{item['code']}"
            if full_code in INDICES:
                item["index_name"] = INDICES[full_code][0]
                item["market"] = INDICES[full_code][1]
        return data

    def _get_historical_index_data(self) -> list:
        results = []
        date_str = self.date_display
        for full_code, (name, market) in INDICES.items():
            code = full_code[2:]
            prefix = full_code[:2]
            # 指数在腾讯 qfq 下返回空，必须走不复权；显式声明 security_type="index"，
            # 让 get_daily_quote -> get_kline 把 autype 折成 ""。
            data = self.market_data.get_daily_quote(code, date_str, market=prefix, security_type="index")
            if not data:
                continue
            results.append(
                {
                    "name": code,
                    "code": code,
                    "index_name": name,
                    "market": market,
                    "price": data["close"],
                    "pre_close": data["pre_close"],
                    "open": data["open"],
                    "high": data["high"],
                    "low": data["low"],
                    "volume": data["volume"],
                    "change": data["change"],
                    "change_pct": data["change_pct"],
                }
            )
        return results

    def get_watchlist_data(self) -> list:
        config = self.load_config("watchlist.yaml")
        watchlist = config.get("watchlist", [])
        if not watchlist:
            return []
        if self.is_historical:
            data = self._get_historical_watchlist_data(watchlist)
        else:
            data = self._get_realtime_watchlist_data(watchlist)
        return self._enrich_stock_data(data, watchlist)

    def _get_realtime_watchlist_data(self, watchlist: list) -> list:
        codes = [(item["code"], item.get("market")) for item in watchlist]
        quotes = self.market_data.realtime(codes)
        if isinstance(quotes, dict):
            quotes = [quotes]

        code_to_config = {item["code"]: item for item in watchlist}
        for quote in quotes:
            config_item = code_to_config.get(quote["code"], {})
            quote["tags"] = config_item.get("tags", [])
        return quotes

    def _get_historical_watchlist_data(self, watchlist: list) -> list:
        results = []
        date_str = self.date_display
        for item in watchlist:
            code = item["code"]
            market = item.get("market")
            data = self.market_data.get_daily_quote(code, date_str, market=market)
            if not data:
                continue
            results.append(
                {
                    "name": item.get("name", code),
                    "code": code,
                    "price": data["close"],
                    "pre_close": data["pre_close"],
                    "open": data["open"],
                    "high": data["high"],
                    "low": data["low"],
                    "volume": data["volume"],
                    "change": data["change"],
                    "change_pct": data["change_pct"],
                    "tags": item.get("tags", []),
                }
            )
        return results

    def get_portfolio_data(self) -> list:
        config = self.load_config("portfolio.yaml")
        portfolio = config.get("portfolio", [])
        if not portfolio:
            return []
        if self.is_historical:
            data = self._get_historical_portfolio_data(portfolio)
        else:
            data = self._get_realtime_portfolio_data(portfolio)
        return self._enrich_stock_data(data, portfolio)

    def _get_realtime_portfolio_data(self, portfolio: list) -> list:
        codes = [(item["code"], item.get("market")) for item in portfolio]
        quotes = self.market_data.realtime(codes)
        if isinstance(quotes, dict):
            quotes = [quotes]

        code_to_config = {item["code"]: item for item in portfolio}
        results = []
        for quote in quotes:
            config_item = code_to_config.get(quote["code"], {})
            position = config_item.get("position", 0)
            cost_price = config_item.get("cost_price", 0)
            current_price = quote["price"]
            profit_per_share = current_price - cost_price if cost_price > 0 else 0
            total_profit = profit_per_share * position
            profit_pct = (profit_per_share / cost_price * 100) if cost_price > 0 else 0
            market_value = current_price * position
            cost_value = cost_price * position

            quote["position"] = position
            quote["cost_price"] = cost_price
            quote["market_value"] = round(market_value, 2)
            quote["cost_value"] = round(cost_value, 2)
            quote["total_profit"] = round(total_profit, 2)
            quote["profit_pct"] = round(profit_pct, 2)
            quote["target_weight"] = config_item.get("target_weight")
            quote["broker"] = config_item.get("broker")
            quote["buy_orders"] = config_item.get("buy_orders", [])
            results.append(quote)
        return results

    def _get_historical_portfolio_data(self, portfolio: list) -> list:
        results = []
        date_str = self.date_display
        for item in portfolio:
            code = item["code"]
            market = item.get("market")
            position = item.get("position", 0)
            cost_price = item.get("cost_price", 0)
            data = self.market_data.get_daily_quote(code, date_str, market=market)
            if not data:
                continue

            current_price = data["close"]
            profit_per_share = current_price - cost_price if cost_price > 0 else 0
            total_profit = profit_per_share * position
            profit_pct = (profit_per_share / cost_price * 100) if cost_price > 0 else 0
            market_value = current_price * position
            cost_value = cost_price * position

            results.append(
                {
                    "name": item.get("name", code),
                    "code": code,
                    "price": current_price,
                    "pre_close": data["pre_close"],
                    "open": data["open"],
                    "high": data["high"],
                    "low": data["low"],
                    "volume": data["volume"],
                    "change": data["change"],
                    "change_pct": data["change_pct"],
                    "position": position,
                    "cost_price": cost_price,
                    "market_value": round(market_value, 2),
                    "cost_value": round(cost_value, 2),
                    "total_profit": round(total_profit, 2),
                    "profit_pct": round(profit_pct, 2),
                    "target_weight": item.get("target_weight"),
                    "broker": item.get("broker"),
                    "buy_orders": item.get("buy_orders", []),
                }
            )
        return results

    def _enrich_stock_data(self, data: list, config_items: list) -> list:
        config_by_code = {item["code"]: item for item in config_items}
        for item in data:
            code = item["code"]
            config = config_by_code.get(code, {})
            market = config.get("market") or item.get("market")
            item["market"] = market
            klines = self.kline_data_service.get_klines(
                code=code,
                end_date=self.date_display,
                market=market,
                timeframe="day",
                limit=120,
            )
            float_shares = config.get("float_shares")
            indicators = IndicatorCalculator.calculate(klines, float_shares=float_shares)
            item["klines_count"] = len(klines)
            item["last_kline"] = klines[-1] if klines else None
            item["prev_kline"] = klines[-2] if len(klines) >= 2 else None
            item["indicators"] = indicators
            item["risk_flags"] = RuleEvaluator.build_risk_flags(item)
        return data

    def load_strategy_rules(self) -> list:
        config = self.load_config("strategy_rules.yaml")
        return config.get("strategies", [])

    @staticmethod
    def dedupe_stock_rows(rows: list) -> list:
        seen = set()
        result = []
        for row in rows:
            key = row.get("code")
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result
