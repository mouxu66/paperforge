<!--
本文由工程保障团队于 2026-08-17 整理，沉淀「多模型 VLM 分级图表审计 pipeline」的限速实测、
三层架构与排障修复。配套脚本：scripts/run_glm_orchestrate.py（编排）、scripts/run_orch_batch.py
（批量）、scripts/run_orch_retry.py（带进程级重试的补跑）。

关键事实速记：
- 智谱免费视觉模型：glm-4v-flash（基础，~98RPM 无限速，综合最佳）、glm-4.1v-thinking-flash
  （思考链，聚焦重扫用，间歇 1305 过载）、glm-4.6v-flash（最强但持续 1305 过载，基本不可用）、
  glm-4.7-flash（纯文本 30B MoE，统筹/裁决层，限速严重：实测 6 次仅 1 次成功）。
- Agnes AI：agnes-2.5-flash（免费，20RPM 硬限 + 内部 ~3s/次节流，不可用做主通道）、
  agnes-2.5-pro（付费，最强）。
- 三层架构：阶段0 glm-4v-flash 初筛 → 阶段1 本地 Ornstein-V2 规划（选≤6张重扫）→
  阶段2 glm-4.1v-thinking-flash 聚焦复扫 → 阶段3 本地收敛定级 →
  阶段4 云端 glm-4.7-flash 独立复核（抓本地模型幻觉，限速下优雅降级）。
- 已修复 3 个真实 bug：①初筛误走 Agnes（.env provider 锁死）②阶段4 4.7 URL 拼错
  （空 BASE_URL）③阶段1 规划撑爆本地上下文（30图 prompt 超窗）。
- 重要陷阱：.env 的 PAPERFORGE_GLM_VISION_PROVIDER=agnes 会让初筛走 Agnes 慢速通道；
  PAPERFORGE_GLM_VISION_BASE_URL= 空串会清空默认智谱端点导致阶段4 MissingSchema。
  批量运行必须用启动器显式覆盖这两个值。
-->

# 多模型 VLM 分级图表审计 Pipeline 指南

> 2026-08-17 整理。本 pipeline 用「免费视觉模型初筛 + 本地模型统筹 + 云端强模型裁决」三层架构，
> 在零成本约束下对论文图表做数字/刻度风险审计。本文覆盖模型限速实测、架构、排障与批量运行配方。

## 一、模型限速实测全景（2026-08-17）

所有模型均为免费档。限速实测结论（决定是否作主通道）：

| 模型 | 类型 | 实测限速 | 角色 | 可用性 |
|------|------|----------|------|--------|
| `glm-4v-flash` | 视觉 | ~98 RPM，无限速 | 阶段0 初筛 | ✅ 综合最佳，作主通道 |
| `glm-4.1v-thinking-flash` | 视觉(思考链) | 间歇 `1305` 平台过载 | 阶段2 聚焦复扫 | ⚠️ 可用，偶发重试 |
| `glm-4.6v-flash` | 视觉(最强) | 持续 `1305` 过载 | — | ❌ 基本不可用，弃用 |
| `glm-4.7-flash` | 纯文本 30B MoE | 严重过载（实测 6 次仅 1 次成功，`1305`/`429` 高频） | 阶段4 独立复核 | ⚠️ 必须保留但需容忍限速 |
| `agnes-2.5-flash` | 视觉 | 20 RPM 硬限 + 内部 ~3s/次节流 | — | ❌ 不可作主通道 |
| `agnes-2.5-pro` | 视觉(付费) | 付费档 | — | 最强但收费，未采用 |

**关键结论**：
- `glm-4v-flash` 综合最佳，作为初筛主通道。
- `glm-4.7-flash` 限速最严重，但**能发现本地模型（Ornstein-V2）的幻觉/误判**，作为独立裁决层必须保留；
  其调用失败仅告警不阻塞（阶段4 已 `try/except` 降级）。
- 错误码区分：`429`=限流，`1305`=智谱平台过载（非限流，需指数退避重试，不可当失败）。

## 二、三层架构

```
阶段0  初筛     glm-4v-flash（云端视觉）
                  逐张图做 A+B 交叉初筛（OCR 数字 × 视觉读数），标 ⚠冲突/一致/无数字
                  ↓
阶段1  规划     本地 Ornstein-V2 @8080（纯文本，看不到图）
                  只喂「可疑图」信号（A+B⚠/几何异常/无数字），选 ≤6 张重扫，给 focus_hint
                  ↓
阶段2  复扫     glm-4.1v-thinking-flash（云端视觉，带 focus_hint）
                  对规划选出的图聚焦重扫，输出结构化 tick_values
                  ↓
阶段3  收敛定级  本地 Ornstein-V2 @8080（纯文本）
                  以 OCR 锚点为准，给出 low/medium/high 最终判定
                  ↓
阶段4  复核     glm-4.7-flash（云端纯文本，独立第二意见）
                  复核阶段3 判定，挑出定级过松/过紧需人工再看的图；限速下优雅降级
```

**为什么 4.7 必须保留**：本地 Ornstein-V2 偶发过紧/过松定级（如把单锚点误判拉成 `high`），
实测 4.7 复核能纠正（例：`pr_1612.08810` 的 Figure 9 从 `high` 拉回「建议人工复核」）。

## 三、已修复的真实 Bug（排障必读）

### Bug 1：初筛误走 Agnes 慢速通道
- **现象**：阶段0 全部图「失败退避重试」、整篇极慢/卡死。
- **根因**：`figures.py` 的 `analyze_figure_semantic_glm` 按 `glm_vision_provider` 选上游；
  `.env` 里 `PAPERFORGE_GLM_VISION_PROVIDER=agnes` 把初筛锁到 Agnes（20RPM 硬限 + ~3s/次节流）。
- **修复**：运行启动器显式覆盖 `PAPERFORGE_GLM_VISION_PROVIDER=glm`（见第五节）。
- ⚠️ **陷阱**：脚本参数写的 `model=glm-4v-flash` 不会覆盖 provider，provider 由 env 决定。

### Bug 2：阶段4 的 4.7 复核 URL 拼错（MissingSchema）
- **现象**：阶段4 崩 `MissingSchema: Invalid URL '/chat/completions'`。
- **根因**：`.env` 中 `PAPERFORGE_GLM_VISION_BASE_URL=` 是空串，把默认智谱端点
  `https://open.bigmodel.cn/api/paas/v4` 清空；`_call_glm_text` 拼出无 scheme 的 URL。
- **修复**：`run_glm_orchestrate.py` 的 `_call_glm_text` 加兜底——
  base_url 为空时回落智谱默认端点，不再静默拼非法 URL。

### Bug 3：阶段1 规划撑爆本地上下文
- **现象**：大图数论文（如 30 图的 `pr_1506.03340`）阶段1 本地 400
  `request exceeds available context size (24576 tokens)` → 崩溃。
- **根因**：阶段1 把**全部图**的信号块拼进一个 prompt（30 图 → 29283 token），超出
  Ornstein-V2 的 24576 窗口。
- **修复**：阶段1 只喂「可疑图」信号（A+B⚠/几何异常/无数字），并加 `MAX_PLAN_CHARS=28000`
  超长截断保护 + 截断提示。阶段3/4 的 prompt 只含重扫图（≤6 张），本就安全。

## 四、错误码与重试策略

- `429` / `1305`（平台过载）/ `502` / `503` / `504` → 指数退避重试（编排脚本 `range(8)`）。
- `1305` 不是限流，是智谱平台过载，**必须纳入重试列表**（曾漏导致阶段4 直接 raise 中断整篇）。
- 阶段4 复核调用失败 → 仅告警跳过（`try/except RuntimeError`），不阻塞主流程。

## 五、运行配方

### 单篇（覆盖 .env 的 agnes 设置，走智谱上游）
```bat
rem scripts/run_orch_glm.bat
set PAPERFORGE_ORCH_LOCAL=1
set PAPERFORGE_GLM_VISION_ENABLED=1
set PAPERFORGE_GLM_VISION_PROVIDER=glm
set PAPERFORGE_GLM_VISION_API_KEY=<你的智谱key>
set PAPERFORGE_GLM_VISION_MODEL=glm-4v-flash
set PAPERFORGE_GLM_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4
set PAPERFORGE_GLM_TEXT_API_KEY=<你的智谱key>
set PAPERFORGE_GLM_TEXT_BASE_URL=https://open.bigmodel.cn/api/paas/v4
python -u scripts/run_glm_orchestrate.py <paper_id>
```
> 智谱 key 通过环境变量注入，**不要写入 `.env`**（`.env` 已被 gitignore，但 `.env.example` 仅留占位）。

### 批量（21 篇示例，单篇失败不中断）
```bash
python -u scripts/run_orch_batch.py      # 串行跑 PAPERS 列表，单篇超时 480s，汇总 scripts/runs/batch_summary.log
python -u scripts/run_orch_retry.py --auto-fail   # 读取 batch_summary.log 的 FAIL 篇，带 3 次进程级重试
python -u scripts/run_orch_retry.py <pid> <pid>   # 指定篇重试
```

### 本地模型（阶段1/3 统筹）
- Ornstein-V2（llama.cpp @8080）由 `LlamaServerManager` 托管；
  启动前确认 `http://127.0.0.1:8080/v1/models` 存活，否则阶段1/3 报「本地文本模型调用失败」。
- Windows 控制台 GBK 编码：运行前 `set PYTHONUTF8=1` + `set PYTHONIOENCODING=utf-8`，
  否则打印 ✓/⚠ 等字符会 `UnicodeEncodeError`（日志本身正常，仅控制台显示崩）。

## 六、实测结果（2026-08-17）

- 21 篇（图数 5–30，文件齐全）全量跑完：**21/21 OK**，耗时约 61 分钟（首轮 19 OK / 2 FAIL，
  经 Bug 3 修复 + 进程级重试补跑后全绿）。
- 两篇首轮失败均非逻辑问题：`pr_1506.03340` 因上下文超窗（Bug 3）+ 修复引入的拼接 SyntaxError
  连环失败；`pr_1306.2119` 偶发 `0xC000000A` native crash（重试自愈）。
- 每篇日志：`scripts/runs/orch_<paper_id>.log`；总汇总：`scripts/runs/batch_summary.log`。
