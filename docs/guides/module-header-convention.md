# 模块头部注释约定

为降低 god-file 带来的导航成本，统一模块头部职责说明。

## 规则
1. **适用对象**：`mock_api/routers/` 下每个模块；以及超过 ~500 行的核心模块（`crud/`、`main.py`、`depth_*.py` 等）。
2. **格式**：文件顶部 docstring 必须 ≥ 2 行。
   - **第 1 行**：一句话职责（面向谁、做什么）。
   - **第 2 行**：模块边界 / 依赖 / 拆分批次；若为 god-file，明确标注「待拆分」与建议拆法。
3. 标题用中文，避免空话；不写实现细节，只写「为什么在这、装了什么」。

## 范例（符合：`mock_api/routers/papers.py`）
```python
"""论文相关路由：论文 CRUD / 笔记 / 引用 / 排名 / 分析 / 收藏 / 统计 / 搜索建议 / PDF 批注 / PDF 代理。

批次 1 拆分：从 main.py 搬迁 papers 簇路由（仅依赖 crud + get_db + schemas）。"""
```

## 需要补头部注释的模块
| 模块 | 现状 | 需补 |
|---|---|---|
| `mock_api/main.py` | 有概览头部，但 82 条路由职责未列 | 补「仍驻留路由簇」清单 + `待拆分` 标记 |
| `mock_api/crud/papers.py` | 仅 1 行 | 补职责 + 标注 god-file/待拆分建议 |
| `mock_api/crud/writing.py` | 仅 1 行 | 补职责（含合规层 provenance/引用校验）+ 待拆分 |
| `mock_api/crud/analysis.py` | 仅 1 行 | 补职责 + 待拆分 |
| `mock_api/routers/papers.py` | 已符合（范例） | 维持 |
| `mock_api/depth_*.py` 等超长模块 | 多数缺 2 行头部 | 按规则补 |

## 校验
- 新增 `routers/` 模块必须带 2 行头部，否则 PR 不通过。
- `tests/test_docs_consistency.py` 可扩展：扫描 `routers/` 与 >500 行模块，断言头部存在。
