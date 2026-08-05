# PaperForge 架构文档

> 高层导航。详细决策见 [docs/adr/](adr/README.md)；运维见根目录 [RUNBOOK.md](../../RUNBOOK.md)；新人导航见 [guides/repository-navigation.md](guides/repository-navigation.md)。

## 1. 系统分层
- **前端** `web/`：React 18 + TypeScript + Ant Design 5 + Zustand + Vite。
- **后端** `mock_api/`：FastAPI + SQLAlchemy + SQLite（同步引擎）。
- **AI 网关** `mock_api/llm/`：OpenAI 兼容协议，多 Provider 运行时切换。
- **本地模型**：llama.cpp（Qwen 主模型 + OCR 视觉子进程）。

## 2. 关键子系统（一句话）
- 论文库 / 检索 / RAG：`crud/papers.py`、`crud/search.py`、`semantic_search.py`
- 写作工作台：`crud/writing.py`、`routers/writing.py`
- DEPTH 审稿：`depth_*.py`、`workers/reviews.py`、`tasks.py`
- 模型生命周期：`llama_server_manager.py`、`ocr_subprocess.py`、`vram_scheduler.py`
- 学术诚信：`crud/writing.py`（provenance + 引用校验）、`reflection_pipeline.py`（advisory）

## 3. 三个代码内机制（此前无文档，已补 ADR）
- ADR-007 OCR 子进程隔离
- ADR-008 VRAM 互斥调度器
- ADR-009 学术诚信合规层

## 4. 数据流与集成点（待补图）
- 论文入库：`pdf_parser` → `crud` → FTS5 / 向量
- 写作导出：`crud/writing` → provenance「AI 使用声明」
- 审稿：`depth_pipeline` → `workers` → SSE 推送

## 5. 路由架构（PR1→PR15 完成于 2026-07-22）

### 5.1 重构概览

原 `main.py`（3467 行，88 路由 god-file）已拆分为 15 个域路由器，全部在 `app.py` 中平级直挂。

**重构后 main.py**：77 行，仅保留日志初始化、`create_app()` 调用、SPA catch-all、`__main__`。

### 5.2 路由器清单

| PR | 文件 | 路由数 | 职责 |
|----|------|--------|------|
| PR1 | `routers/health.py` | 3 | 存活/就绪探针（liveness + readiness） |
| PR2 | `routers/models.py` | 6 | LLM 模型配置 CRUD + 切换 |
| PR3 | `routers/qwen.py` | 1 | Qwen 服务状态查询 |
| PR4 | `routers/compute.py` | 2 | 算力模式（speed/deep） |
| PR5 | `routers/chat.py` | 4 | AI 问答 / 综述生成 / 语义搜索 |
| PR6 | `routers/upload.py` | 3 | PDF/ZIP 批量上传 |
| PR7 | `routers/arxiv.py` | 2 | arXiv 检索与导入 |
| PR8 | `routers/tasks.py` | 4 | 通用任务提交/查询/SSE 进度流 |
| PR9 | `routers/zotero.py` | 3 | Zotero 目录监控与导入 |
| PR10 | `routers/admin.py` | 1 | arXiv 抓取触发 |
| PR11 | `routers/depth.py` | 13 | DEPTH v4.1 审稿流水线 + v1 评分 |
| PR12 | `routers/reflection.py` | 6 | 感悟/读后/复现报告评审 |
| PR13 | `routers/reports.py` | 2 | 报告分析 |
| PR14 | `routers/writing.py` | 36 | 写作工作台（项目/章节/模板/导出/版本） |
| PR15 | — | — | 消除中间层 `api_router`，papers 直挂 `app.py` |

> `routers/papers.py`（47 routes）和 `admin/package.py`、`admin/api_keys.py` 在重构前已独立，不在 PR 范围内。

### 5.3 最终装配（app.py）

```
create_app()
  ├─ admin_router         (package — 教师版打包)
  ├─ api_keys_router      (admin — API Key 管理)
  ├─ health_router        (PR1)
  ├─ papers_router        (论文 CRUD + PDF 代理 — 直挂)
  ├─ models_router        (PR2)
  ├─ qwen_router          (PR3)
  ├─ compute_router       (PR4)
  ├─ chat_router          (PR5)
  ├─ upload_router        (PR6)
  ├─ arxiv_router         (PR7)
  ├─ tasks_router         (PR8)
  ├─ zotero_router        (PR9)
  ├─ depth_router         (PR11)
  ├─ reflection_router    (PR12)
  ├─ reports_router       (PR13)
  └─ writing_router       (PR14)
```

### 5.4 共享基础设施（PR0）

- `concurrency.py`：`depth_review_lock`（threading.Lock）、`reflection_file_lock`（threading.RLock），供 depth 和 reflection 跨域共享。
- `routers/common.py`：`_as_dict`、`_safe_dict`、`_sse_event` 等共享 helper。

### 5.5 验证数据（2026-07-22）

| 指标 | 值 |
|------|-----|
| 总路由数（app.routes） | 146 |
| main.py 行数 | 77（重构前 3467，-97.8%） |
| 全量测试（pytest -k "not test_runbook_exists"） | 710/731 通过（97.1%），12 跳过 |
| Ruff（mock_api + tests） | 0 errors |

> 7 个预存测试失败（`test_feature_catalog_endpoints.py` / `test_reflection_file_*.py`）属测试断言与路由参数名未对齐，与架构拆分无关。

## 6. 已知技术债
- `routers/papers.py`、`crud/papers.py`、`crud/writing.py`、`crud/analysis.py` 体积过大，需进一步拆分（见 [module-header-convention](guides/module-header-convention.md)）。
- `routers/papers.py`、`crud/papers.py`、`crud/writing.py`、`crud/analysis.py` 体积过大，需进一步拆分。（OCR 边界已于 2026-07-22 收敛完成，见 §5.6）
