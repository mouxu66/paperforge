# PaperForge 工作纪律（本仓库所有 agent 必读）

> **⚠️ 这是仓库根目录的必读文件。每次执行任务前，先读本文件；执行多步任务时对照第 2 节纪律自查。**
> 完整方法论与运行 DeepSeek Harness 的配方见技能：`.agents/skills/deepseek-harness/SKILL.md`（来源：deepseek-ai/deepseek-harness，MIT）。

---

## 1. 本仓库速查

- 技术栈：FastAPI + SQLAlchemy + SQLite 后端（`mock_api/`）、React 18 + Vite 前端（`web/`）、测试 `tests/`（pytest，约 405 个）
- 测试：`pytest`（必要时 `pytest tests/<目标>`）
- 后端启动：`python -m mock_api.main`（或 `uvicorn mock_api.main:app --port 8770`）
- 前端启动：`cd web && npm run dev`
- 文档：`README.md`（总览）、`RUNBOOK.md`（运维排障）；配置见 `.env` / `.env.example`

## 2. 核心纪律（源自 DeepSeek Harness，必须遵守）

1. **[P1] 先计划后动手**：多步任务先写下计划（步骤 / 负责人 / 验收证据），随进度更新；计划要能从会话记录重建，不是用完即弃的草稿。
2. **[P2] 验证行为而非表象**：逻辑改动跑测试并断言状态迁移；模型/用户可见的输出跑真实验证（真实调用、快照）。工具的输出呈现意图（普通文本 / 终端友好 / diff 审查）在开工前定好，呈现是参数的纯函数。
3. **[P3] 边界显式化**：模块/服务边界用显式 `resolve(request) -> spec`，不用藏在实现里的 `?? 默认值`；一个能力 = 定义 / 提供 / 消费三件套，别只做一角。
4. **[P4] 配置显式、失败响亮**：部署相关的可调项是校验过的配置字段，不是硬编码常量；协议常量与安全不变量保持固定。缺配置在加载期（或最早可解析处）就报错，**绝不静默跳过**缺失项。
5. **[P5] 模型可见 ⟺ 有日志**：任何到达模型或用户的内容，必须能从日志/会话记录重建；新增模型可见输入，就要有对应的日志事件。
6. **[P6] 注册即副作用**：监听器/提供者/清理器走 effect 式注册并返回 disposer，防泄漏靠构造不靠约定；瀑布式监听必须调用 `next()` 放行，返回不等于放行。
7. **[P7] 委派 + 评审**：独立的子任务委派给子 agent / 第二助手，给出精确简报与验收标准，然后按标准评审其产出——而不是自己重做一遍。
8. **[P8] 上下文预算**：蒸馏而非倾倒；一份能自包含并链接到深文的规则，胜过长篇内联论述；长会话及时压缩，不背历史包袱。

## 3. 第二助手（可选，需要 key）

官方 `dsh` CLI 有**四种入口模式**（headless 一次性任务 / web UI / 自定义 profile / plugin 插件管理），配好 `DEEPSEEK_API_KEY` 后即可当第二编码助手；主力是 `pnpm dsh --profile headless "任务"`（跑完打印最终答案退出）。此外还有**四种运行模式**（Agent 预设，Web UI 中选择）：标准 / PTC（程序化工具调用）/ 极简 / 创造。入口模式与运行模式的命令、用途及克隆/构建步骤详见 skill 第 4 节。没有 key 时 harness 无法运行——dsh 没有"无模型"模式；本文件第 2 节是模型无关的部分，无 key 也必须遵守。配套工具 `scripts/dsh_delegate.py` 可自动化委派+评审：`--accept` 逐条跑验收命令、`--no-delegate` 跳过委派只评审已有产出（无需 key），完整用法见独立 skill `.agents/skills/dsh-delegate/SKILL.md`（或脚本 docstring）。
