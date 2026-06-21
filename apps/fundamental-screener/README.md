# Fundamental Screener Streamlit MVP

Phase 6 数据工作台，用来浏览 `packages/fundamentalscreener` core 的输出。

## 角色边界

- 仅做可视化与导航。
- 不重复实现板块轮动 / 公司排名 / 财务评分 / 估值评分 / 异常 flags 检测。
- 不生成研报，不输出买卖建议，不预测板块。

所有计算都来自 `packages/fundamentalscreener`，本 app 通过 `services/data_service.py`
调用 core 函数，按表格 / 折线图渲染。

## 启动

```bash
source ~/.venvs/czsc/bin/activate
streamlit run apps/fundamental-screener/app.py
```

依赖：

- `streamlit`
- `pandas`（仅折线图用）
- `packages/fundamentalscreener`

如缺失：

```bash
python -m pip install streamlit pandas
```

## 默认数据源

侧边栏 `Fixture JSON 路径` 默认指向：

```
packages/fundamentalscreener/tests/fixtures/minimal_market.json
```

该 fixture 与 CLI `--fixture` 参数共用同一份骨架数据，因此 Streamlit 与
`python -m packages.fundamentalscreener.cli ...` 看到的是完全一致的数值。

## 页面结构

- 顶部信息卡：日期 / 分类口径 / 基准 / 板块数。
- 板块归一化走势曲线（板块 + 基准）。
- 板块指标表（受侧边栏排序字段和 Top N 控制）。
- 板块下钻：
  - 公司排名表（按 `combined_score` 排序）。
  - 财务质量横向对比表。
  - 估值横向对比表。
  - 公司 / 财务 / 估值的异常 flags + 估值 label 汇总。
- 板块层 warnings 和板块详情 warnings 折叠显示。

## 测试

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
```

## 不做的事

- 不在 app 内复制排序、评分、异常检测算法。
- 不生成研报。
- 不输出买卖建议。
