class RuleEvaluator:
    """规则事实检查与风险提示。"""

    @staticmethod
    def build_risk_flags(item: dict) -> list:
        flags = []
        change_pct = item.get("change_pct", 0)
        profit_pct = item.get("profit_pct")
        indicators = item.get("indicators") or {}
        ma = indicators.get("ma") or {}
        price = item.get("price")

        if change_pct <= -5:
            flags.append(f"单日跌幅 {abs(change_pct):.2f}% ，波动较大")
        if profit_pct is not None and profit_pct <= -10:
            flags.append(f"浮亏 {abs(profit_pct):.2f}% ，已超过10%观察阈值")
        if price and ma.get(20) and price < ma[20]:
            flags.append("收盘价位于MA20下方")
        if price and ma.get(60) and price < ma[60]:
            flags.append("收盘价位于MA60下方")
        turnover_rate = indicators.get("turnover_rate")
        if turnover_rate and turnover_rate >= 15:
            flags.append(f"换手率 {turnover_rate:.2f}% ，交易分歧较高")
        return flags

    @staticmethod
    def evaluate_strategy_facts(rules: list, stocks: list) -> list:
        results = []
        for stock in stocks:
            indicators = stock.get("indicators") or {}
            ma = indicators.get("ma") or {}
            prev_ma = indicators.get("prev_ma") or {}
            price = stock.get("price")
            prev = stock.get("prev_kline") or {}
            for rule in rules:
                check = rule.get("check")
                if not check:
                    continue
                if check == "below_ma20" and price and ma.get(20) and price < ma[20]:
                    results.append(f"- {stock['name']} ({stock['code']})：低于MA20，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "below_ma60" and price and ma.get(60) and price < ma[60]:
                    results.append(f"- {stock['name']} ({stock['code']})：低于MA60，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "break_below_ma5" and price and prev.get("close") and ma.get(5) and prev_ma.get(5):
                    if prev["close"] >= prev_ma[5] and price < ma[5]:
                        results.append(f"- {stock['name']} ({stock['code']})：跌破MA5，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
                elif check == "volume_above_20ma":
                    volume_ma20 = indicators.get("volume_ma20")
                    if volume_ma20 and stock.get("volume", 0) >= volume_ma20 * 1.2:
                        results.append(f"- {stock['name']} ({stock['code']})：成交量高于20日均量，匹配规则「{rule.get('name', rule.get('text', '未命名'))}」")
        return results
