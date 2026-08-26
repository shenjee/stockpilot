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
- 一次性补充尚未取得的指标或涨跌停事件

## 架构

- 数据持久化与查询：`packages/marketreview`
- 交易日历与指数日 K：`packages/marketdata`
- 本 Skill 负责理解请求，通过 API、网络查询、询问用户或其他可用方式取得数据，完成业务校验后调用 package 写入；用户要求查看时读取、统计并展示表格

默认数据库：`<workspace>/stockpilot/db/market_review.sqlite3`

## 使用方式

Agent 可以请求外部数据源，但必须调用 `packages/marketreview` 的稳定入口读写复盘数据，不经过独立 CLI，也不直接操作 SQLite。

写入操作：

1. 使用 `packages/marketdata` 交易日历解析业务日期，确认 Skill 要生成的日期是已收盘交易日。
2. 根据用户请求，通过 `packages/marketdata`、API、网络查询或询问用户取得数据。本次没有取得的内容可一次性列出，用户可以跳过。
3. 在 Skill 层检查市场、代码、方向、收盘状态、涨跌幅限制、证券宇宙和输入类型；检查通过后，打开 `MarketReviewRepository(default_market_review_db_path())`，将数据写入。
4. 复盘原子字段未提供时保留原值，具体值包括 `0` 会更新，显式 `null` 会清空。
5. 涨跌停事件使用简单的保存、更新或删除接口；每只股票的每个触板方向是一条独立事件行，不保存或推断名单完整性。初始化或数据断档后，可提交 Skill 取得的当天实际连板高度锚点。

repository 和 SQLite 不重复执行上述业务校验，不查询行情判断事件是否真实，不检查多个高度锚点是否一致，也不沿历史数据阻止写入。数据库只负责字段持久化、唯一行身份和基本事务安全。用户查看后认为数据有误时，可要求 Skill 修订或覆盖。

显示操作：

1. 用 repository 读取总体复盘原子字段和涨跌停事件。
2. 根据当前已保存数据统计有效涨停、涨停炸板、打开跌停、收盘跌停、首板、连板和其他展示指标，再按 PRD 八类表格格式化。原子字段为 `null` 时留空；只有总体复盘或涨跌停事件任一存在，就展示当日组合视图，两者都不存在时才显示无数据。
3. 显示时不自动取数、不追问缺失数据、不判断完整性、不修改数据库。

Skill 自行完成统计和单位换算：元→亿元/万亿元，小数比率→百分数。package 返回原始原子值和每日涨跌停事件。

涨跌停事件及高度锚点 YAML 示例见 PRD `docs/marketreview/daily_market_review_skill_prd.md` 第 5.2 节。

## 边界

- 写入和显示是两个独立操作；只有用户要求写入时才修改数据库
- V1 可自动获取三只指数日 K；`packages/marketreview` 不提供指数采集编排接口
- 涨跌停事件、两融、成交额等其余数据可由 Skill 通过 API、网络查询或询问用户取得
- 有效涨停、涨停炸板、打开跌停、收盘跌停、首板和连板均由 Skill 根据当前已有事件统计，不保存重复聚合值
- `streak_height_anchor` 表示 Skill 取得的有效涨停股票在记录当天的实际连板高度；数据库保存该值但不检查历史一致性
- Skill 负责写入前的业务校验；repository 不重复验证数据内容是否符合真实市场
