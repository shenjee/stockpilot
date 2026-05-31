---
name: china-stock-daily-tracker
description: "中国A股每日行情追踪 - 生成事实型行情报告，覆盖指数、自选股、持仓股。"
metadata:
  author: StockPilot
  version: 1.0.0
  category: finance
  tags: [a-share, stock, daily-report, market-data]
  requires: []
---

# 中国A股每日行情追踪

生成事实型A股行情日报，覆盖主要指数、自选股、持仓股。不做买卖建议，只描述数据事实。

## 数据来源

- 腾讯财经公开API（零依赖，标准库实现）
- 本地配置：`config/watchlist.yaml`、`config/portfolio.yaml`

## 功能

1. **主要指数行情** - 上证指数、深证成指、创业板指等9大指数
2. **自选股追踪** - 从watchlist.yaml读取，输出价格、涨跌幅、成交量
3. **持仓股监控** - 从portfolio.yaml读取，输出盈亏、仓位变化
4. **本地K线库** - 使用 SQLite 缓存自选股/持仓股历史日线
5. **技术指标状态** - MA、MACD、KDJ、RSI、BOLL、成交量、换手率、振幅、ATR
6. **经验策略规则** - 从 strategy_rules.yaml 读取并展示规则状态，做事实型触发检查
7. **板块轮动** - V1预留接口
8. **定时报告** - 收盘简报(15:30)、复盘报告(20:30)

## 使用方式

### 手动生成报告

```bash
python3 scripts/generate_report.py \
  --workspace "$HOME/Documents/Stock Pilot"
```

也可以用环境变量指定私有工作区：

```bash
export STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot"
python3 scripts/generate_report.py
```

### 定时运行（cron）

生成报告：

```bash
# 收盘简报 - 工作日 15:30
30 15 * * 1-5 cd ~/development/china-stock-daily-tracker && STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot" python3 scripts/generate_report.py --type close

# 复盘报告 - 工作日 20:30
30 20 * * 1-5 cd ~/development/china-stock-daily-tracker && STOCKPILOT_WORKSPACE="$HOME/Documents/Stock Pilot" python3 scripts/generate_report.py --type review
```

## 配置文件

### config/watchlist.yaml - 自选股列表

```yaml
watchlist:
  - code: "600519"
    name: "贵州茅台"
    tags: ["白酒", "核心资产"]
  - code: "300750"
    name: "宁德时代"
    tags: ["新能源", "创业板"]
```

### config/portfolio.yaml - 持仓记录

```yaml
portfolio:
  - code: "600111"
    name: "北方稀土"
    position: 1000        # 持仓数量
    cost_price: 25.50     # 成本价
    target_weight: 0.15   # 目标仓位占比(可选)
  - code: "000001"
    name: "平安银行"
    position: 2000
    cost_price: 12.30
```

### config/strategy_rules.yaml - 经验策略规则

```yaml
strategies:
  - name: "20日均线趋势过滤"
    category: "均线"
    status: "active"
    check: "below_ma20"
    text: "不上20日均线，不确认趋势条件"
```

## 报告输出

- 路径：`assets/reports/daily_report_YYYYMMDD.md`
- 格式：Markdown
- 内容：指数概览、自选股行情、持仓盈亏、技术指标状态、经验规则、板块数据状态

## 约束

- 仅使用Python标准库
- 非交易日跳过生成
- 所有判断基于数据事实
- 不输出任何买卖建议
