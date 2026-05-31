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
4. **板块轮动** - V1预留接口
5. **定时报告** - 收盘简报(15:30)、复盘报告(20:30)

## 使用方式

### 手动生成报告

```bash
python ~/.openclaw/skills/china-stock-daily-tracker/scripts/generate_report.py
```

### 定时运行（cron）

生成报告并发送到当前 chat session：

```bash
# 收盘简报 - 工作日 15:30
0 30 15 * * 1-5 openclaw cron run --job stock-report-close

# 复盘报告 - 工作日 20:30
0 30 20 * * 1-5 openclaw cron run --job stock-report-review
```

或者使用 cron 工具添加定时任务（推荐）：

```bash
# 添加收盘简报任务（15:30）
openclaw cron add \
  --name "stock-close-report" \
  --schedule "0 30 15 * * 1-5" \
  --command "generate_stock_report --type close --send-to-chat"

# 添加复盘报告任务（20:30）
openclaw cron add \
  --name "stock-review-report" \
  --schedule "0 30 20 * * 1-5" \
  --command "generate_stock_report --type review --send-to-chat"
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

## 报告输出

- 路径：`assets/reports/daily_report_YYYYMMDD.md`
- 格式：Markdown
- 内容：指数概览、自选股行情、持仓盈亏、板块数据状态

## 约束

- 仅使用Python标准库
- 非交易日跳过生成
- 所有判断基于数据事实
- 不输出任何买卖建议
