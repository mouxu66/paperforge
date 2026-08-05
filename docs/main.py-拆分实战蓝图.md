# PaperForge 后端模块地图

> 状态：**已拆分完成**（2026-07-14）。原 `mock_api/main.py` 中的路由已按业务域迁移到 `mock_api/routers/`，本文件不再是一份待执行的“蓝图”，而是当前后端结构的权威说明与模块地图。

---

## 一、当前结构总览

```
mock_api/
├── app.py              # FastAPI 应用工厂：中间件、生命周期、异常处理、路由装配
├── main.py             # 路由装配层：定义 api_router，include routers/papers.py，保留 SPA catch-all
├── routers/
│   ├── __init__.py
│   └── papers.py       # 论文簇路由（CRUD / 笔记 / 引用 / 排名 / 分析 / 收藏 / 统计 / 搜索 / PDF 批注 / PDF 代理）
├── crud/               # 数据访问层
│   ├── __init__.py
│   ├── papers.py       # 论文、收藏、导入、富化
│   ├── notes.py       # 笔记
│   ├── search.py      # 检索
│   ├── embeddings.py  # 向量
│   └── ...
├── services/           # 业务服务层
│   ├── __init__.py
│   └── pdf_proxy_service.py
├── llm/                # AI 网关（Provider 抽象 + 工厂）
├── admin/              # 管理功能（打包）
├── models.py           # SQLAlchemy 2.0 ORM 模型（16 张表）
├── schemas.py          # Pydantic 请求/响应模型
├── settings.py         # 集中式配置（ADR-003）
├── database.py         # SQLite 连接、Session、FTS5、迁移辅助
├── tasks.py            # TaskManager（后台任务 + SSE）
├── scheduler.py        # arXiv 定时拉取调度器
└── ...
```

---

## 二、路由事实源

当前后端共有 **104 条路由**：

- `mock_api/main.py` 内联定义：**82 条**
- `mock_api/routers/papers.py`：**22 条**

`mock_api/app.py` 的 `create_app()` 统一装配：

```python
from .main import api_router
app.include_router(api_router)
```

`mock_api/main.py` 末尾：

```python
from .routers import papers as _papers_router
api_router.include_router(_papers_router.router)
```

> ⚠️ 历史说明：2026-07-14 之前 `routers/` 目录下存在 12 个未激活的路由文件（ai/arxiv/compute/depth/health/helpers/models/reports/tasks/upload/writing/zotero），与 `main.py` 内联路由重复且部分导入即报错。这些文件已在 2026-07-14 整树删除，仅保留 `papers.py` 作为已验证的拆分模板。

---

## 三、论文簇路由（`routers/papers.py`）

已迁移并稳定运行的路由：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/papers/{paper_id}/pdf-proxy` | PDF 代理（绕过 CORS） |
| GET | `/api/papers` | 分页列表 |
| GET | `/api/papers/{paper_id}` | 详情 |
| GET | `/api/search/suggest` | FTS5 搜索建议 |
| GET | `/api/favorites` | 收藏列表 |
| POST | `/api/favorites` | 添加收藏 |
| DELETE | `/api/favorites/{paper_id}` | 取消收藏 |
| GET | `/api/stats` | 库统计 |
| GET/POST/PUT/DELETE | `/api/papers/{paper_id}/notes` 等 | 论文笔记 CRUD |
| GET | `/api/projects/{project_id}/notes` | 项目笔记 |
| GET | `/api/papers/{paper_id}/citations` | 引用关系 |
| POST | `/api/papers/rank` | 综合评分排序 |
| POST | `/api/papers/analysis` | 综合分析报告 |
| POST | `/api/papers/analyze` | 指定论文对比 |
| POST | `/api/papers/batch-delete` | 批量删除 |
| GET/POST/PATCH/DELETE | `/api/papers/{paper_id}/pdf-annotations` 等 | PDF 批注 CRUD |

---

## 四、剩余内联路由（`main.py`）

以下路由仍内联在 `main.py`，未来可按 `routers/papers.py` 的模式继续拆分：

- **健康检查**：`/api/health*`
- **DEPTH**：评分、批量、v4.1 审稿、reflection、统一列表
- **LLM 模型管理**：`/api/models*`, `/api/model/current`, `/api/model/switch`
- **AI 能力**：`/api/ask`, `/api/ask/stream`, `/api/generate`, `/api/search/semantic`
- **PDF 上传**：`/api/upload-paper`, `/api/upload-batch`, `/api/upload-zip`
- **arXiv**：`/api/arxiv/search`, `/api/arxiv/import`
- **任务系统**：`/api/tasks*`
- **Zotero**：`/api/zotero/*`
- **算力模式**：`/api/compute/modes`, `/api/compute/mode`
- **写作工作台**：`/api/writing/*`
- **报告导出**：`/api/reports/*`

---

## 五、拆分规范（供后续迁移参考）

1. **单一职责**：新 router 文件 ≤ 400 行；函数 ≤ 50 行。
2. **依赖方向**：router 只依赖下层模块（`database` / `crud` / `schemas` / `models` / `services` / `llm`），**绝不 `from ..main import ...`**。
3. **数据库会话**：统一 `from ..database import get_db`，在 handler 签名中使用 `Depends(get_db)`。
4. **测试**：每批迁移后补 contract 测试（200/404/403/422 路径）。
5. **不破坏路径**：前端路径保持不变；如需版本化，统一加 `/api/v1` 前缀（另见 ADR）。

---

## 六、验收基线

- `pytest -q` 全绿（当前基线：365 个测试）。
- `ruff check mock_api tests` 无新增报错。
- `mypy mock_api` 无新增严重错误（当前 mypy 为 continue-on-error 门禁）。

---

> 本文件替代了原 `main.py-拆分实战蓝图.md` 中“未改代码 / 3728 行 / 行号映射”等失效内容。后续如需继续拆分剩余内联路由，请按本模块地图的规范执行。
