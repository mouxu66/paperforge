# ADR-015: 运行时组件注册表与动态组合机制（创造模式映射）

**状态**: Proposed

**日期**: 2026-08-14

## 背景

方法论来源：DeepSeek Harness（`dsh`）的**创造模式**——"Agent 可以检查当前运行时的插件树，在内存中试验 Cordis 插件，并据此组合和创作新的模式"。PaperForge 已将其四种运行模式（标准 / PTC / 极简 / 创造）纳入 `.agents/skills/deepseek-harness/SKILL.md`，本 ADR 把其中**创造模式**的"自省 → 试验 → 创作"三步映射为 PaperForge 的运行时机制。

现状问题（2026-08-14 代码调研）：

| 编号 | 问题 | 证据 |
|------|------|------|
| R1 | **编排静态化**：审稿 DAG 拓扑硬编码在 `build_depth_dag()` 的 if/else；审计检查是 `AuditService.run_paper_audit` 里 15 个 `if enabled(...)` 的手写序列 | `depth_pipeline.py:build_depth_dag`、`experiment_audit/service.py` |
| R2 | **单例与全局状态**：LLM provider 走 `get_factory()` 单例；组合新拓扑/新检查 = 改代码 + 重启进程 | `llm/factory.py:229`、`compute_mode.py` |
| R3 | **无运行时自省**：17 个业务路由模块在 `routers/` 各自装配；检查/节点/provider 没有统一元数据，系统无法"看见自己" | `routers/`、`main.py` |
| R4 | **无安全试验路径**：想试新检查组合或新拓扑只能改代码；红队/评测脚本只能走旁路手工脚本 | `scripts/run_fraud_paper_test.py`（未入库、无断言） |
| R5 | **算力预设只影响参数**：`compute_presets.json` 只决定 temperature/max_tokens，不影响拓扑与并行策略 | `compute_mode.py:to_kwargs` |

衔接：上一轮"PTC 对照审计"得出最高收益借鉴点是**拓扑数据化**（让 DAG 拓扑成为可生成/可组合的数据）。本 ADR 的三层机制正是该设计点的载体；同时符合已入库纪律（AGENTS.md §2）：边界显式化（纪律 3）、配置显式失败响亮（纪律 4）、模型可见 ⟺ 有日志（纪律 5）。

## 决策

引入**三层机制**：`ComponentRegistry`（自省）→ `CompositionEngine`（内存试验）→ `PresetManager`（创作与持久化）。全部使用**声明式数据**（JSON spec），**不执行 spec 中的任意代码**——保证可审计性（纪律 5）与安全边界。

### 层 1：ComponentRegistry —— 自省

- **组件树**：`kind ∈ {route, check, dag_node, llm_provider, extractor, compute_preset}`，每个组件一个注册项。
- **注册方式**：模块顶部装饰器自注册，import 时生效、幂等、顺序无关：

  ```python
  @registry.register(
      kind="check", id="P0-1_numeric_mismatch",
      deps=["table_extraction"], llm_required=False,
      cost_tier="cheap", description="正文-表格数字一致性",
  )
  def check_numeric(...): ...
  ```

- **现有组件接线表**（首期全部纳入，不改其行为）：
  - `check`：`experiment_audit/` 的 P0-1/P0-2/P0-3/P0-5/P0-6 等（已有稳定 id 约定）
  - `dag_node`：`build_depth_dag` 的 Q0/Q1/QE/Q234/QF/Q5a/Q5b/Q5c
  - `llm_provider`：`llm/factory.py` 的 openai/zhipu/deepseek/llama_cpp
  - `route`：`routers/` 下 17 个业务路由模块
  - `compute_preset`：`compute_presets.json` 各预设
- **遥测挂接**：注册项携带 `health_ref`，复用既有设施——`circuit_breaker.py` 失败计数、`depth_pipeline.get_timings()` 节点耗时、`llm_cache` 命中率，自省与监控同一数据源。
- **自省 API**（新 `routers/system.py`，全部走 `auth.py` 鉴权，写操作限定 admin scope）：
  - `GET /api/system/registry` —— 组件树 + 状态（enabled / health / failure）
  - `GET /api/system/registry/<kind>/<id>` —— 单项详情
  - `GET /api/system/registry/digest` —— LLM 可读的紧凑摘要（创造模式的"agent 视图"）

### 层 2：CompositionEngine —— 内存试验

- **输入**：声明式 spec（JSON）：

  ```json
  {
    "name": "fraud-redteam-lite",
    "checks": ["P0-1_numeric_mismatch", "P0-6_baseline_fairness"],
    "dag_topology": "standard",
    "provider_override": {"provider": "llama_cpp", "temperature": 0.0},
    "compute_override": {"max_tokens": 512}
  }
  ```

- **校验**：对 spec 做存在性、依赖闭包、冲突检测、`llm_required` vs 当前 provider 可用性、与 ADR-012 `FATAL_VETO` 护栏兼容性检查。失败**响亮**返回结构化错误（对齐 ADR-005 错误信封）。
- **构建**：**显式实例化**（把 spec 中的配置注入构造参数），不触碰 `get_factory()` / `get_settings()` 等全局单例（对齐 ADR-003 显式配置）；构建出的 `DepthDAG` / `AuditService` 携带 `sandbox=True` 标记。
- **Dry-run**：对指定 paper 真实执行，结果只落 scratch（内存或 `scripts/runs/` 类目录），**不写生产表**；产出结构（checks_run / findings / timing）复用现有审计语义，可直接对照。

### 层 3：PresetManager —— 创作与持久化

- **分层 patch 模型**（对应 dsh 的 bundle + 用户覆盖层）：`preset = {base: 内置预设, patches: [...]}`，内置预设为基线，用户/agent 创建的 preset 为覆盖层。
- **持久化**：`audit_presets.json`（扩展 `compute_presets.json` 家族）；随包内置三个预设，对应 dsh 运行模式：`standard`（标准）/ `minimal`（极简：只留 shell 类确定性检查 + 单 LLM 节点）/ `ptc-style`（类 PTC：单节点内多轮工具调用编排）。
- **晋升门槛（安全护栏）**：spec 必须通过校验 **且** ≥1 次成功 dry-run 才能持久化；记录 provenance（`creator: user|agent`、spec hash、日期、dry-run 证据）——纪律 5。
- **生效**：热生效于下一次审计 / DEPTH 运行；**不回溯改写**已持久化的审计结果。
- **API**：`POST /api/system/presets/<name>/dry-run`、`POST /api/system/presets`（晋升）、`GET / DELETE /api/system/presets/<name>`。

### Agent 入口（创造模式闭环）

`GET /api/system/registry/digest`（自省）→ 提议 spec → `dry-run`（试验）→ 通过则 `POST /api/system/presets`（创作）。即 dsh 创造模式在 PaperForge 的对应闭环。

## 非目标

- **不执行 spec 中的任意代码**：spec 是声明式数据（安全检查 + 可审计性）。
- **不改造已持久化的审计结果**：preset 只影响后续运行。
- **不做 provider 的常驻热切换**：`provider_override` 仅作用于本次组合实例。

## 后果

**收益**
- 拓扑数据化落地载体：承接 PTC 审计设计点 1/4（拓扑可组合、验证-反馈-重试包装器可在此机制上叠加）。
- 试验零成本：新检查/新拓扑先 dry-run 再上线，无需改代码重启（解决 R1/R2/R4）。
- 创造模式闭环：agent 可自省、提议、验证、沉淀 preset（解决 R3）。
- 遥测复用：熔断/计时/缓存状态进入注册表，自省与监控同一数据源。

**权衡 / 成本**
- 新组件需自注册（约定成本）：用"注册表完备性"测试兜底（对照现有 P0-* 检查清单、DAG 节点清单，新增组件缺失注册即测试失败）。
- 组合引擎需解全局单例：显式实例装配工作量集中在一个模块（`composition.py`），对齐 ADR-003。
- 自省 API 扩大暴露面：全部走 `auth.py`，写操作限定 admin scope，回环读操作默认放行。

## 实施阶段

- **P0**：`registry.py` + 现有组件接线 + 自省 API（只读，无行为变更，可独立上线）
- **P1**：`composition.py` —— spec 校验 / 显式构建 / sandbox dry-run
- **P2**：`presets.py` —— 分层预设、晋升门槛、agent 闭环、内置 standard/minimal/ptc-style 预设

每阶段向后兼容、fail-open 语义不变（对齐现有审计的 fail-open 约定）。

## 关联

- 上游：ADR-003（显式配置）、ADR-005（错误信封）、ADR-012（FATAL_VETO 护栏）、ADR-014（评分严谨化）
- 方法论：`.agents/skills/deepseek-harness/SKILL.md` 第 4 节（创造模式）、AGENTS.md §2 纪律 3/4/5
- 前置审计：PTC 对照审计（拓扑数据化 = 设计点 1）
