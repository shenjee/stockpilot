# Fundamental Screener Streamlit Frontend

基本面量化工作台，用来浏览 `packages/fundamentalscreener` core/repository 的输出。

## 角色边界

- 仅做可视化与导航。
- 不重复实现板块轮动 / 公司排名 / 财务评分 / 估值评分 / 异常 flags 检测。
- 不生成研报，不输出买卖建议，不预测板块。
- 不向用户暴露 fixture、SQLite、数据库路径或 CLI 参数。

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

## 数据获取机制

产品界面默认使用真实市场数据：

- 数据源：同花顺行业板块，当前通过 AkShare 封装读取。
- 备选源：东方财富行业板块，仅用于 fallback 或开发对照。
- 本地缓存：由应用内部管理，用于网络失败时保留最近可用结果。
- 用户动作：点击 `获取数据 / 运行分析` 后同步数据、质量检查并展示结果。

fixture 只用于测试和开发 smoke，不是产品数据源，不应出现在用户界面。

## 页面结构

- 顶部操作区：获取数据 / 运行分析、分析日期、数据状态。
- 顶部信息卡：日期 / 数据截止 / 分类口径 / 基准 / 板块数 / 质量状态。
- 板块归一化走势曲线（板块 + 基准）。
- 板块指标表（受侧边栏排序字段和 Top N 控制）。
- 板块下钻：
  - 公司排名表（按 `combined_score` 排序）。
  - 财务质量横向对比表。
  - 估值横向对比表。
- 公司 / 财务 / 估值的异常 flags + 估值 label 汇总。
- 数据质量 warnings 和板块详情 warnings 折叠显示。

## 开发计划

该小前端的产品功能和执行步骤见：

```text
docs/fundamental_screener_streamlit_frontend_plan.md
```

## 测试

```bash
source ~/.venvs/czsc/bin/activate
python -m unittest discover -s apps/fundamental-screener/tests -p 'test_*.py'
```

## 不做的事

- 不在 app 内复制排序、评分、异常检测算法。
- 不生成研报。
- 不输出买卖建议。
- 不把 fixture / SQLite 当作用户可选数据源。
