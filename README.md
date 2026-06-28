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

- 后端路由：72 条
- 论文数据：371 篇（24 种子 + 347 从 arXiv 拉取）
- 单元测试：141 个，全部通过
- 数据库表：14 张

---

## 🚀 快速启动

### 方式一：教师版（推荐）
1. 下载 `PaperForge_Teacher.exe`（见 Releases）
2. 双击运行，浏览器自动打开
3. 如需 AI 功能，在「模型管理」页面配置本地 Ollama 或云端 API

### 方式二：开发者模式
```bash
# 克隆项目
git clone https://github.com/你的用户名/paperforge.git
cd paperforge

# 安装后端依赖
pip install -r requirements.txt

# 启动后端
python -m mock_api.main

# 启动前端（另开终端）
cd web
npm install
npm run dev
```

访问 http://localhost:5173 即可使用。

## 📁 项目结构

```
paperforge/
├── mock_api/           # 后端核心
│   ├── llm/            # AI 网关（多 Provider）
│   ├── admin/          # 管理功能（打包等）
│   ├── main.py         # 路由主入口（72 条）
│   └── models.py       # 数据库模型
├── web/                # 前端 React 应用
│   ├── src/
│   │   ├── api/        # API 客户端
│   │   ├── components/ # UI 组件
│   │   ├── pages/      # 页面
│   │   └── store/      # Zustand 状态
│   └── package.json
├── scripts/            # 工具脚本（测试、数据拉取）
├── tests/              # 单元测试（141 个）
├── README.md
└── requirements.txt
```

## 🤝 贡献与反馈

欢迎提交 Issue 和 Pull Request。如果你在使用中遇到问题，请在 Issue 中详细描述。

## 📄 许可证

MIT License
