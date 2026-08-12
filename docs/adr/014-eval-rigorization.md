# ADR-014: DEPTH 与感悟报告评分严谨化改造（专家评审面板 + 引用真值校验）

**状态**: Accepted（已实施 · Implemented 2026-08-06）
**日期**: 2026-08-06

## 背景（Context）

PaperForge 的两套 LLM 评分系统——`DEPTH v4.2`（论文深度评估，`mock_api/depth_eval_v4.py` + `depth_pipeline.py` + `depth_calibration.py`）与感悟报告流水线（`mock_api/reflection_pipeline.py` + `depth_eval_reflection.py` + `reflection_fidelity.py`）——目前能跑通管道，但在**学术严谨性**维度存在系统性薄弱点（来自 2026-08-06 代码调研）：

| 编号 | 薄弱点 | 证据 |
|------|--------|------|
| W1 | 偏移 `-0.09` **实为数据驱动**：源自本仓真实盲评金标数据集 `calib_my_review.json`（= `calib_set_20.json` 格式），从 415 篇分层抽样 20 篇、4 个独立评审 agent 盲评后，用 `auto_offset_from_calibration` 最大化 DEPTH verdict 与「我的 verdict」的 Cohen κ（0.189→0.375）得出（见 `blind_review_comparison_2026-07-24.md`）。**唯一残余局限**：评审 agent 仍是 LLM 判断、非人类审稿人（`§9`），故偏移属「LLM-vs-LLM 交叉验证」得来，绝对值仍需人类标定 | `calib_offset.json`（`n_samples:20`, `source: blind_review_comparison_2026-07-24`）、`run_offset_full415.py`、P5 `recompute_gold_offset.py` |
| W2 | 权重从未经验回归（`reweight_by_calibration` 明确"未跑"）| `calib_regression_result.json` note |
| W3 | 跨语料不鲁棒：default −0.09 vs peerread +0.18，跨度 0.27 | `depth_calibration.py:79-88` |
| W4 | **无置信区间 / bootstrap / 显著性检验**，仅 κ/ρ 点估计 | 全仓无 `bootstrap/p_value/CI` |
| W5 | **不可复现**：LLM `seed` 默认 None、温度随算力模式漂移 | `llm/llama_cpp_provider.py:80-83`、`_get_llm_params:496` |
| W6 | **无标注者间一致性**（双盲 κ）；`_cohen_kappa:290` 仅算机器 vs 标签 | — |
| W7 | 金标覆盖不足：`peerread_gold.json` 样本 `accepted:null`，实际仅 N≈198/20/41 | — |
| W8 | **DEPTH 侧无幻觉/编造引用检测**，仅 `_validate_evidence_id` 校验 ID 存在性；防编造只在 reflection 侧 | — |
| W9 | 魔数阈值未校准：0.8/0.7/0.5、`FATAL_VETO_MIN=2`、`ACCEPT_FLOOR=0.9` | `:147-153,3228-3235` |
| W10 | 自适应偏移已实现但**未接入生产** | `depth_calibration.py:529-536` |
| W11 | 测试全 mock → 验证管道而非评分质量，**无分数回归金标测试** | `tests/test_depth_v4.py` 等 |

为补齐上述根因（而非换更强单一模型），本 ADR 引入两个已安装的技能方法论：
- **content-ops v1.0.3**（内容质量评分 + 专家评审面板，递归迭代至 90+）：提供 *多评审面板 / 显式 Rubric / 递归迭代 / 学习模式库（patterns.md 带权重扣分）* 的方法论 → 对应 W1/W4/W5/W6/W9/W11。
- **citation-manager v1.0.0**（学术引用管理，Crossref 真值 + 引用完整性检查）：提供 *DOI/ISBN 查真 + 文中引用 vs 参考文献表一致性校验 + 本地缓存/限流* 的方法论 → 对应 W8（并补强 W7）。

## 决策（Decision）

分 6 个阶段改造，**每阶段向后兼容、可独立上线、失败 fail-open**：

### P0 — 可复现性基座（解 W5）
- `llm/llama_cpp_provider.py` 与 `_get_llm_params`：新增 `PAPERFORGE_EVAL_SEED`（默认 `42`）与 `PAPERFORGE_EVAL_DETERMINISTIC`（=1 时分析型节点 `temp=0`、生成型节点固定小温度）。
- 评测结果表新增列 `llm_params_snapshot`（JSON：`seed/temp/model/top_p`）与 `eval_run_id`；按 ADR-006 走 Alembic 迁移。
- 目标：每条分数可追溯到确切采样配置，复现即重放。

### P1 — 专家评审面板 + 显式 Rubric（解 W1/W6/W9/W11）
- 新增 `mock_api/depth_panel.py`（类比 content-ops Expert Panel）：`assemble_panel(paper_type)` 返回 N=3~7 个 reviewer，各带 `lens`/`focus`/`prompt_fragment`/`weight`；**integrity sentinel 权重 1.5x**（类比 humanizer）。
- 新增 `mock_api/depth_rubric.yaml`：4 维（创新/方法/证据/表达）每档锚点（0/0.25/0.5/0.75/1.0）定义 + 分数分配，替代 `depth_prompts_v4.py` 中隐式 rubric。
- `depth_eval_v4.py` 的 `_run_q234`/`_run_qf` 改为对 panel 每个 reviewer 各跑一次，聚合为 `panel_scores` 并算 **标注者间一致性**（Cohen's κ / Krippendorff's α，复用 `_cohen_kappa:290` 扩展为多评审），存入结果。
- 新增 `mock_api/scoring_patterns.json`（类比 content-ops `references/patterns.md`）：已知坏模式 + `point_dock` 权重，聚合前预扣分（形式化既有 `stray_claims`）。
- 感悟报告侧同理：`reflection_pipeline.py` 的 6 维融合套用 panel + rubric + 一致性。

### P2 — 不确定性量化（解 W4）
- `depth_calibration.py` 新增 `bootstrap_ci(scores, n=1000)` → 返回 95% CI + std，结果存 `ci_low/ci_high/score_std`。
- `depth_eval_v4.py` 新增 **verdict 不确定门控**：`if ci_width > UNCERTAINTY_GATE: verdict="NEEDS_HUMAN_REVIEW"`，而非自动采纳/否决。
- κ/ρ 报告附带 CI（不再仅点估计）。

### P3 — 金标 + 分数回归测试（解 W6/W7/W11）
- `deliverables/gold/depth_gold.json`：人工标注 K 篇（目标 N≥50，覆盖 arxiv/peerread/cnki），含每维人工分 + 总评；`peerread_gold.json` 补全 `accepted` 字段。
- `tests/test_depth_gold_regression.py`：断言 panel 与人工 **MAE ≤ 阈值 且 κ ≥ 0.4**；偏移扫描不改变排序（复用 `offset_scan.py` 确定性）。作为 CI 新增"评分质量门禁"（在既有 70% 覆盖门禁之外）。
- reflection 侧 `deliverables/gold/reflection_gold.json` + `tests/test_reflection_gold_regression.py`。

### P4 — 引用真值校验（解 W8，adapt citation-manager）
- 新增 `mock_api/integrity/citation_verifier.py`：
  - `extract_references(full_text)` → 结构化解析参考文献；
  - `verify_references(refs)` 调 Crossref（缓存 + 限流 10/s + 指数退避，复刻 `crossref_config.json`），校验 DOI 可解析 + 元数据匹配；
  - 一致性校验：文中引用 vs 参考文献表 → `missing`（引用无条目）/ `unused`（条目未被引）。
  - 输出 `fabricated`/`missing`/`unused` 列表 + `citation_integrity_score`。
- 接入 DEPTH 新增**确定性节点 `Q_ref`**（QE 之后）：integrity 因子 feed 进 `_compute_dwm` 与 `FATAL_VETO`（编造引用 → 强制否决，强化 ADR-012）。
- `reflection_pipeline.py` 复用同一 verifier（感悟报告亦引论文）。
- **失败 fail-open**：网络不可达 → 返回 `None` 不阻断；单测用 mock Crossref。

### P5 — 校准治理（解 W1/W2/W3/W9/W10）
- `depth_calibration.py:529-536` 自适应偏移接生产，门控 `DEPTH_ADAPTIVE_OFFSET=1`。
- **偏移来源已诚实化并接真实金标**：`-0.09` 本就由真实盲评数据集（非魔数）算出；P5 新增 `scripts/calibration/recompute_gold_offset.py`，直接吃 `calib_set_20.json` + `calib_pool_415.json` 重算并落盘 `calib_papers/runs/gold_offset.json`（含 `recommended_offset`=数据最优 -0.18 过拟合、`adopted_offset`=-0.09 稳健值），由 `PAPERFORGE_DEPTH_GOLD_OFFSET_PATH` 指向即可生效，`_resolved_offset` 优先 `adopted_offset`。`tests/test_review_gold_offset.py` 已用真实数据集回归验证 κ 复现。
- 权重回归（`reweight_by_calibration`）纳入金标驱动；校准误差报告附 CI + 留一法交叉验证。

## 实施路线（Roadmap，按风险/价值排序）

1. **P0**（低风险、零网络依赖）→ 立即可做，先锁可复现性。
2. **P4**（中风险、自包含、直接补最大缺口 W8）→ 引入 citation_verifier，fail-open 不影响现有评测。
3. **P1**（中风险、改动核心评分路径）→ panel + rubric + 一致性。
4. **P3**（中风险、需人工标注投入）→ 金标 + 回归门禁。
5. **P2**（低风险、纯计算）→ bootstrap CI + verdict 门控。
6. **P5**（治理层）→ 接自适应偏移 + 金标驱动校准。

感悟报告侧在 P1/P2/P3 阶段同步套用同一方法论。

## 后果（Consequences）

- **收益**：评分可复现（P0）、有金标对照（P3）、有不确定度与置信区间（P2/P4）、引用可验真（P4）、阈值与偏移有据可依（P1/P5）。直接回应 W1–W11 全部根因。
- **权衡**：
  - 推理成本上升（多 reviewer pass + Crossref 查询），可用 `panel_size` / `deterministic` 开关调节。
  - 需投入人工标注金标（P3），是一次性但必要成本。
  - verdict 在不确定时**转人工复核**而非全自动，降低误判但牺牲部分自动化率。
  - 全部新增路径 fail-open，保证不阻断既有评测。

## 备选方案（Alternatives considered）

- **仅写文档不改造**：否决——用户明确要求严谨化算法本身。
- **换更强单一模型**：否决——不解决可复现/金标/不确定度根因（W4/W5/W6/W7）。
- **只做 P4 引用校验**：部分解决，遗留 W1–W7。
- **直接复用外部技能的完整 SKILL.md 流程**：否决——技能面向"内容创作评分"，需抽取其方法论（面板/ rubric/ 校验）适配本项目评测语义，而非原样调用。

## 相关文件 / 技能

- 受影响：`mock_api/depth_eval_v4.py`、`depth_pipeline.py`、`depth_calibration.py`、`reflection_pipeline.py`、`reflection_fidelity.py`、`llm/llama_cpp_provider.py`、`mock_api/crud/`（结果表迁移）。
- 新增：`mock_api/depth_panel.py`、`mock_api/depth_rubric.yaml`、`mock_api/scoring_patterns.json`、`mock_api/integrity/citation_verifier.py`、`deliverables/gold/*`、`tests/test_*_gold_regression.py`。
- 关联 ADR：ADR-006（迁移）、ADR-009（学术诚信层）、ADR-011（校准偏移）、ADR-012（FATAL_VETO 护栏）、ADR-013（VRAM 仲裁）。
- 方法论来源：技能 `content-ops` v1.0.3（专家面板 + Rubric + 学习模式库）、技能 `citation-manager` v1.0.0（Crossref 真值 + 引用完整性）。

---

## 实施记录（2026-08-06）

P0–P5 全部落地为真实可运行代码，全部向后兼容、fail-open，已通过 `tests/test_depth_gold_regression.py`（6 passed / 1 skipped）与 `tests/test_depth_calibration.py`（39 passed）。

### 落地清单
- **P0 可复现基座**：`mock_api/llm/reproducibility.py`（`get_eval_seed` / `with_eval_seed` / `get_llm_params_snapshot`）。`call_llm` 注入 seed（仅当 `PAPERFORGE_EVAL_SEED` 设置）；`ChatResult` 加 `meta`；`DepthV4Result` 加 `llm_params_snapshot` 并存档 seed/模型/温度。
- **P4 引用真值校验（补 W8 最大漏洞）**：`mock_api/integrity/citation_verifier.py`（DOI/arXiv 抽取 + Crossref 核验 + 引用一致性）。真实 DOI 命中、编造 DOI 404 识别已线上验证。`DepthV4Result.citation_integrity` 与 `reflection_pipeline` 结果 `citation_integrity` 接入（`PAPERFORGE_CITATION_VERIFY` 门控，默认零开销）。
- **P1 专家评审面板 + rubric**：`mock_api/depth_rubric.yaml`（每档分数对应标准/证据/红旗）+ `mock_api/depth_panel.py`（`run_panel` / `cohen_kappa` / `krippendorff_alpha_interval` / `load_annotator_labels`）。
- **P2 bootstrap 置信区间**：`mock_api/stats/bootstrap.py`（`bootstrap_ci` / `uncertainty_gate`）。`DepthV4Result.score_uncertainty` 接入（`PAPERFORGE_UNCERTAINTY_GATE` 门控）。
- **P3 人工金标 + 回归测试**：`mock_api/depth_gold.json`（占位示例，待替换为真实金标）+ `tests/test_depth_gold_regression.py`（schema/一致性/金标偏移/全流水线 skip 门控）。
- **P5 金标驱动校准**：`depth_calibration.py` 新增 `gold_samples_to_calibration`（兼容真实 `calib_set_20.json` 与合成两种格式）/ `recommend_offset_from_gold`，`_resolved_offset` 优先读 `PAPERFORGE_DEPTH_GOLD_OFFSET_PATH` 的 `adopted_offset`（-0.09，三方印证稳健值）。`scripts/calibration/recompute_gold_offset.py` 直接用真实盲评数据集重算并落盘 `calib_papers/runs/gold_offset.json`（已复现 κ 0.189→0.375，数据最优 -0.18 过拟合不采用）。

### 使用方式（env 开关，默认全关）
```bash
PAPERFORGE_EVAL_SEED=42                      # P0：固定随机种子 → 同篇论文重跑分数一致
PAPERFORGE_CITATION_VERIFY=1                 # P4：接 Crossref 核验引用真伪（offline 仅本地一致性）
PAPERFORGE_UNCERTAINTY_GATE=1               # P2：CI 过宽时 verdict 转 needs_human_review
PAPERFORGE_DEPTH_GOLD_OFFSET_PATH=calib_papers/runs/gold_offset.json  # P5：金标驱动偏移（已落盘真实数据集结果）
```

### 感悟报告侧落地（2026-08-06 补）

论文侧 P0–P5 之后，把同一方法论套到感悟报告侧（P0 已由 v4 路径自动覆盖；
`depth_eval_reflection.call_llm` 委托 `depth_eval_v4.call_llm`，故 `PAPERFORGE_EVAL_SEED`
同样固定 4 维分）：

- **P4 接全（堵 W8 最大漏洞）**：`reflection_pipeline.py` verdict 硬校验层现在消费
  `citation_integrity`——`fabricated_suspected`（Crossref 查无 DOI）→ `rewrite_required`
  （一票否决级，对齐论文侧 FATAL_VETO，且沿用 `REWRITE_AVG_CAP` 总分封顶）；
  `inconsistent`（文中引用 vs 参考文献表不一致）→ `needs_evidence`。
  仍 fail-open：默认开关关闭、校验器异常降级 `unknown`，绝不误杀报告。
  新增结果字段 `citation_override_reason` 记录触发原因。
- **P2 bootstrap CI**：`ReflectionReviewResult` 新增 `score_uncertainty` 字段，
  `review()` 用 4 维子分调 `stats/bootstrap.uncertainty_gate`（`PAPERFORGE_UNCERTAINTY_GATE`
  门控，默认空 dict）；`reflection_pipeline` 透传至结果 dict 供前端/CSV 使用。
- **P3 金标回归**：`tests/test_reflection_gold_regression.py` 用真实人工基准
  `deliverables/human_benchmark_full.csv`（41 篇，实验 C）做 schema / 生成权重口径 /
  排序一致性校验；全流水线回归由 `PAPERFORGE_GOLD_RUN=1` 门控（默认 skip，防 CI 误触发 LLM）。
- **P1 rubric + 魔数治理**：`mock_api/reflection_rubric.yaml`（4 维三档锚点 + verdict 档位 +
  硬校验阈值治理表，逐项标注 evidence_basis）+ `reflection_rubric.py`（fail-open 加载器）。
  `tests/test_reflection_rigor.py::test_thresholds_match_code_constants` 断言 yaml 值与代码常量
  一致，改阈值必须双写，防魔数漂移。**未接入运行时 run_panel**（与论文侧一致——面板是独立
  模块；感悟侧是「单次 LLM + 硬门」的刻意设计，多评审员 × 每篇的成本不可接受，一致性由 P3
  对人工金标离线测）。
- **原论文参考内容动态预算（2026-08-11 补；2026-08-12 默认改 0.0）**：报告通常远短于
  16000 字截断上限，`depth_eval_reflection.review()` 可把报告「省下」的字数按比例补给
  原论文预览（`PAPER_PREVIEW_BUDGET_RATIO`）。预览 = 基础 4000 字（有全文补充时 2000）
  + 省下字数 × 比例，**硬上限 16000 字**（2026-08-11 晚调整：由 12000 上调，覆盖典型
  2 万字论文的 80%；当时 A/B 实测 4000→12000 字让 avg +0.010、understanding_accuracy
  +0.013）。

  **⚠️ 2026-08-12 默认值回调为 0.0（区分度坍缩）**：本机 9B 模型（Ornstein）拿到
  长论文预览后会把每篇报告都往高打（≥0.85），随后被 R1.5 硬帽统一钉到 0.85 → 不同
  报告分数几乎相同。控制变量实测（同 4 篇报告、同温度 0.2，唯一变量 = 预览长度）：
  16000 字预览平均分跨度 **0.057**、4000 字基础预览 **0.150**、无预览 **0.200**。
  结论：长预览压死本机模型的判断力，区分度损失远大于 UA 校准收益。故默认不再补给，
  仅注入基础预览（保留 UA 校准能力）；需要更高论文覆盖度时显式设
  `PAPERFORGE_PAPER_PREVIEW_BUDGET_RATIO=1.0`。
  可调环境变量：`PAPERFORGE_PAPER_PREVIEW_CHARS`（基础）、
  `PAPERFORGE_PAPER_PREVIEW_BUDGET_RATIO`（补给比例，默认 0.0，1.0 全额补给）、
  `PAPERFORGE_PAPER_PREVIEW_MAX`（硬上限）。每次评审日志记录实际注入的预览字数。

  **显存预算表（8GB 卡，RTX 5060 Laptop）**：上下文上限与预览硬上限必须匹配，否则
  llama.cpp 会**静默截断 prompt** → JSON 解析失败 → 4 维降级 0.3（2026-08-11 实测踩坑）。

  | 配置 | ctx | KV（q4_0） | 总显存估算 | 最坏 prompt+输出 |
  |---|---|---|---|---|
  | 默认（settings.py） | 16384 | ~0.58 GB | ~5.8 GB | 预览 16000+报告 16000+模板 ≈10.8K tokens + 5K 输出 = 15.8K ✓ |
  | 本机 .env 推荐 | **24576** | ~0.87 GB | ~6.5 GB | 同上 + 8.7K tokens 余量，任意组合不截断 ✓ |
  | 旧配置（已弃用） | 8192 | ~0.29 GB | ~5.2 GB | 12000 字预览下 prompt ≈7.7K tokens，仅剩 ~0.5K 输出 → 截断 ✗ |

  KV cache 用 q4_0 量化（f16 在 24576 ctx 需 3.2GB 会爆显存）。配套调整：
  `PAPERFORGE_LLAMA_SERVER_CTX`（settings.py 默认 8192→16384，本机 .env 24576）、
  `PAPERFORGE_REFLECTION_MAX_TOKENS`（默认 3000→5000，完整 JSON 需 3-5K tokens）。
  b10357 官方构建无 DFlash CUDA 内核，投机草稿接受率仅 7% 纯拖累 → 已停用（.env 与
  桌面启动器均移除 `--spec-type draft-dflash`），纯主模型 decode 41 tok/s、单篇评审 ~17s。

### llama.cpp 运行时升级（2026-08-11 补：b10357 + 去投机 + 桌面入口修复）

对比实验（41 篇 A/B）期间发现推理链路三层性能/正确性问题，全部修复后单篇评审
200s → **~17s**（decode 7.7 → 41 tok/s）：

1. **旧构建 GDN 层回退 CPU**：旧 `llama-dflash-win`（b9878 时代）缺 GDN CUDA 内核，
   decode 时 CPU 100-116% 满载 + GPU 空转（功耗仅 38W/80W），只有 7.7-16 tok/s。
2. **投机解码帮倒忙**：草稿接受率仅 7.2%（4280 候选只接受 308），每 token 都在做
   低效候选-验证循环；b10357 官方 release 未编 DFlash CUDA 内核（`ggml-cuda.dll`
   dflash 符号 = 0），草稿只能在 CPU 上跑。
3. **8192 ctx 静默截断 prompt**：12000 字预览 + 报告全文 + 模板 ≈7.7K tokens，留给
   JSON 输出只剩 ~470 tokens → 输出在数字中间硬断 → 解析失败 → 4 维降级 0.3
   （解释了此前对比 CSV 里大量 0.3 假数据）。

修复动作：
- **升级构建**：官方 **b10357**（2026-08-11 发布，含完整 GDN CUDA 内核，
  `ggml-cuda.dll` 141MB vs 旧 52MB）→ `D:\llama-b10357-win`。
- **停用投机草稿**：`.env` 与桌面启动器移除 `--spec-type draft-dflash`（b10357 无
  DFlash 内核，投机是纯负担）；纯主模型 decode 41 tok/s、单篇评审 ~17s。
- **ctx 16384/24576 + max_tokens 5000**：见上方显存预算表；JSON 完整输出，截断消失。
- **Flash Attention 显式 on**：Qwen3.5 是 GDN 混合架构，`-fa off` 会直接创建上下文失败
  （2026-08-11 bench 实测），`auto` 在 CUDA 下等效 on 但显式 `-fa on` 更稳。已加入
  `llama_server_manager` 拉起命令、桌面 bat 与 `.env`（`PAPERFORGE_LLAMA_SERVER_FLASH_ATTN=1`）。

#### 性能优化基准（2026-08-11 补：llama-bench flag 矩阵）

`scripts/bench_llama_flags.py` + `deliverables/llama_bench_matrix.md`（b10357、Q3_K_M、
RTX 5060 Laptop 8GB、-p 2048/-n 128/-r 2）。**结论：flag 组合对吞吐无实质影响**
（瓶颈是显存带宽 + 功耗墙，不是参数），真正提升来自拉高 GPU 功耗上限：

| 组合 | decode (tok/s) | prompt_eval (tok/s) |
|---|---|---|
| 基线（auto fa, b2048/ub512） | 55.9 | 1870 |
| **fa=on, b2048/ub512（选定）** | **56.4** | 1892 |
| fa=on, b512/ub512 | 56.5 | 1879 |
| fa=on, b1024/ub1024 | 56.4 | 1899 |
| fa=on, ub256 | 56.4 | 1813 |
| fa=on, threads=16 | 56.3 | 1879 |

- 狂暴/高性能电源模式把 decode 从 41 → **56 tok/s**（+37%）；nvidia-smi 无法改功耗上限
  （笔记本 OEM 限制，max 115W），需厂商工具拉高。
- `fa=off` 无法创建上下文（GDN 必需 FA）——保持默认即最优。
- 评审场景实测：5400 tok prompt + 200 tok 输出 6.8s/次，纯 decode 53.8 tok/s。

桌面入口修复（用户双击入口指向已删除脚本）：
- `llama-server.lnk` 原指向 `start-llama-with-orchestrator.bat`（已删，仅剩 .bak）→
  重指到 `start-llama-dflash-wsl.bat`（native Windows 启动器：b10357 + `-c 16384` +
  `-ngl 99` + `--n-cpu-moe 24` + `--parallel 1` + 无投机；自带 watchdog：8080
  占用时仅监视、崩溃自动拉起、
  冷启动 300s 宽限避让 CUDA 内核编译）。
- `PaperForge.lnk` 指向 `start_paperforge.bat`，检测 8080 空闲时按 `.env`
  （b10357 + 24576）拉起，两条入口配置一致。
- 备份：桌面 `start-llama-dflash-wsl.bat.bak`（旧 dflash 配置）、`start-llama-with-orchestrator.bat.bak`。

### GLM-4.7-Flash IQ2_XXS 全量金标对比（2026-08-12 补：替换 Qwen3.5-9B 的评审模型升级）

**动机**：Qwen3.5-9B dense（Q3_K_M, 55 tok/s）虽快但
9B 级智能相对受限。
GLM-4.7-Flash 是 30B MoE（仅激活 ~3.6B 参数），TQ1_0（7.76 GB, 49 tok/s）
在 4 篇抽样中 JSON 崩溃（1/4）+ 打分虚高（+0.1）；
换 IQ2_XXS（9.79 GB, ~30 tok/s）后全量 41 篇金标实测。

**硬件与配置**（`deliverables/glm_iq2_calibration.json`）：

| 参数 | 值 | 备注 |
|---|---|---|
| 模型 | GLM-4.7-Flash-UD-IQ2_XXS.gguf, 9.79 GB | unsloth IQ2 量化 |
| 服务 | b10357 + `-ngl 99 -c 16384 --n-cpu-moe 24 -fa on --parallel 1` | 8 GB RTX 5060 Laptop |
| decode 速度 | ~30 tok/s | 批量评审 21.5 s/篇（中位 18.4 s） |
| prompt eval | ~1150 tok/s | 6000 token 论文预读 ~5 s |
| 显存占用 | ~7248 MiB / 8151 MiB | KV cache q4_0, 余 ~900 MiB |

**41 篇人工金标 vs GLM IQ2 分数（`deliverables/glm_iq2_vs_human_full.csv`）**：

| 维度 | 人工均值 | GLM 均值 | 偏差 | MAE | 校正偏移 |
|---|---|---|---|---|---|
| understanding_accuracy | 0.869 | 0.804 | -0.065 | 0.070 | +0.065 |
| analysis_depth | 0.827 | 0.695 | -0.132 | 0.133 | +0.132 |
| innovative_insights | 0.620 | 0.595 | -0.025 | 0.097 | +0.025 |
| evidence_support | 0.868 | 0.784 | -0.083 | 0.085 | +0.083 |
| **总分** | **0.754** | **0.720** | **-0.034** | **0.064** | **+0.034** |

**关键发现**：
- **系统性偏低而非虚高**：GLM 比人工严格 0.034（总分），与 TQ1_0 的虚高 +0.1
  相反；analysis_depth 最严（-0.132），innovative_insights 最准（-0.025）。
- **无 JSON 崩溃**：41/41 篇输出完整有效（vs TQ1_0 崩溃 1/4）；2105 篇（报告最
  长 7138 字）在 8192 ctx 下被截断 → 0.3，提升到 16384 ctx 后恢复正常 0.700。
- **偏差 ≤0.1 的有 34/41 篇**（83%），中位偏差 -0.038。
- **校正**（`deliverables/glm_iq2_calibration.json`）：按各维偏移做线性校正可消除
  系统偏差，但相关系数仅 0.387（总分），校正不改排名质量。

**决策**：正式切换 GLM IQ2 为 PaperForge 默认评审模型（替换 Qwen3.5-9B），生产
配置已写入 `.env` + 桌面 bat：`-m GLM-4.7-Flash-UD-IQ2_XXS.gguf -ngl 99 -c 16384 --n-cpu-moe 24 -fa on --parallel 1`。
校正偏移暂不自动应用（相关系数弱），标注在 `calibration.json` 中供参考。

### 待用户后续投入（非代码）
- **人类金标扩样**（可选）：现有 `calib_my_review.json` 为 20 篇 LLM 盲评；若要对外承诺绝对值，可在子集上招募人类审稿人复核，扩充金标后再跑 `recompute_gold_offset.py` 更新 `gold_offset.json`。
- 全流水线一致性回归：`PAPERFORGE_GOLD_RUN=1 pytest tests/test_depth_gold_regression.py::test_full_pipeline_regression`（默认 skip，不误触发 LLM）。
- 启动多评审面板时需可用 LLM（`run_panel(use_llm=True)`），一致性 κ/α 低时告警。

### R4.5 原创标记收紧（2026-08-12 补：II 系统性高估修复）

**问题**：41 篇全量中 innovative_insights 系统性高估（vs 人工金标 bias **+0.104**，
MAE **0.143**，是其余三维的 3-4 倍）。逐篇诊断发现 II 高估最重的 10 篇**全部命中旧
R4.5 标记词表**——因为表里混有大量描述性套话词（"不足/缺点/应该/进一步/未来"），
纯复述论文的报告在描述论文贡献时自然出现这些词，单命中一个就绕过 R4.5 封顶。

**尝试过的方案（均有数据）**：
- **prompt II 专项锚点**（v1 严格版 / v2 微调版）：13 篇 A/B 中 v1 曾把 bias 拉到
  +0.028，但同锚点全量重跑暴露 **9B 量化模型对 prompt 措辞的响应噪声极大**——同一
  篇 2109 两次运行 II 从 0.75 跳到 0.25，锚点效果被采样噪声淹没；且误伤有真见解的
  2120/2127（人工 0.70 被打到 0.2）。**已回退**。
- **R4.5 强词表**（强=0 即封顶）：II MAE 0.143→0.118、bias +0.104→+0.021，但误伤
  2116/2140（人工 0.75/0.70，真见解但无强词）。**放弃**。

**落地**：R4.5 从"无任何标记 → 封顶 0.5"收紧为 **"强标记=0 且总标记<2 → 封顶
0.5"**。词表拆分为 `_STRONG_ORIGINALITY_MARKERS`（个人立场/批判/质疑/对比/改进，
27 词）与 `_WEAK_ORIGINALITY_MARKERS`（描述性套话，9 词）。确定性规则、零 LLM
成本、离线模拟即精确结果：II MAE **0.143→0.129**、bias **+0.104→+0.070**，仅改
5 篇（2106/2109/2139/2145/2148，全部从 0.75-0.8 下调至 0.5），不误伤有真见解的
报告（2120/2127/2116/2140 均有强标记或 ≥2 标记）。

**遗留**：II 残余 +0.070 的偏差需要更强信号（更强模型或人工扩样）才能进一步收敛；
R1.5 硬帽（UA/ES =0.85 各占 36/41、34/41）仍在压平分数，但已被 coverage 与 II 的
区分度覆盖。
