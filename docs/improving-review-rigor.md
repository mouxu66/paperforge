# 评审严谨性改进方案（ADR-014 · 执行记录）

> 本文档回答一个问题：**老师问"凭什么给这个分"，我们拿什么证据回答。**
> 2026-08-09 落地：P0 开关 + 真实 PeerRead 金标 + 双模型交叉复核 + 前端可复核性展示。

---

## 一、体检结论：严谨性基建很强，但生产路径全关

代码里早已就位的机制（rubric / 证据锚定 / 硬校验 / 校准偏移 / 金标回归骨架），
多数靠环境变量门控、**默认关闭**。老师看不到置信度、看不到可复现性、看不到引用核验——
不是没有，是没生效。

### 四个真实漏洞（老师不信你的原因）

| # | 漏洞 | 证据 |
|---|------|------|
| A | **同篇论文每次跑分不一样** | settings 实证：9B 模型 run-to-run 随机性 **±0.15**，淹没 QF ±0.05 微调 |
| B | **金标是假的** | `depth_gold.json` 自注 `PLACEHOLDER_SYNTHETIC`；校准偏移 -0.09 来自"LLM-vs-LLM 交叉验证"，非人类审稿人 |
| C | **本地模型只看到论文一小块** | 服务器实际 ctx=8192 token，但 `segment_paper_text` 只给 摘要+引言(≤4k字) + 正文开头(16k字) + 摘要+结论 —— 论文中段（方法/实验）基本不可见，QE 证据池残缺 |
| D | **可复核性没有展示给老师** | `verdict_reason`、证据锚点、置信区间都算好了，前端不展示 |

---

## 二、P0：严谨性开关总闸（已落地）

新增/点亮于 `.env`（模板见 `.env.rigor.example`）：

| 开关 | 值 | 作用 |
|------|----|------|
| `PAPERFORGE_EVAL_SEED=42` | 1 | 固定种子 → 同篇论文重跑分数一致（可复核） |
| `PAPERFORGE_DEPTH_GRAMMAR_ENABLED=true` | 1 | GBNF 语法约束（此前已开）：评分强制 [0,1]、evidence_id 枚举、杜绝 JSON 风暴 |
| `PAPERFORGE_QWEN_CALIBRATION=1` | 1 | 压住千问 validates_confidence 偏松（R5-R7 结构下限） |
| `PAPERFORGE_UNCERTAINTY_GATE=1` | 1 | 分数附 bootstrap 95% CI，CI 过宽 → `needs_human_review` |
| `PAPERFORGE_CITATION_VERIFY=1` | 1 | 引用真值校验（offline 一致性 + Crossref 在线 DOI 核验） |
| `PAPERFORGE_SECOND_OPINION_ENABLED=1` | 1 | 双模型交叉复核（本方案 P1 新增，见下） |

**诚实提醒**：seed 固定只对单模型内可复现；不同模型/不同版本之间的绝对分仍不可直接比较——
这正是需要"双模型复核 + 金标校准"的原因。

---

## 三、P1-金标：论文侧接入真实 PeerRead 数据（已落地）

### 做了什么

- **构建脚本** `scripts/gold/build_peerread_gold.py`：
  从 `deliverables/peerread_rescore_n200_base.jsonl`（198 篇真实 PeerRead 论文，
  **offset=0 真基线**，DEPTH 实测分 + 真实 `human_accepted` 标签）生成
  **`deliverables/gold/peerread_verdict_gold.json`**（人类 accept/reject 各 99）。
- **回归测试** `tests/test_peerread_gold_regression.py`：schema 校验 + 与人类一致率
  持续监控 + 防回退闸门。
- `mock_api/depth_gold.json` 说明改为诚实描述，指向真实金标。

### 重要发现 + 重扫结论（2026-08-09）

**旧配置 `peerread: +0.18` 是用 0.8 阈值在 N=198 上扫出来的，而 accept 阈值早已降到 0.6，
两者从未一起重新验证过。** 用真实金标在 0.6 阈值下重扫（offset_scan.py，
产物 `deliverables/gold/peerread_offset_scan_t06.json`）：

| 偏移 | 与人类一致率 | Cohen κ | 说明 |
|------|-------------|---------|------|
| **+0.18（旧生产）** | **56.6%** | 0.131 | 过度接受（FP=82） |
| 0.0（采纳） | **68.2%** | 0.364 | 稳健圆整值 |
| −0.02（数据最优） | 70.7% | 0.414 | 记录不采纳（防过拟合） |

结论：**旧 +0.18 在 0.6 阈值下把 DEPTH 推向过度接受；移除后一致率提升 11.6pp**。
生产表 `DEFAULT_OFFSET_TABLE["peerread"]` 已改为 **0.0**，并加了防回退测试。
诚实声明：68.2% 是 9B 本地模型在 PeerRead 语料上的真实水平，
仍是「需要更强模型 / 双模型复核 / 人工裁决」的依据，不是遮羞布。

---

## 四、P1-双模型：交叉复核（已落地）

### 文件：`mock_api/second_opinion.py`

- **门控**：`PAPERFORGE_SECOND_OPINION_ENABLED=1`（默认关，向后兼容）。
- **第二评审员来源**：从 `llm_configs` 表挑一个与主 provider 不同的启用配置
  （云端 GLM/DeepSeek/ChatGPT 或另一本地端点）；无第二模型 → 静默跳过。
- **轻量 prompt**：只读文本头部 6000 字符，独立输出 `score / verdict / reason`。
- **分歧判定**：`|Δscore| ≥ 0.15`（`PAPERFORGE_SECOND_OPINION_THRESHOLD`）或 verdict
  语义不一致 → `flag=disagreement` → 结果建议 `needs_human_review`。
  论文侧 verdict 等价类：accept≈minor_revision；报告侧：needs_evidence≈needs_depth。
- **全程 fail-open**：任何异常（未启用/无第二模型/超时/解析失败）只返回
  `enabled=False`，**绝不改变主评审分数与 verdict**。

### 接入点：`mock_api/depth_tasks.py`

- 论文评审（`run_depth_review_sync`）与报告评审（`run_depth_reflection_sync`）
  完成后各调一次 `run_second_opinion(...)`，结果写入
  `final_verdict.cross_check` / `reflection_result.cross_check`。
- 同时透传 `score_uncertainty`、`llm_params_snapshot`、`citation_integrity`
  三个可复核性字段（原本已在 DepthV4Result 里算好，只是没落库/没展示）。

### 修的一个真 bug（session 副作用）

`find_second_provider` 最初内部自建 `SessionLocal()` 并 `close()`——在测试的
patch 场景下返回的正是调用方的共享 session，close 导致主评审最终 commit 丢失
（记录卡在 `running`）。修复：**调用方把自己的 session 传进来复用**（`db=db`），
仅 db 为空时才自建并在 finally 关闭。生产与测试均已验证。

---

## 五、P1-前端：评审依据（可复核性）面板（已落地）

- `web/src/api/types.ts` / `web/src/api/depth.ts`：补齐
  `cross_check`、`score_uncertainty`、`llm_params_snapshot`、`citation_integrity` 类型。
- `web/src/components/ReflectionResultView.tsx`：新增「评审依据」面板——
  双模型分歧（Δscore / verdict 对照 / 第二模型名）、LLM 参数快照（模型/temp/seed）、
  置信区间（CI 宽时高亮 needs_human_review）。
- `web/src/pages/DepthReview.tsx`：新增同构卡片。

老师点开结果页就能看到：**分是怎么来的（verdict 理由）→ 把握多大（CI）→
谁来复核的（第二模型）→ 怎么复现（seed）**。

---

## 五.5 漏洞 C 软件解法：全文覆盖层（ADR-014 P9，2026-08-09 完成）

见 `mock_api/depth_fulltext.py`。开启 `PAPERFORGE_DEPTH_FULLTEXT_ENABLED=1` 后：
- **map**：全文 → 分块（~1500 字，句子级 + 重叠）→ 每块本地模型摘要（并行 3，单块失败跳过）
- **reduce**：块摘要 → 1-2 轮合并 → 全局摘要（≤1500 字）——模型"知道"全文讲了什么
- **采样增强**：按「数值/百分比/表图信号 + 位置分散度」选 top_k 块原文注入 QE/Q234 prompt，
  让 QE 能引用真实中段段落（证据锚定，不编造）
- **缓存**：按 (paper_id, text_hash) 存 `depth_fulltext_cache` 表，重评不重复付 LLM 成本
- 默认关 + 全程 fail-open（LLM 全挂也至少注入采样原文块）；`tests/test_depth_fulltext.py` 20 用例

## 六、遗留 TODO（P2，按性价比排序）

1. ✅ **偏移重扫（2026-08-09 完成）**：真实金标 + 0.6 阈值 → 采纳 0.0（68.2% / κ=0.364），
   旧 +0.18（56.6%）已移除并加防回退测试。
2. ✅ **漏洞 C 软件解法（2026-08-09 完成）**：分块摘要 + 采样增强注入（见上 §五.5）。
   后续可再评估硬件升级（12-16G 卡 + 14B 模型）作为加速器。
3. **人工盲评 20 篇**：论文侧补人类金标（reflection 侧已有 41 篇人工基准，
   但被标注"不视为权威"——老师给分后即可转正）。
4. **CI 阈值标定**：`UNCERTAINTY_GATE` 的 CI 宽度阈值需要基于金标标定，避免误报。
5. **批改闭环**：每次评审结果入库，定期重算 κ/ρ 与人类一致率，形成持续校准报表。

---

## 七、验收

- `tests/test_second_opinion.py`、`tests/test_peerread_gold_regression.py`、
  `tests/test_depth_tasks_db.py`、`tests/test_reflection_*` 全绿（173 passed）。
- 前端 `tsc -b` 通过。
