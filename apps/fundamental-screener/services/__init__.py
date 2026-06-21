"""Streamlit 调用 core 的薄封装层。

Phase 6 严格遵守 docs §18 的边界：
- 不在 services 内复制排序、评分、异常检测算法。
- 只把 ``packages.fundamentalscreener`` core 的结果转成视图友好的结构。
"""
