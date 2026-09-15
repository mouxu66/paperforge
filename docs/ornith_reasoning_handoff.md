# PaperForge — 本地推理模型「思考模式(Reasoning)」改造交接简报

> 用途：把现状问题 + 项目背景知识完整打包，交给更强的大模型去设计/实现「让本地推理模型在 DEPTH 评分管线里开启 chain-of-thought 并真正提升评分质量」的改造方案，之后再交回本 agent 落地。
> 最后更新：2026-08-21（本地模型已从 Ornstein-V2 切换到 Ornith-1.5-9B）

---

## 0. 一句话目标

让本地推理模型 **Ornith-1.5-9B**（推理模型，默认输出 `<think>…</think>` 先想后答）在 **DEPTH 学术严谨度评分管线** 里开启思考模式，使 CoT 真正提升评分质量，且 **不破坏现有结构化解析、不触发看门狗超时、不与 GBNF grammar 冲突**。

---

## 1. 当前遇到的核心问题（必须解决）

开 `--reasoning on` 后，DEPTH 端到端实测出现 **两个独立故障**，任一都能打挂评分：

### 问题 A：看门狗级联超时（速度问题，出在并行波次）
- 本地 llama-server 是 `--parallel 1`（单 KV 槽，一次只能处理 1 个请求，其余排队）。
- DEPTH 是 DAG 并行波次：`Q0∥Q1` 在 t=0 同时打服务、`Q234∥QF` 在 QE 后同时打。
- 开 reasoning 后单节点耗时暴涨：**Q0 实测 61.7s**（关 reasoning 仅 4s，≈15 倍），因为模型要先生成一长串 `<think>` 再给答案。
- 于是 `Q0∥Q1` 同时打单槽：Q0 占 61.7s，Q1 排队 + 自己 ~60s → Q1 墙钟 ≈ 122s > **120s 看门狗** → 节点被硬判死。
- `depth_eval_v4.py:668` `future.result(timeout=_LLM_WATCHDOG_TIMEOUT)` 直接杀节点，日志打 `LLM watchdog 硬超时 (120s)` → DAG 级联取消下游 → 整篇失败 → 回退串行模式。

### 问题 B：CoT 泄漏污染结构化解析（格式问题，串行也会中）
- 开 `--reasoning` 时，llama.cpp **本应**把思维链塞进 `reasoning_content`、把最终答案留 `content`。但对 **Ornith 这个 GGUF 分离时好时坏**——有时整段 CoT 直接落进 `content`。
- 实测 Q5a 节点的 `content` 里是 `"The user wants me to find 2-3 key reasons..."` 这种自然语言思维链，而非结构化字段 `critique_point / evidence_id / severity`。
- Q5a 解析器（`depth_eval_v4.py:255` `Q5aResult` + `:1483` 三层证据 ID 校验）一个字段都找不到 → **"Q5a: 0 条质疑"**，整篇评分作废。
- 该故障与速度无关，串行模式也照样发生。

### 问题 C（潜在地雷，暂未触发）：grammar 与 reasoning 互斥
- 实测：`--reasoning on` + GBNF `grammar` 同时开 → `content` 直接变空（grammar 约束的是最终 `content`，但 reasoning on 把答案走了 `reasoning_content` 通道，被约束的 `content` 成空串）。
- 生产路径默认 `PAPERFORGE_DEPTH_GRAMMAR_ENABLED=false`，故未触发；但「开 grammar 提格式 + 开 reasoning 提质量」双管齐下必中招。**结论：grammar 与 reasoning 在 llama.cpp 里二选一。**

### 现状临时方案（已验证可用，但牺牲了思考）
回退 `--reasoning off` 后：`openai_provider.py:216` 的 `<think>` 剥离兜底生效，Q5a 正常解析，全 DAG **24.6s**，`verdict=major_revision, score=0.290`。
**根因未解**：`--reasoning off` 时 Ornith 仍是推理模型，但服务端压住 `<think>` 前缀，最终答案进 `content` 被剥离逻辑兜底；这等于「没真正用上思考」，质量提升无从谈起。

---

## 2. 项目背景知识包（交给强 AI 必读）

### 2.1 系统架构
- 技术栈：FastAPI + SQLAlchemy + SQLite 后端（`mock_api/`）、React18+Vite 前端（`web/`）。
- 本地 LLM 走 `llama.cpp` 的 `llama-server`（OpenAI 兼容 `/v1/chat/completions`），由 `llama_server_manager.py` 拉起。
- DEPTH 评分经 `OpenAIProvider`（`mock_api/llm/openai_provider.py`）调 `127.0.0.1:8080/v1`。

### 2.2 DEPTH 评分管线（v4.2，DAG 波次并行）
拓扑（`depth_eval_v4.py:28`）：
```
Q0 ∥ Q1 → QE → Q234 ∥ QF → Q5a → Q5b → Q5c
```
- 每个节点 = 一次 LLM 调用，返回值被**严格结构化解析**（不是散文）。
- 关键节点解析约束：
  - **Q5a**（`depth_eval_v4.py:255` `Q5aResult`）：产出 2–3 条「质疑」，每条带 `evidence_id / critique_point / severity`，有**三层证据 ID 校验**（直接匹配 → 正则兜底 → 关键词提取 → 重试，见 `:1483` `_extract_evidence_id_regex`）。解析不到字段直接判 0。
  - 其余节点（Q0/Q1/QE/Q234/QF/Q5b/Q5c）各有对应 `_parse_*` 与可选 GBNF grammar（`depth_grammar_v4.py`）。
- 节点温度：**显式锁定**（`depth_eval_v4.py:1249` `self._temperature = get_settings().depth_temperature or mode_cfg.get("temperature", 0.0)`）。

### 2.3 本地 LLM 服务链路
- 启动命令构造在 `mock_api/llama_server_manager.py:_spawn()`（151–191 行），**所有 flag 已从硬编码改为读 `settings` 字段**（2026-08-21 改）。
- 当前实际 flag（Ornith 精确档）：
  ```
  -m D:/Ornith-1.5-9B-Q4_K_M.gguf --port 8080 --host 127.0.0.1
  -ngl 99 -c 24576 --parallel 1 -ctk q4_0 -ctv q4_0
  --jinja --reasoning off --temp 0.6 --top-p 0.95 --top-k 20
  --repeat-penalty 1.0 --min-p 0.0 --presence-penalty 0.0
  --samplers "top_k;top_p;temp;penalties" -fit off -fa on
  ```
  ⚠️ 关键修正：`--samplers` 链 **必须含 `top_p`**，否则 `--top-p` 设置空转不生效（llama.cpp 只执行链内采样器）。

### 2.4 当前模型与配置（Ornith 现状）
- 模型：`D:/Ornith-1.5-9B-Q4_K_M.gguf`（5.63GB，9B dense，Q4_K_M，Qwen3.5 架构）。
- 运行时：`D:/llama-b10488-win/llama-server.exe`（build 10488，够新，Qwen3.5 就绪）。
- **该 GGUF 无 MTP/nextn/draft 张量**（GGUFReader 已确认 40 元数据键 / 427 张量里无 `nextn/mtp/draft/spec`）→ **投机解码 `draft-mtp` 不可行**；`ngram` 投机对「长 prefill+短 JSON、文本多样」负载净收益≈0，不推荐。
- 硬件：RTX 5060 Laptop 8GB 显存（8151MiB），满负荷 util 94–97%、功耗恒定 114W。显存挤满，无法提高 `--parallel`（需更多 KV 槽显存）。
- 实测速度（reasoning off，修正采样链后）：decode **55.3 tok/s**，prefill 347.8 tok/s。

### 2.5 2026-08-21 已做的采样参数修正（不要回退）
- `settings.py` 新增 7 个 env 字段：`llama_server_reasoning / llama_server_temp / llama_server_top_p / llama_server_top_k / llama_server_min_p / llama_server_presence_penalty / depth_temperature`。**默认值保持 Ornstein 旧行为**（reasoning=off, temp=0.0, top_p=1.0, min_p=0.01），Ornith 经 `.env` 填新值 → **Ornstein 旧基线 κ=0.375 仍可复现**。
- `llama_server_manager.py:_spawn()` 改用 settings 字段（见 2.3）。
- `openai_provider.py:210-216`：`content = msg.get("content") or ""`，仅 content 缺失才极端兜底到 `reasoning_content`，随后仍剥离 `<think>` 标签。
- `depth_eval_v4.py:1249`：DEPTH 温度改用 `depth_temperature` 覆盖 compute_mode 默认 0.0（否则按请求把温度盖回 0.0，server `--temp` 失效）。

### 2.6 关键文件 / 行号索引
| 关注点 | 文件:行 | 说明 |
|---|---|---|
| DAG 拓扑 | `mock_api/depth_eval_v4.py:28` | `Q0∥Q1→QE→Q234∥QF→Q5a→Q5b→Q5c` |
| 看门狗超时常量 | `depth_eval_v4.py:774` | `_LLM_WATCHDOG_TIMEOUT = 120.0`（env `PAPERFORGE_LLM_WATCHDOG_TIMEOUT`） |
| 看门狗硬杀 | `depth_eval_v4.py:668` | `future.result(timeout=_LLM_WATCHDOG_TIMEOUT)` |
| 看门狗线程池 | `depth_eval_v4.py:837` | `max_workers = min(16, cpu*4)` |
| DEPTH 温度来源 | `depth_eval_v4.py:1249` | `get_settings().depth_temperature or mode_cfg temp` |
| Q5a 解析 | `depth_eval_v4.py:255` `Q5aResult`；`:1483` `_extract_evidence_id_regex` | 严格结构化，缺字段判 0 |
| grammar 门控 | `depth_eval_v4.py:649` `if grammar and _depth_grammar_enabled()`；`:720-723` | 仅 `settings.depth_grammar_enabled` 时透传 `grammar` |
| 服务启动 flag | `mock_api/llama_server_manager.py:151-191` `_spawn()` | 全部读 settings |
| 本地并发信号量 | `mock_api/llm/openai_provider.py:50-51` | `_LLM_MAX_CONCURRENCY`（env `PAPERFORGE_LLM_MAX_CONCURRENCY`，**默认 1**）→ `_LLM_SEM` |
| 本机超时上限 | `openai_provider.py:85` | `_LOCAL_TIMEOUT_CAP = 100.0`（env `PAPERFORGE_LOCAL_TIMEOUT_CAP`） |
| 安全余量 | `openai_provider.py:88` | `_WATCHDOG_SAFETY_MARGIN = 10.0` → ceiling = watchdog−10 |
| 超时收敛 | `openai_provider.py:101-111` | `effective = min(requested, cap, ceiling)` |
| chat 解析 | `openai_provider.py:198-220` | 取 content，剥离 `<think>`，丢弃 reasoning_content |
| 请求超时默认 | `mock_api/llm/factory.py:106` | `PAPERFORGE_LLM_REQUEST_TIMEOUT` 默认 120 |
| `<think>` 剥离正则 | `openai_provider.py:143-144` | `_THINK_TAG_OPEN_RE / _THINK_TAG_CLOSE_RE` |

### 2.7 超时 / 并发不变式（**改动前必读，违反会孤儿级联**）
本机 LLM 调用有一条**硬不变式**：单次本机调用占用 `_LLM_SEM` 的时长 **必须 < 看门狗超时**，否则 watchdog 判死后工作线程仍握信号量 → 下一篇排队即超时 → 孤儿级联。

当前数值链：
- 看门狗 `WATCHDOG = 120s`
- 安全余量 `MARGIN = 10s` → ceiling = 110s
- 本机超时上限 `CAP = 100s`（env `PAPERFORGE_LOCAL_TIMEOUT_CAP`）
- 请求超时 `requested = 120s`（factory 默认）
- **effective = min(120, 100, 110) = 100s** → requests 在 100s 先超时抛 `LocalLLMTimeout`（不可重试），watchdog 120s 兜底。

**要支持 reasoning（单节点 ~60s+），必须按比例同时抬 WATCHDOG 与 CAP，并保持 `CAP + MARGIN < WATCHDOG`**，例如 `WATCHDOG=300, CAP=280` → ceiling=290, effective=min(req,280,290)。**只抬一个会破坏不变式**。

### 2.8 校准与金标（κ）方法论
- DEPTH 核心指标是**与人类审稿人 verdict 的一致性 Cohen's κ**，不是通用 benchmark。
- 金标基线：Ornstein-V2 在 20 篇金标上 κ=0.375；Ornith 在 16 篇（**错配参数 temp=0.0+reasoning off+top_p 空转**）下 κ=0.298（失真，不可作结论）。
- 当前正在跑**全量 415 篇 Ornith 回归**（background task `kZVR5j`，reasoning off、temp=0.6），完成后算 Ornith 的 415 κ 与 Ornstein 0.375 对比。
- ⚠️ **温度混淆**：Ornstein 基线在 temp=0.0 测的，Ornith 现用 temp=0.6。若 ornith@0.6 ≥ 0.375 即胜出；若低于需补跑 ornith@0.0 做严格同条件对比。
- 偏置：Ornith 分数分布已漂移，金标最优偏置约 −0.04（Ornstein 为 −0.09）。更换模型后 second_opinion 阈值、reflection P 级（ADR-014 P1/P2/P3）、fidelity 层需重验。

---

## 3. 改造方案要点（供强 AI 设计，不要直接照搬）

**改动 A — 让 reasoning_content 被捕获且不影响解析**
- `openai_provider.py:198-220` 的 `chat()` 当前丢弃 `reasoning_content`。改为：在 `ChatResult` 里新增 `reasoning` 字段存 `reasoning_content`（用于审计/日志），**解析仍以 `content` 为准**。
- 把 `<think>` 剥离（`openai_provider.py:216`）做成**更鲁棒**的提取：优先取 `content` 中最后一个结构化块（如最后一个 JSON / `reasoning:` 段），忽略前导思维文本；若 `content` 为空或无可解析标记，再从 `reasoning_content` 剥离 `<think>` 后提取。

**改动 B — 配套抬升超时/并发不变式（详见 2.7）**
- 开 reasoning 后单节点 ~60s，并行波次会排队。在 8GB 显存无法提高 `--parallel` 的前提下，**必须按比例抬 `PAPERFORGE_LLM_WATCHDOG_TIMEOUT` 与 `PAPERFORGE_LOCAL_TIMEOUT_CAP`**，保持 `CAP + 10 < WATCHDOG`。
- 考虑把 reasoning 只开在「综合/判断类」节点（Q5a/Q5b/Q5c），抽取类节点（Q0/Q1/QE/Q234/QF）保持快路径，降低整体耗时与超时风险。

**改动 C — grammar 与 reasoning 互斥的硬约束**
- 在代码层保证：`depth_grammar_enabled` 与 `llama_server_reasoning=on` **不能同时为真**（二选一，或 reasoning on 时强制 grammar off），避免 `content` 变空。

**改动 D — 性能/成本权衡（重要）**
- 开 reasoning 后单节点 ~15× 慢，415 篇串行可能从 ~3–7h 涨到 ~40h+。强 AI 应给出明确的耗时预算与是否需分批/断点续跑方案（现有脚本 `batch_eval_415.py` 支持 `--limit` 与 jsonl 增量）。

**改动 E — 验证黄金路径（判定成功的标准）**
1. 单篇端到端：reasoning on 下 DAG 全节点成功，Q5a 正确解析出带 `evidence_id` 的质疑，`verdict` 合法。
2. 冒烟：原生 `/v1/chat/completions` 请求返回 `reasoning_content` 非空、`content` 为干净 JSON 或 DEPTH 格式。
3. 金标：在 16/415 金标集上算 κ，与 Ornstein 0.375 正面对打（注意温度同条件）。
4. 不回归：不破坏 Ornstein 旧基线（env 默认值保持不变即可复现）。

---

## 4. 当前进行中状态（交接时勿中断）
- **全量 415 Ornith 回归**正在后台跑（task `kZVR5j`），配置 reasoning off / temp 0.6，约 25s/篇。
- **勿为测投机解码或试 reasoning 去 kill 该服务**：记忆里记过，kill 正在跑的 llama-server 去试 MTP 会卡死 CUDA 用户态、须整机重启。
- 服务存活检查：`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health`（200=就绪）。

---

## 5. 交接给强 AI 的最小提问模板（可直接复制）

> 我在做一个本地论文评分工具 PaperForge，后端用 llama.cpp 跑本地推理模型 Ornith-1.5-9B（Qwen3.5 架构、推理模型、默认输出 `<think>…</think>`）。核心评分管线 DEPTH 是一条 DAG（`Q0∥Q1→QE→Q234∥QF→Q5a→Q5b→Q5c`），每个节点调一次 LLM 并做严格结构化解析。现在我想开启模型的思考模式（chain-of-thought）来提升评分质量，但遇到两个故障：(A) 因 llama-server `--parallel 1` + DAG 并行波次，开 reasoning 后单节点 ~60s，排队把 Q1 推过 120s 看门狗导致级联超时；(B) llama.cpp 对 Ornith 的 `<think>` 分离不稳定，CoT 有时泄漏进 `content` 污染结构化解析（Q5a 拿不到 evidence_id）。还有一个 grammar 与 reasoning 互斥的地雷。完整背景、文件行号、超时不变式见本文件 §1–§3。请设计一套改动方案（含代码改法），让思考模式真正生效且不破坏现有管道。注意：8GB 显存无法提高 `--parallel`，超时链必须保持 `CAP+10 < WATCHDOG` 不变式。
