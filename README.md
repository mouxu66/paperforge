# PaperForge · 基于本地论文库的学术写作助手

PaperForge 是一个本地化的学术写作桌面工具，覆盖从文献检索到论文初稿导出的完整链路。它整合了论文管理、智能问答和写作工作台，所有引用都来自本地真实论文，绝不编造参考文献。

**核心承诺**：零幻觉 · 隐私优先 · 开箱即用

---

## ✨ 主要功能

- **论文库管理**：支持 arXiv 搜索/导入、PDF 拖拽上传（自动解析元数据）、Zotero 导入
- **混合检索**：FTS5 关键词 + 向量语义 + RRF 融合排序
- **智能问答**：基于 RAG 的问答系统，回答全部来自真实论文
- **写作工作台**：项目管理、大纲编辑、章节写作、AI 续写/改写/结构建议、引用推荐
- **多格式导出**：Markdown / Word / LaTeX，自动生成参考文献列表
- **被引情感分析**：基于引用上下文 + LLM，识别其他文献对目标论文的支持 / 批评 / 背景引用
- **关系图可视化**：中心论文 + 被引情感边 / 语义相似度 fallback，直观展示学术影响力网络
- **模型管理**：支持 OpenAI 兼容 API（本地 Ollama / 智谱 / DeepSeek 等），运行时切换
- **多语言**：中 / 英文界面切换
- **完全离线**：可选本地模型（llama.cpp / Ollama），数据不上传

---

## 🛠️ 技术架构

| 层级 | 技术栈 |
| :--- | :--- |
| 前端 | React 18 + TypeScript + Ant Design 5 + Zustand + Vite |
| 后端 | FastAPI + SQLAlchemy + SQLite |
| AI 网关 | OpenAI 兼容协议，支持多 Provider 运行时切换 |
| 本地模型 | llama.cpp / Ollama（推荐 Qwen3.5-9B） |
| 向量引擎 | Fastembed (ONNX Runtime) + BAAI/bge-small-en-v1.5 |

---

## 📊 项目数据

- 后端路由：≥135 条（`main.py` 仅 73 行装配入口，业务路由已拆为 16 个独立路由器，见 `mock_api/routers/`）
- 论文数据：410 篇（24 篇种子 + 386 篇从 arXiv 拉取，实际数量随使用增长）
- 单元测试：405 个（393 通过，12 跳过）
- 数据库表：16 张

---

## 🚀 快速启动

### 前置条件
- Python 3.10+
- Node.js 18+
- （可选）本地 Ollama 或兼容 OpenAI 协议的 LLM API Key

### 方式一：教师版（推荐）
1. 下载 `PaperForge_Teacher.exe`（见 Releases）
2. 双击运行，浏览器自动打开
3. 如需 AI 功能，在「模型管理」页面配置本地 Ollama 或云端 API
4. 数据持久化位置：`%APPDATA%/PaperForge/paperforge_mock.db`（教师版），
   `mock_api/paperforge_mock.db`（开发者模式）

### 方式二：一键启动（Windows）

Windows 用户可直接双击项目根目录的 `start_paperforge.bat`：

```bash
start_paperforge.bat
```

该脚本会自动探测端口、启动后端、按需构建前端并打开浏览器。

### 方式三：开发者模式
```bash
# 克隆项目
git clone https://github.com/你的用户名/paperforge.git
cd paperforge

# 安装后端依赖
pip install -r mock_api/requirements.txt

# 配置环境变量（可选，默认生产模式安全）
cp .env.example .env
# 开发模式（启用 admin 打包端点）：在 .env 中设 ENV=development

# 启动后端（推荐入口）
python -m mock_api.main
# 或：uvicorn mock_api.main:app --reload --port 8770

# 启动前端（另开终端）
cd web
npm install
npm run dev
```

访问 http://localhost:5173 即可使用。

### 构建与打包（教师版 .exe）

PaperForge 支持通过 PyInstaller 打包为单文件 `.exe` 分发：

```bash
# 1. 前置条件：ENV=development（admin 端点需显式开发态）
export ENV=development

# 2. 构建前端静态资源
cd web && npm install && npm run build && cd ..

# 3. PyInstaller 打包（本地）
pyinstaller paperforge.spec --noconfirm
# 或通过 Docker（更可重现）：
#   docker build -f Dockerfile.builder -t paperforge-builder .
#   docker run --rm -v "$(pwd):/app" paperforge-builder pyinstaller paperforge.spec --noconfirm

# 4. 产物在 dist/PaperForge_Teacher.exe
#    启动后数据库持久化到 %APPDATA%/PaperForge/paperforge_mock.db
```

也可通过 API 触发打包（需开发态）：`POST /api/admin/package`（需本机访问 + `ENV=development`）

### 导出格式

PaperForge 支持以下导出格式：
- **Markdown**：纯文本 Markdown，含引用列表
- **Word (.docx)**：python-docx 生成，保留格式
- **LaTeX**：生成 `.tex` 文件 + `.bib` 引用文件
- **PDF**：通过前端 `window.print()` 导出（浏览器原生，无需额外依赖）

## 📁 项目结构

```
paperforge/
├── mock_api/           # 后端核心
│   ├── llm/            # AI 网关（多 Provider）
│   ├── crud/           # 数据访问层（papers/notes/search 等）
│   ├── routers/        # 按业务拆分的路由（当前 papers.py）
│   ├── services/       # 业务服务层（PDF 代理等）
│   ├── admin/          # 管理功能（打包等）
│   ├── app.py          # FastAPI 应用工厂（中间件/生命周期）
│   ├── main.py         # 路由装配主入口（薄入口 ~73 行，路由见 routers/）
│   ├── models.py       # 数据库模型（16 张表）
│   └── settings.py     # 集中式配置（ADR-003）
├── web/                # 前端 React 应用
│   ├── src/
│   │   ├── api/        # API 客户端
│   │   ├── components/ # UI 组件
│   │   ├── pages/      # 页面
│   │   └── store/      # Zustand 状态
│   └── package.json
├── scripts/            # 工具脚本（测试、数据拉取）
├── tests/              # 单元测试（405 个）
├── .github/workflows/  # CI/CD 流水线
├── RUNBOOK.md          # 运维手册与排障指南
└── README.md
```

## 🤝 贡献与反馈

欢迎提交 Issue 和 Pull Request。如果你在使用中遇到问题，请在 Issue 中详细描述。

## 📄 许可证

MIT License
