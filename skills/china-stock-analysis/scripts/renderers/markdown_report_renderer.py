from services.indicator_service import IndicatorCalculator


class MarkdownReportRenderer:
    """把结构化报告数据渲染为 Markdown。"""

    @staticmethod
    def format_volume(volume: int) -> str:
        if volume >= 100000000:
            return f"{volume / 100000000:.2f}亿手"
        if volume >= 10000:
            return f"{volume / 10000:.2f}万手"
        return f"{volume}手"

    @staticmethod
    def _dedupe_stock_rows(rows: list) -> list:
        seen = set()
        result = []
        for row in rows:
            key = row.get("code")
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    def generate_index_section(self, data: list, is_historical: bool = False) -> str:
        lines = ["## 📊 主要指数行情\n"]
        if is_historical:
            lines.append("*注：历史报告使用最新指数数据（腾讯接口不支持指数历史K线）*\n")
        if not data:
            lines.append("*暂无数据*\n")
            return "\n".join(lines)

        lines.append("| 指数 | 最新价 | 涨跌 | 涨跌幅 | 成交量 |")
        lines.append("|------|--------|------|--------|--------|")
        for item in data:
            name = item.get("index_name", item["name"])
            change_str = f"{item['change']:+.2f}"
            change_pct_str = f"{item['change_pct']:+.2f}%"
            volume_str = self.format_volume(item["volume"])
            lines.append(f"| {name} | {item['price']:.2f} | {change_str} | {change_pct_str} | {volume_str} |")

        up_count = sum(1 for i in data if i["change"] > 0)
        down_count = sum(1 for i in data if i["change"] < 0)
        flat_count = len(data) - up_count - down_count
        lines.append(f"\n**市场概况**：{up_count}个指数上涨，{down_count}个下跌，{flat_count}个平盘。")
        return "\n".join(lines) + "\n"

    def generate_watchlist_section(self, data: list) -> str:
        lines = ["## 📈 自选股行情\n"]
        if not data:
            lines.append("*暂无自选股配置，请在 config/watchlist.yaml 中添加*\n")
            return "\n".join(lines)

        lines.append("| 股票 | 代码 | 现价 | 涨跌 | 涨跌幅 | 成交量 | 标签 |")
        lines.append("|------|------|------|------|--------|--------|------|")
        for item in data:
            tags = ", ".join(item.get("tags", [])) if item.get("tags") else "-"
            change_str = f"{item['change']:+.2f}"
            change_pct_str = f"{item['change_pct']:+.2f}%"
            volume_str = self.format_volume(item["volume"])
            lines.append(f"| {item['name']} | {item['code']} | {item['price']:.2f} | {change_str} | {change_pct_str} | {volume_str} | {tags} |")

        movers = [i for i in data if abs(i["change_pct"]) >= 3]
        if movers:
            lines.append("\n**今日异动**：")
            for mover in movers:
                direction = "上涨" if mover["change_pct"] > 0 else "下跌"
                lines.append(f"- {mover['name']} ({mover['code']}) {direction} {abs(mover['change_pct']):.2f}%")
        return "\n".join(lines) + "\n"

    def generate_portfolio_section(self, data: list) -> str:
        lines = ["## 💼 持仓股监控\n"]
        if not data:
            lines.append("*暂无持仓配置，请在 config/portfolio.yaml 中添加*\n")
            return "\n".join(lines)

        lines.append("| 股票 | 代码 | 券商 | 现价 | 今日涨跌 | 持仓 | 成本价 | 市值 | 浮动盈亏 | 盈亏率 |")
        lines.append("|------|------|------|------|----------|------|--------|------|----------|--------|")

        total_market_value = 0
        total_cost_value = 0
        for item in data:
            total_market_value += item["market_value"]
            total_cost_value += item["cost_value"]
            change_str = f"{item['change']:+.2f} ({item['change_pct']:+.2f}%)"
            profit_str = f"{item['total_profit']:+.2f}"
            profit_pct_str = f"{item['profit_pct']:+.2f}%"
            broker = item.get("broker") or "-"
            lines.append(f"| {item['name']} | {item['code']} | {broker} | {item['price']:.2f} | {change_str} | {item['position']} | {item['cost_price']:.2f} | {item['market_value']:.2f} | {profit_str} | {profit_pct_str} |")

        total_profit = total_market_value - total_cost_value
        total_profit_pct = (total_profit / total_cost_value * 100) if total_cost_value > 0 else 0
        lines.append("\n**持仓汇总**：")
        lines.append(f"- 总市值：{total_market_value:,.2f} 元")
        lines.append(f"- 总成本：{total_cost_value:,.2f} 元")
        lines.append(f"- 浮动盈亏：{total_profit:+.2f} 元 ({total_profit_pct:+.2f}%)")

        risk_items = []
        for item in data:
            for flag in item.get("risk_flags", []):
                risk_items.append(f"- {item['name']} ({item['code']})：{flag}")
        if risk_items:
            lines.append("\n**持仓风险提示（事实阈值）**：")
            lines.extend(risk_items)
        return "\n".join(lines) + "\n"

    def generate_indicator_section(self, watchlist_data: list, portfolio_data: list) -> str:
        lines = ["## 📐 技术指标与量价状态\n"]
        combined = self._dedupe_stock_rows(portfolio_data + watchlist_data)
        if not combined:
            lines.append("*暂无股票数据*\n")
            return "\n".join(lines)

        lines.append("| 股票 | 代码 | K线样本 | MA | MACD | KDJ | RSI | BOLL | 成交量 | 量比 | 换手率 | 振幅 | ATR |")
        lines.append("|------|------|---------|----|------|-----|-----|------|--------|------|--------|------|-----|")

        for item in combined:
            indicators = item.get("indicators") or {}
            price = item.get("price")
            volume = item.get("volume", 0)
            if indicators and price:
                ma_text = IndicatorCalculator.describe_ma(indicators, price).replace("均线：", "")
                macd_text = IndicatorCalculator.describe_macd(indicators).replace("MACD：", "")
                kdj_text = IndicatorCalculator.describe_kdj(indicators).replace("KDJ：", "")
                rsi_text = IndicatorCalculator.describe_rsi(indicators).replace("RSI6：", "")
                boll_text = IndicatorCalculator.describe_boll(indicators, price).replace("BOLL：", "")
                volume_text = IndicatorCalculator.describe_volume(indicators, volume).replace("成交量：", "")
                volume_ratio_text = IndicatorCalculator.describe_volume_ratio(indicators).replace("量比：", "")
                turnover_text = IndicatorCalculator.describe_turnover(indicators).replace("换手率：", "")
                amplitude_text = IndicatorCalculator.describe_amplitude(indicators).replace("振幅：", "")
                atr_text = IndicatorCalculator.describe_atr(indicators, price).replace("ATR14：", "")
            else:
                ma_text = macd_text = kdj_text = rsi_text = boll_text = "-"
                volume_text = volume_ratio_text = turnover_text = amplitude_text = atr_text = "-"

            lines.append(
                f"| {item['name']} | {item['code']} | {item.get('klines_count', 0)} | "
                f"{ma_text} | {macd_text} | {kdj_text} | {rsi_text} | {boll_text} | "
                f"{volume_text} | {volume_ratio_text} | {turnover_text} | {amplitude_text} | {atr_text} |"
            )

        lines.append("\n**结构化状态说明**：")
        for item in combined:
            states = (item.get("indicators") or {}).get("states") or []
            if states:
                lines.append(f"- {item['name']} ({item['code']})：" + "；".join(states))
        return "\n".join(lines) + "\n"

    def generate_strategy_section(self, rules: list, triggered: list) -> str:
        lines = ["## 📚 经验策略规则\n"]
        if not rules:
            lines.append("*暂无策略规则配置，请在 config/strategy_rules.yaml 中添加*\n")
            return "\n".join(lines)

        status_counts = {}
        for rule in rules:
            status = rule.get("status", "active")
            status_counts[status] = status_counts.get(status, 0) + 1
        lines.append("**规则库状态**：" + "，".join(f"{status} {count}条" for status, count in sorted(status_counts.items())))
        lines.append("")

        active_rules = [rule for rule in rules if rule.get("status", "active") in ("active", "testing")]
        lines.append("| 状态 | 类别 | 规则 |")
        lines.append("|------|------|------|")
        for rule in active_rules[:12]:
            lines.append(f"| {rule.get('status', 'active')} | {rule.get('category', '-')} | {rule.get('text', rule.get('name', '-'))} |")

        if triggered:
            lines.append("\n**今日规则事实检查**：")
            lines.extend(triggered[:20])
        else:
            lines.append("\n**今日规则事实检查**：暂无可自动检查的规则触发。")
        return "\n".join(lines) + "\n"

    def generate_sector_section(self) -> str:
        lines = ["## 🔄 板块轮动\n"]
        lines.append("*板块数据暂未接入，V2版本将支持*\n")
        return "\n".join(lines) + "\n"

    def generate_summary_section(self, index_data: list, watchlist_data: list, portfolio_data: list) -> str:
        lines = ["## 📝 市场总结\n"]
        if not index_data:
            lines.append("*暂无数据*\n")
            return "\n".join(lines)

        summary_points = []
        for idx in index_data:
            name = idx.get("index_name", idx["name"])
            change_pct = idx["change_pct"]
            direction = "上涨" if change_pct > 0 else "下跌"
            summary_points.append(f"- {name}{direction} {abs(change_pct):.1f}%")

        large_cap_codes = ["000300", "000016"]
        small_cap_codes = ["000852", "399006"]
        large_cap_data = [i for i in index_data if i["code"] in large_cap_codes]
        small_cap_data = [i for i in index_data if i["code"] in small_cap_codes]
        if large_cap_data and small_cap_data:
            large_avg = sum(i["change_pct"] for i in large_cap_data) / len(large_cap_data)
            small_avg = sum(i["change_pct"] for i in small_cap_data) / len(small_cap_data)
            if large_avg > small_avg + 0.5:
                summary_points.append("- 权重股强于小盘股")
            elif small_avg > large_avg + 0.5:
                summary_points.append("- 小盘股强于权重股")
            else:
                summary_points.append("- 大小盘表现分化不明显")

        if watchlist_data:
            up_count = sum(1 for i in watchlist_data if i["change_pct"] > 0)
            down_count = sum(1 for i in watchlist_data if i["change_pct"] < 0)
            if up_count > down_count * 1.5:
                summary_points.append(f"- 个股涨多跌少（{up_count}涨{down_count}跌）")
            elif down_count > up_count * 1.5:
                summary_points.append(f"- 个股跌多涨少（{up_count}涨{down_count}跌）")
            else:
                summary_points.append(f"- 个股涨跌互现（{up_count}涨{down_count}跌）")

        if portfolio_data:
            total_profit_pct = sum(i["profit_pct"] for i in portfolio_data) / len(portfolio_data)
            if total_profit_pct > 0:
                summary_points.append(f"- 持仓整体浮盈 {total_profit_pct:.1f}%")
            else:
                summary_points.append(f"- 持仓整体浮亏 {abs(total_profit_pct):.1f}%")

        all_data = watchlist_data + portfolio_data
        if all_data:
            big_movers = [i for i in all_data if abs(i["change_pct"]) >= 5]
            if big_movers:
                up_movers = [i for i in big_movers if i["change_pct"] > 0]
                down_movers = [i for i in big_movers if i["change_pct"] < 0]
                if up_movers:
                    summary_points.append(f"- {len(up_movers)}只个股涨幅超5%")
                if down_movers:
                    summary_points.append(f"- {len(down_movers)}只个股跌幅超5%")

        lines.extend(summary_points)
        lines.append("")
        return "\n".join(lines)

    def render_report(
        self,
        report_type: str,
        date_display: str,
        generated_at: str,
        data_source_name: str,
        is_historical: bool,
        index_data: list,
        watchlist_data: list,
        portfolio_data: list,
        strategy_rules: list,
        triggered_rules: list,
    ) -> str:
        lines = [
            f"# A股每日行情报告 - {date_display}",
            "",
            f"**报告类型**：{'收盘简报' if report_type == 'close' else '复盘报告'}",
            f"**生成时间**：{generated_at}",
            f"**数据来源**：{data_source_name}{'（历史数据）' if is_historical else '（实时行情）'}",
            "",
            "---",
            "",
        ]
        lines.append(self.generate_summary_section(index_data, watchlist_data, portfolio_data))
        lines.append(self.generate_index_section(index_data, is_historical=is_historical))
        lines.append(self.generate_watchlist_section(watchlist_data))
        lines.append(self.generate_portfolio_section(portfolio_data))
        lines.append(self.generate_indicator_section(watchlist_data, portfolio_data))
        lines.append(self.generate_strategy_section(strategy_rules, triggered_rules))
        lines.append(self.generate_sector_section())
        lines.extend(
            [
                "---",
                "",
                "## ⚠️ 免责声明",
                "",
                "本报告仅基于公开行情数据生成，**不构成任何投资建议**。",
                "所有数据仅供参考，投资有风险，决策需谨慎。",
                "",
            ]
        )
        return "\n".join(lines)
