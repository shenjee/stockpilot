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
- 一次性补充尚未取得的指标或连板名单

## 架构

- 数据与计算：`packages/marketreview`
- 交易日历与指数日 K：`packages/marketdata`
- 本 Skill 负责理解请求，通过 API、网络查询、询问用户或其他可用方式取得数据，调用 package 写入并展示表格

默认数据库：`<workspace>/stockpilot/db/market_review.sqlite3`

## 使用方式

Agent 可以请求外部数据源，但必须调用 `packages/marketreview` 的稳定入口读写复盘数据，不经过独立 CLI，也不直接操作 SQLite。

写入操作：

1. 用 `resolve_review_trade_date(calendar, requested=...)` 解析可写入交易日。
2. 根据用户请求，通过 `packages/marketdata`、API、网络查询或询问用户取得数据。本次没有取得的内容可一次性列出，用户可以跳过。
3. 打开 `MarketReviewRepository(default_market_review_db_path())`，将本次已经取得或由用户提供的数据调用 `repository.patch_review(...)` 写入。
4. 未提供字段保留原值，具体值包括 `0` 会更新，显式 `null` 会清空。不要求所有指标齐全，不记录采集状态。

显示操作：

1. 用 `repository.get_review(trade_date)` 或 `list_reviews(...)` 读取数据。
2. 按 PRD 八类表格格式化展示。原子字段为 `null` 时留空；没有当日复盘记录时显示无数据。
3. 显示时不自动取数、不追问缺失数据、不判断完整性、不修改数据库。

展示层自行完成单位换算：元→亿元/万亿元，小数比率→百分数。package 只返回原始原子值和读时派生指标。

补数 YAML 示例见 PRD `docs/marketreview/daily_market_review_skill_prd.md` 第 5.2 节。

## 边界

- 写入和显示是两个独立操作；只有用户要求写入时才修改数据库
- V1 可自动获取三只指数日 K；`packages/marketreview` 不提供指数采集编排接口
- 涨跌停基础数量、连板名单、两融、成交额等其余字段可由 Skill 通过 API、网络查询或询问用户取得
- 连板名单含 `is_st=true` 时整次 patch 回滚
