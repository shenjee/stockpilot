---
name: china-stock-daily-tracker
description: "中国A股每日行情追踪 - 生成事实型行情报告，覆盖指数、自选股、持仓股。"
metadata:
  author: china-stock-daily-tracker
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

运行脚本位于本 skill 目录的 `scripts/generate_report.py`。

私有数据目录不是 skill 安装目录。默认使用当前 agent/project 的工作目录。配置、SQLite 数据库和报告会写入该 workspace 下的 `config/`、`db/`、`reports/`。

```bash
cd <workspace-dir>
python3 <skill-dir>/scripts/generate_report.py --type close
```

特殊部署时可用 `CHINA_STOCK_DAILY_TRACKER_WORKSPACE` 或本地 `china-stock-daily-tracker.local.json` 覆盖 workspace。

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
    market: "sh"
    name: "北方稀土"
    position: 1000        # 持仓数量
    cost_price: 25.50     # 成本价
    broker: "示例券商"     # 券商（可选）
    target_weight: 0.15   # 目标仓位占比(可选)
    buy_orders:           # 建仓/加仓记录（可选）
      - trade_date: "2026-06-01"
        trade_time: "09:35:00"
        buy_price: 25.50
        quantity: 1000
        amount: 25500.00
  - code: "000001"
    market: "sz"
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

- 路径：`<workspace>/reports/daily_report_YYYYMMDD.md`
- 格式：Markdown
- 内容：指数概览、自选股行情、持仓盈亏、技术指标状态、经验规则、板块数据状态

## 约束

- 仅使用Python标准库
- 非交易日跳过生成
- 所有判断基于数据事实
- 不输出任何买卖建议
