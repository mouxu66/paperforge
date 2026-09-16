# PaperForge · 基于本地论文库的学术写作助手

PaperForge 是一个本地化的学术写作桌面工具，覆盖**从文献检索到初稿导出**的完整链路：把论文收进本地库，用混合检索和 RAG 问答读懂它们，在写作台里写章节并插入真实引用，最后导出带参考文献的 Word / LaTeX / Markdown。

**它和普通 AI 写作工具最大的区别**：所有引用只能来自你自己库里的论文，库里没有的文献一律不给编，导出时还会自动附上 AI 使用声明。

**核心承诺**：零幻觉 · 隐私优先 · 开箱即用

> **第一次用？** 先看 [核心功能实操教程](docs/教程-核心功能实操.md)，或者启动后在界面右上角点 **?** 打开帮助中心。
> 首次进入时还会自动弹一次 6 步新手引导，带你走完「导入 → 建立索引 → 检索 → 问答与审稿入口」，跳过之后可以随时重看。

---

## ✨ 功能总览

### 1. 建库：把论文收进来

| 能力 | 说明 |
| :--- | :--- |
| arXiv 搜索导入 | 按关键词/ID 批量拉取，自动补全标题、作者、摘要、年份 |
| PDF 拖拽上传 | 自动解析元数据并分块建索引，支持批量 |
| Zotero 导入 | 直接同步已有文献库 |
| 元数据补全 | 可接 Semantic Scholar 补全缺失字段 |
| 查重与去重 | 标题/DOI 相似度识别重复条目 |

### 2. 检索与阅读：三种方式找得到

- **混合检索**：SQLite FTS5 关键词 + 向量语义 + RRF 融合排序，关键词命中和语义相关都不漏
- **PDF 标注**：在内置阅读器里高亮、批注，标注可回链到笔记
- **论文笔记**：为每篇论文记笔记，写作时可检索引用

### 3. 问答：答案只来自你的库

基于 RAG 的问答，回答必须给出处。**问到库里没有的文献时，系统会明确说"库中没有"，而不是编一段像模像样的参考文献。**

### 4. 深度评审（DEPTH v4.2）

这是 PaperForge 最有分量的功能。不是让模型"打个分"，而是把它拆成一条多节点流水线：

```
Q0 ∥ Q1 → QE → Q234 ∥ QF → Q5a → Q5b → Q5c
```

- **Q0/Q1 并行**：方法与实验两条线各自独立评估
- **QE 证据聚合**：把分散在各节点的证据收拢
- **Q234/QF 并行**：创新性、严谨性、清晰度与"致命缺陷"检查同时跑
- **Q5a/b/c 收敛**：交叉校验、稳分（弹性权重 + 自适应 delta ±0.25）、输出终评

输出是**分项分数 + 每一分对应的依据**，不是一句"这篇不错"。评分经过金标集校准（415 篇抽样盲评，Cohen's κ 从 0.189 提到 0.375），详见 `docs/adr/014-eval-rigorization.md`。

### 5. 写作工作台

- 项目管理 → 大纲编辑 → 逐章写作
- AI 续写 / 改写 / 结构建议
- **引用推荐与插入**：根据当前段落语义从库中推荐可引文献，一键插入
- 章节版本快照，可回溯

### 6. 导出

| 格式 | 说明 |
| :--- | :--- |
| Markdown | 纯文本，含引用列表 |
| Word (.docx) | python-docx 生成，保留格式 |
| LaTeX | `.tex` + `.bib` 引用文件 |
| PDF | 浏览器原生 `window.print()`，无需额外依赖 |

导出时自动：生成参考文献列表、**注入「AI 使用声明」章节**、校验引用是否真实存在于库中。

### 7. 学术关系分析

- **被引情感分析**：读引用上下文，判断其他文献对目标论文是支持 / 批评 / 背景引用
- **关系图可视化**：中心论文 + 情感边 + 语义相似度 fallback，看清一篇论文在领域里的位置

### 8. 模型与部署

- 接入任何 **OpenAI 兼容 API**（本地 llama.cpp / Ollama、智谱、DeepSeek 等），运行时可切换
- 推荐本地模型 **Ornith-1.5-9B**（Q4_K_M，llama.cpp 推理服务）
- 可**完全离线**运行，论文与写作数据不离开本机
- 中 / 英文界面切换

---

## 🛠️ 技术架构

| 层级 | 技术栈 |
| :--- | :--- |
| 前端 | React 19 + TypeScript + Ant Design 6 + Zustand + Vite |
| 后端 | FastAPI + SQLAlchemy + SQLite |
| AI 网关 | OpenAI 兼容协议，多 Provider 运行时切换 |
| 本地推理 | llama.cpp（Ornith-1.5-9B Q4_K_M） |
| 向量引擎 | Fastembed (ONNX Runtime) + BAAI/bge-small-en-v1.5（384 维） |
| 视觉理解 | 云端 GLM-4V-Flash（图表提取 / 图文一致性） |

---

## 📊 项目规模（实测）

- 论文：**950 篇**，其中 386 篇已建向量索引
- DEPTH v4 评审记录：**854 条**
- 数据库表：**32 张**
- 后端：19 个路由模块，`main.py` 仅 73 行装配入口
- 前端：19 个页面 / 104 个组件
- 测试：**1720 个**（pytest 收集数）

---

## 🚀 快速启动

### 前置条件
- Python 3.10+
- Node.js 18+
- （可选）本地 llama.cpp / Ollama，或任意 OpenAI 兼容 API

### 方式一：桌面版 .exe（最省事）

1. 从 Releases 下载打包好的 `.exe`
2. 双击运行，浏览器自动打开
3. 在「模型管理」里配置本地或云端模型即可用 AI 功能
4. 数据保存在 `%APPDATA%/PaperForge/paperforge_mock.db`

### 方式二：一键启动（Windows）

双击项目根目录的 `start_paperforge.bat`，脚本会自动探测端口、启动后端、按需构建前端并打开浏览器。

```bash
start_paperforge.bat
```

### 方式三：开发者模式

```bash
git clone https://github.com/mouxu66/paperforge.git
cd paperforge

# 后端
pip install -r mock_api/requirements.txt
cp .env.example .env          # 可选，默认即为安全的生产模式
python -m mock_api.main       # 监听 8770

# 前端（另开终端）
cd web && npm install && npm run dev
```

访问 http://localhost:5173 使用；只用后端时访问 http://127.0.0.1:8770 也能加载已构建的前端。

### 方式四：Docker Compose（带本地推理服务）

```bash
# 1) 把模型放进 ./models/
# 2) 启动推理服务
docker compose up -d
# 3) 启动 PaperForge
python -m mock_api.main
```

---

## 📦 打包分发

用 PyInstaller 打成单文件 `.exe`：

```bash
export ENV=development                                  # admin 端点需显式开发态
cd web && npm install && npm run build && cd ..          # 先构建前端静态资源
pyinstaller paperforge.spec --noconfirm                  # 产物在 dist/
```

也可走 Docker（更可重现）：

```bash
docker build -f Dockerfile.builder -t paperforge-builder .
docker run --rm -v "$(pwd):/app" paperforge-builder pyinstaller paperforge.spec --noconfirm
```

或调用 API 触发（需开发态 + 本机访问）：`POST /api/admin/package`

---

## 🖼️ 图表提取

DEPTH v4.2 支持从 PDF 提取图表并做图文一致性分析，详见 [docs/figure_extraction.md](docs/figure_extraction.md)。

| 方案 | 特点 | 推荐场景 |
| :--- | :--- | :--- |
| 内置 Vector Extractor | 零配置，PDF 矢量图直接渲染 | 快速上手 |
| 云端视觉模型（GLM-4V-Flash） | 多模态理解，OCR + 语义摘要 | 需要深层理解 |
| PicAxe | 轻量 Python 库 | 批量处理 |

---

## 📁 项目结构

```
paperforge/
├── mock_api/           # 后端核心
│   ├── llm/            # AI 网关（多 Provider）
│   ├── crud/           # 数据访问层
│   ├── routers/        # 19 个按业务拆分的路由模块
│   ├── services/       # 业务服务层
│   ├── admin/          # 打包等管理功能
│   ├── app.py          # FastAPI 应用工厂
│   ├── main.py         # 路由装配入口（~73 行）
│   ├── models.py       # 数据库模型（32 张表）
│   └── settings.py     # 集中式配置
├── web/                # 前端 React 应用
│   └── src/            # api / components / pages / store
├── scripts/            # 工具脚本（数据拉取、校准、基准）
├── tests/              # 单元测试
├── docs/adr/           # 架构决策记录（含评分严谨化 ADR-014）
├── .github/workflows/  # CI：ruff 硬门禁 + pytest 覆盖率
├── RUNBOOK.md          # 运维手册与排障指南
└── README.md
```

---

## 🤝 贡献与反馈

欢迎提交 Issue 和 Pull Request。

## 📄 许可证

MIT License
