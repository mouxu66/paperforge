# 仓库结构与导航（新人向）

> 目标：5 分钟内知道「我要改的功能在哪」「相关代码去哪找」。完整架构见 `docs/ARCHITECTURE.md`，运维见根目录 `RUNBOOK.md`。

## 1. 顶层布局

```
paperforge/
├── mock_api/            # 后端（FastAPI），所有服务端逻辑
├── web/                 # 前端（React + TS + Vite）
├── docs/                # 文档（本目录 + adr/ + guides/ + operations/）
├── scripts/             # 运维/数据脚本（benchmark、backfill、ingest）
├── tests/               # pytest 用例
├── deliverables/        # 交付物（含 engineering-assurance/）
├── README.md            # 项目介绍 + 技术架构
├── RUNBOOK.md           # 运维手册（启动/排障/回滚）★ 根目录
├── start_paperforge.bat # Windows 一键启动
└── pyproject.toml / pytest.ini / .pre-commit-config.yaml
```

## 2. 后端模块地图（mock_api/）

| 我想改/找 | 去哪 | 备注 |
|---|---|---|
| 路由（HTTP 入口） | `main.py`（82 条） / `routers/papers.py`（22 条） | `main.py` 是 god-file，新路由优先放 `routers/` |
| 论文库 / CRUD | `crud/papers.py` | god-file（52KB） |
| 搜索 / 混合检索 / RAG | `crud/search.py`、`semantic_search.py` | |
| 写作工作台 | `crud/writing.py` | 含学术诚信合规层 |
| 分析 / 推荐 / 引用关系 | `crud/analysis.py` | god-file |
| 本地模型（Qwen） | `llama_server_manager.py` | |
| OCR（隔离子进程） | `ocr_subprocess.py` | 见 ADR-007 |
| VRAM 仲裁 | `vram_scheduler.py` | 见 ADR-008 |
| 学术诚信合规 | `crud/writing.py` + `reflection_pipeline.py` | 见 ADR-009 |
| 任务 / 异步 / SSE | `tasks.py`、`workers/` | |
| DEPTH 审稿 | `depth_*.py`、`workers/reviews.py` | |
| 配置 | `config.py`、`settings.py` | pydantic-settings |
| 数据库 / 迁移 | `database.py`、`models.py`、`schemas.py` | |
| LLM Provider | `llm/`（factory + openai/zhipu/deepseek/llama_cpp） | |

子包速查：`crud/`（数据访问）、`routers/`（路由）、`services/`（如 `pdf_proxy_service.py`）、`workers/`（后台任务）、`llm/`（模型网关）、`utils/`（paths 等）、`admin/`（打包路由）、`diagnostics/`（数据快照）。

## 3. 前端模块地图（web/src/）

```
web/src/
├── api/          # 后端接口封装
├── pages/        # 页面（路由级）
├── components/   # 组件（home / writing / duplicate / motion …）
├── hooks/        # React hooks
├── store/        # Zustand 状态
├── layouts/      # 布局
├── locales/      # 中/英文案
├── types/        # TS 类型
├── utils/        # 工具
└── styles/       # 样式
```
找页面：先 `pages/`；找某功能的交互：对应 `components/<domain>/`；找数据获取：`api/` + `store/`。

## 4. 脚本与交付物
- `scripts/`：一次性/运维脚本（如 `backfill_chunks.py`、`serve_api.py`、`ai_benchmark*.py`）。`archive/` 为历史归档，勿改。
- `deliverables/engineering-assurance/`：团队交付物（评审、ADR 相关产出等）。
- `tests/`：按模块命名 `test_<module>.py`，运行 `pytest`。

## 5. 常见任务落点
- 加一个论文相关接口 → `routers/papers.py` 或 `main.py`，配套 `crud/` + `schemas.py`。
- 改写作/导出 → `crud/writing.py`（注意合规护栏）。
- 调模型行为 → `llm/factory.py`、`config.py`、`settings.py`。
- 排障 → 根 `RUNBOOK.md` + `logs/paperforge.log`。
