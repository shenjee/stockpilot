---
name: daily-market-review
description: "每日资本市场总体复盘。用于生成、补充和查看指定交易日的涨跌停、市场宽度、连板、两融、指数、成交额、市值与估值数据。"
metadata:
  author: stock-pilot
  version: 0.1.0
  category: finance
  tags:
    - a-share
    - market-review
    - daily-review
    - limit-up
    - streak
    - 市场复盘
    - 总体复盘
  requires: []
---

# 每日市场复盘

整理、补充和展示指定已收盘交易日的资本市场总体复盘数据。不做买卖建议。

## 适用场景

- 生成今天的总体复盘
- 生成 2026-08-21 的总体复盘
- 查看已保存的历史总体复盘
- 一次性补充缺失的手工指标或连板名单

## 架构

- 数据与计算：`packages/marketreview`
- 交易日历与指数日 K：`packages/marketdata`
- 本 Skill 只负责理解请求、收集补数、调用 package、展示表格

默认数据库：`<workspace>/stockpilot/db/market_review.sqlite3`

## 使用方式

Agent 调用 `packages/marketreview` 的稳定入口，不经过独立 CLI，也不直接请求外部数据源。

典型流程：

1. 用 `resolve_review_trade_date(calendar, requested=...)` 解析可写入交易日
2. 打开 `MarketReviewRepository(default_market_review_db_path())`
3. 用 `auto_patch_indices(repository, marketdata_provider, calendar, trade_date)` 自动拉取三只指数；部分失败时保留已有值
4. 用 `missing_atomic_fields(repository, trade_date)` 一次性列出仍需手工补充的指标
5. 收集 YAML/表格补数后，调用 `repository.patch_review(...)` 写入
6. 用 `repository.get_review(trade_date)` 或 `list_reviews(...)` 读取，并按 PRD 八类表格展示

展示层自行完成单位换算：元→亿元/万亿元，小数比率→百分数。package 只返回原始原子值和读时派生指标。

补数 YAML 示例见 PRD `docs/marketreview/daily_market_review_skill_prd.md` 第 5.2 节。

## 边界

- V1 自动获取仅三只指数日 K
- 涨跌停基础数量、连板名单、两融、成交额等其余字段为手工录入
- 连板名单含 `is_st=true` 时整次 patch 回滚
