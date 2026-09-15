# ADR-016: 评审模型切换链 Qwen3.5-9B → GLM-4.7-Flash → Ornstein-V2

**状态**: Accepted（已实施 · Implemented 2026-08-13）
**日期**: 2026-08-13

## 背景（Context）

PaperForge 的本地评审模型在 2026-08-11 至 08-13 两天内经历了**两次快速切换**：

| 阶段 | 模型 | 类型 / 量化 | 时间 |
|------|------|------------|------|
| ① | Qwen3.5-9B | dense 9B · Q3_K_M → Q4_K_M | 初始 ~ 08-11 |
| ② | GLM-4.7-Flash | 30B-A3B MoE · IQ2_XXS | 08-11 评估 → 08-12 切换 |
| ③ | Ornstein-V2 | dense 9B · Q4_K_M | 08-12 起，当前默认 |

第一次切换（Qwen → GLM）已由 ADR-014 记录（「正式切换 GLM IQ2 为默认评审模型，替换 Qwen3.5-9B」，评估见 `deliverables/glm47flash_suitability.md` 2026-08-11）。

但**第二次切换（GLM → Ornstein-V2）没有留下任何书面记录**，导致仓库出现「模型可见声明与仓库内证据互相矛盾」：

- `README.md` / 架构图 / 导出报告页脚已写「Ornstein-V2」（本轮改名）；
- ADR-014 仍停在「生产默认 = GLM-4.7-Flash」；
- 数据库 `llm_configs` 配置行一度还挂旧名「Qwen3.5-9B」；
- `.env` 里 GLM / Qwen 的过时注释与第 33 行实际配置 `ornstein-v2-Q4_K_M.gguf` 并存。

本 ADR 补记 GLM → Ornstein-V2 的切换事实，收敛所有文档到单一事实源，使「模型可见声明 ⟺ 仓库证据」闭环。

## 决策（Decision）

1. **确立 Ornstein-V2 为当前生产默认评审模型**（8080 端 `llama-server` 单模型加载）。
   - 文件：`D:/ornstein-v2-Q4_K_M.gguf`（9B dense，Q4_K_M）。
   - 运行时：llama.cpp b10357，`-ngl 99 -c 24576 -fa on --parallel 1`，无投机草稿（b10357 无 DFlash CUDA 内核）。
   - 证据：`deliverables/ornstein_vs_human_full.csv`（41 篇全量金标对比，2026-08-13）、`ornstein_iclr_20.json`（2026-08-12）、8080 实际加载 `ornstein-v2-Q4_K_M.gguf`（2026-08-13 15:22 起）。

2. **切换动机（诚实记录）**：GLM-4.7-Flash 为 30B-A3B MoE（IQ2_XXS ~9.79GB，约 2.5GB 走 CPU 卸载），JSON 稳定（41/41）但 9B 级模型里 Ornstein-V2 在同等总 MAE 下给出更均衡的维度误差（见下），且 dense 架构无需 MoE 卸载复杂度、单篇评审更快（DEPTH ~17s vs GLM ~21.5s）。**GLM 弃用的正式书面理由此前缺失，本条以事后证据补记，不再追溯更细的当时候选讨论。**

3. **命名统一为 `Ornstein-V2`**（带连字符）。仓库此前有 `Ornstein-V2` / `Ornstein V2` / `Ornstein` 三种写法，本轮起以 `Ornstein-V2` 为准；运行时对外显示文本（README、架构图、导出报告、second_opinion docstring）均已对齐。

4. **同步清理 `.env` 过时 GLM / Qwen 注释**（本轮落地）：GLM 换入说明、GLM MoE 卸载说明、`PAPERFORGE_QWEN_CALIBRATION` 的「GLM 原始分」注释等改为与 Ornstein-V2 一致；保留历史实测注释（如 grammar 提速实测标注的「Qwen3.5-9B-Q4_K_M」）不动，因那是当时的测量记录而非「当前模型」声明。

## 金标对比数据（Ornstein-V2 vs 人类，41 篇）

`deliverables/ornstein_vs_human_full.csv`（2026-08-13，`parse_failed=0`、`llm_empty=0`，41/41 完整输出）：

| 维度 | 人工均值 | Ornstein-V2 均值 | bias | MAE |
|---|---|---|---|---|
| understanding_accuracy | 0.869 | 0.826 | −0.043 | 0.057 |
| analysis_depth | 0.827 | 0.761 | −0.066 | 0.103 |
| innovative_insights | 0.620 | 0.696 | **+0.076** | **0.140** |
| evidence_support | 0.868 | 0.826 | −0.042 | 0.058 |
| **总分** | **0.754** | **0.780** | **+0.026** | **0.064** |

对照 GLM-4.7-Flash IQ2（ADR-014）：总 MAE 相同（0.064），但 Ornstein-V2 的 AD/ES/UA 三维 MAE 更低（0.103/0.058/0.057 vs 0.133/0.085/0.070），唯 II 维更差（0.140 vs 0.097）——II 残余 +0.076 偏差正是 ADR-014 中 R4.5 原创标记收紧后遗留的已知弱项，需更强模型或人工扩样进一步收敛。

## 后果（Consequences）

- **收益**：模型声明与运行时、配置、文档三方一致；切换链可追溯；后续换模型必须走 ADR（否则再犯「声明与证据矛盾」）。
- **权衡**：II 维评分仍是弱项（bias +0.076）；Ornstein-V2 的量化/出处来源未在本仓留有下载记录，仅记录本地 gguf 路径。
- **注意**：`.env` 为 gitignored，本 ADR 的 `.env` 清理只影响本机；团队环境需各自同步第 33 行模型路径。

## 备选方案（Alternatives considered）

- **回退到 GLM-4.7-Flash**：总 MAE 持平但 AD/ES/UA 更差，且 MoE CPU 卸载增加运维复杂度 → 否决。
- **回退到 Qwen3.5-9B**：9B 级智能更受限（ADR-014 已论证）→ 否决。
- **只改代码不改文档**：即本轮之前的实际状态，正是本次审计指出的矛盾根源 → 否决。

## 相关文件

- 证据：`deliverables/ornstein_vs_human_full.csv`、`ornstein_rerun_20260813.log`、`ornstein_iclr_20.json`、`deliverables/glm47flash_suitability.md`。
- 配置：`.env`（`PAPERFORGE_LLAMA_SERVER_MODEL` 等）、数据库 `llm_configs` 表。
- 显示文本：`README.md`、`scripts/draw_architecture.py`、`scripts/export_scores_table.py`、`mock_api/second_opinion.py`。
- 关联 ADR：ADR-014（GLM 切换 + 评审严谨化）、ADR-013（VRAM 仲裁）。
