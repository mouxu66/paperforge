# DEPTH 图表一致性：人工标注实验与 PaperFigure 字段扩展计划

> 对应 DEPTH 图表整合路线图的 **④ 人工标注验证** 与 **⑤ PaperFigure 字段增强**。
> 状态：设计稿 / 待实施

## 1. 背景与目标

DEPTH v4.2 已接回图表证据：

- **轻量路径**：QE 节点把 `PaperFigure.ocr_text` / `qwen_summary` 并入证据池（前缀 `【图表】`）。
- **深度路径**：QF 节点产出 `figure_consistency_score`（0~1，默认中性 0.5）与 `inconsistency_flags`，对 `final_base` 做有界微调（±0.05）。

但当前 QF 节点仅消费 OCR 文字与 Qwen 摘要，缺少结构化信息（caption、坐标轴、子图拆分），也**未经过人工标注验证**。本计划解决两个问题：

1. **④ 建立可复现的人工标注实验**，证明 QF 打分/flag 与真实图文不一致的对应关系。
2. **⑤ 扩展 `PaperFigure` 字段**，让后续 QF 节点能基于更结构化的视觉证据做更鲁棒的图文一致性审查。

---

## 2. 当前数据现状

```
PaperFigure
├── id
├── paper_id            -> papers.id
├── page                # 所在页码
├── figure_index        # 该页第几张 XObject
├── figure_path         # uploads/figures/{paper_id}/pX_iY.png
├── ocr_text            # PaddleOCR-VL 抽出的原始文字
├── embedding           # 384-dim JSON (bge-small-en-v1.5)
└── qwen_summary        # Qwen 对 ocr_text 的解读

QFResult (depth_eval_v4.py)
├── figure_consistency_score  # 0~1
├── inconsistency_flags       # list[str]
└── has_figures                 # bool
```

**诚实上限**：目前只能抽内嵌位图 XObject，矢量图、LaTeX 编译图等无法识别；OCR 只抽字，不理解图在说什么。因此人工标注实验必须严格控制预期，先验证“字面上的不一致”能否被 QF 正确捕捉。

---

## 3. ④ 人工标注实验

### 3.1 实验目标

- 验证 `figure_consistency_score` 与人工判定的图文一致性的相关性（Spearman ≥ 0.5 视为可用）。
- 验证 `inconsistency_flags` 的精确率与召回率。
- 产出一份带人工标签的 `figure_annotation_v1.jsonl`，作为后续迭代的基准数据集。

### 3.2 样本策略

| 维度 | 建议 |
| --- | --- |
| **样本量** | 第 1 轮 50 张图（约 20-30 篇论文），后续可扩至 200 张；每条记录需 2 人标注 + 1 人仲裁。 |
| **来源** | 本地已上传论文（`papers` 表），优先选择已跑通 `pipeline_figure_understanding.py` 的论文。 |
| **分层** | 按领域（CS/生物/医学/物理）与图类型（折线图/柱状图/散点图/表格图/示意图）分层抽样。 |
| **正例比例** | 故意混入 30%-40% 已知可能含问题的图（如 arXiv 上被 comment 指出图表问题的论文），避免数据集过“干净”。 |

### 3.3 标注维度与标签体系

对每一张图，标注员需回答：

#### 3.3.1 总体一致性

| 标签 | 含义 |
| --- | --- |
| `consistent` | 图文一致，无明显问题 |
| `minor_inconsistency` | 有小问题但不影响主要结论（如拼写错误、单位省略） |
| `major_inconsistency` | 有明显矛盾，可能推翻结论（如数值与正文不符、坐标轴错误） |
| `cannot_judge` | 信息不足或无法判断 |

#### 3.3.2 问题类型（多选）

| Flag | 定义 | 示例 |
| --- | --- | --- |
| `value_mismatch` | 图中数值/趋势与正文或 caption 描述不符 | 正文说 accuracy 89.7%，图中最优点是 87.5% |
| `axis_label_error` | 坐标轴标签、单位、刻度与实际数据不符 | y 轴标成 “Loss(%)” 但展示的是 accuracy |
| `missing_baseline` | 缺少必要的 baseline 或对照组 | 声称 outperform baseline 但未显示 baseline |
| `caption_contradiction` | caption 中的结论与图本身矛盾 | caption 说“稳定提升”，图显示剧烈波动 |
| `sample_size_omission` | 未在图或 caption 中给出样本量 | error bar 看起来很大但 n 未知 |
| `significance_exaggeration` | 差异看起来很小但标注为显著 | p 值或置信区间未标 |
| `wrong_figure_type` | 选错了图类型导致误导 | 用柱状图画时间序列 |
| `visual_distortion` | 截断 y 轴、不一致比例等视觉误导 | y 轴从 88% 开始放大差异 |
| `ocr_noise` | OCR/Qwen 摘要本身错误导致误判 | 机器把 89.7 识别成 8.97 |
| `other` | 其他未覆盖问题 | — |

#### 3.3.3 置信度

标注员对本次判断的信心：`high` / `medium` / `low`。

### 3.4 标注工具 / 流程

```
Step 1: 导出
  scripts/export_figure_annotation_tasks.py --output annotations/tasks.jsonl

Step 2: 人工标注
  使用任意 JSON/CSV 编辑器或定制小网页；
  每张图展示：原始图、ocr_text、qwen_summary、正文相关段落（可选）、caption。

Step 3: 交叉标注
  同一份任务给 2 位标注员；冲突由第 3 人仲裁。

Step 4: 合并与评估
  scripts/evaluate_figure_annotations.py \
      --human annotations/human.jsonl \
      --predicted annotations/qf_predictions.jsonl \
      --output annotations/metrics.json
```

### 3.5 数据格式

**任务文件 (`tasks.jsonl`)**：

```json
{
  "task_id": "fig_0001",
  "paper_id": "upload_a1b2c3_...",
  "paper_title": "...",
  "page": 5,
  "figure_index": 1,
  "figure_path": "uploads/figures/{paper_id}/p5_i1.png",
  "figure_type_hint": "line_chart",
  "ocr_text": "...",
  "qwen_summary": "...",
  "context_paragraphs": [
    "The proposed method achieves 89.7% accuracy, surpassing baseline by 2.2%."
  ],
  "caption": "Figure 3: Test accuracy on ImageNet-1K."
}
```

**人工标注结果 (`human.jsonl`)**：

```json
{
  "task_id": "fig_0001",
  "annotator": "alice",
  "overall_label": "major_inconsistency",
  "flags": ["value_mismatch", "missing_baseline"],
  "confidence": "high",
  "comment": "The caption claims 89.7%, but the highest point in the plot is 87.5%."
}
```

**QF 预测结果 (`qf_predictions.jsonl`)**：

```json
{
  "task_id": "fig_0001",
  "figure_consistency_score": 0.42,
  "inconsistency_flags": ["value_mismatch"]
}
```

### 3.6 评估指标

#### 3.6.1 总体一致性（有序分类）

- **Spearman 相关系数**：人工 ordinal score（consistent=0, minor=1, major=2）与 `1 - figure_consistency_score` 的相关性。目标 ≥ 0.5。
- **Ordinal-F1 / 加权 Kappa**：处理类别不平衡。
- **二值化 AUC**：将 `major_inconsistency` 视为正例，`consistent+minor` 视为负例，用 `figure_consistency_score` 做 ROC-AUC。目标 ≥ 0.75。

#### 3.6.2 Flags

| 指标 | 说明 |
| --- | --- |
| 精确率 (Precision) | QF 预测的 flag 中，确实被人工标注的比例 |
| 召回率 (Recall) | 人工标注的 flag 中，QF 预测出的比例 |
| F1 | 按 flag 和全局分别计算 |
| 混淆矩阵 | 每个 flag 的 TP/FP/FN |

#### 3.6.3 人工一致性

- **Cohen’s Kappa**（两位标注员之间）：目标 ≥ 0.6。
- **Fleiss’ Kappa**（扩展至多人）。
- 对冲突项记录仲裁原因，用于迭代标签指南。

#### 3.6.4 诊断指标

- **False Positive Rate by flag**：哪些 flag QF 容易误报。
- **False Negative Rate by flag**：哪些问题 QF 漏检。
- **Score calibration**：将 `figure_consistency_score` 分桶（0.0-0.2, 0.2-0.4, ...），统计各桶中人工标注为 major 的比例，应单调递减。

---

## 4. ⑤ PaperFigure 字段扩展计划

### 4.1 新增字段

在 `paper_figures` 表新增以下结构化字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `caption` | TEXT | 图注/说明文字（从 PDF 版面邻近文本提取）。 |
| `caption_bbox` | JSON | caption 在页面上的包围框 `[x0, y0, x1, y1]`，用于对齐。 |
| `axis_labels` | JSON | 坐标轴标签，例如 `{"x": "Epoch", "y": "Accuracy (%)"}`。 |
| `axis_ticks` | JSON | 刻度信息：`{"x": [0, 10, 20, ...], "y": [80, 85, 90, ...]}`。 |
| `subfigure_regions` | JSON | 多图拆分：`[{index, bbox, label}, ...]`。 |
| `figure_type` | VARCHAR(32) | `line_chart` / `bar_chart` / `scatter` / `heatmap` / `table_image` / `other`。 |
| `table_data` | JSON | 若图为表格图，解析成结构化二维表。 |
| `source_text_span` | TEXT | 正文中引用/描述该图的段落原文，用于 QF 对齐。 |
| `ocr_engine` | VARCHAR(32) | 记录 OCR 引擎，便于追溯。 |
| `extraction_version` | VARCHAR(16) | 提取 schema 版本，例如 `v2`。 |

### 4.2 提取/更新策略

```
┌─────────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│  PDF upload     │────▶│ extract_figures_    │────▶│ PaperFigure      │
│  / reprocess    │     │ for_paper()         │     │ (基础字段)       │
└─────────────────┘     └─────────────────────┘     └──────────────────┘
                                                              │
                                                              ▼
                                                   ┌─────────────────────┐
                                                   │ 版面分析 + 结构化提取 │
                                                   │ - 找 caption        │
                                                   │ - 找坐标轴/刻度     │
                                                   │ - 多图拆分          │
                                                   │ - 表格图解析        │
                                                   └─────────────────────
                                                              │
                                                              ▼
                                                   ┌─────────────────────┐
                                                   │ 回填 caption/axis/  │
                                                   │ subfigure/table 等  │
                                                   └─────────────────────┘
```

**阶段一： caption + source_text_span**

- 在 `extract_figures_for_paper()` 中，对每张图在页面内按几何位置查找最近的 `Figure X:` / `Fig. X:` 文本块，作为 `caption`。
- 从论文 `full_text` 中匹配引用该图的句子（正则 `Fig(?:ure)?\.?\s*X`），作为 `source_text_span`。
- 收益：QF 节点可直接用 caption 和正文段落做文本-图一致性判断。

**阶段二：坐标轴 / 刻度识别**

- 对位图 figure 用 OCR + 版面分析定位 x/y 轴标签和刻度数字。
- 将 `axis_labels` / `axis_ticks` 存入 JSON。
- 收益：检测“坐标轴标签写错”“刻度范围被截断”等具体 flag。

**阶段三：多图拆分**

- 对含多个 subfigure 的图（如 `(a)`/`(b)` 排布），用规则/轻量 CV 拆分 `subfigure_regions`。
- 收益：每个子图独立进 QF，避免大图“平均化”导致的问题被稀释。

**阶段四：表格图解析**

- 对明显为表格的 figure（大量水平/竖直线或 OCR 后呈现行列结构），尝试解析成 `table_data`。
- 收益：数值级一致性校验（如 89.7 vs 87.5 自动发现）。

### 4.3 对 QF 节点的增益

| 新增字段 | QF 节点如何使用 | 预期收益 |
| --- | --- | --- |
| `caption` | 作为 prompt 输入，让 LLM 判断 caption 与图是否一致 | 减少“图没问题但 caption 错”漏检 |
| `axis_labels` / `axis_ticks` | 结构化校验：刻度是否与正文数值对齐 | 新增 `axis_label_error`、`visual_distortion` flag |
| `subfigure_regions` | 每个子图独立跑 QF，避免信息稀释 | 子图级精确召回 |
| `table_data` | 数值等式/不等式校验 | 将 `value_mismatch` 从 LLM 猜测变为规则+LLM 混合 |
| `source_text_span` | 提供上下文，降低 QF 对全文的依赖 | 更准确、更短 prompt |

### 4.4 数据迁移

新增字段均为可空或带默认值，对旧记录无影响。迁移脚本模板：

```sql
-- SQLite does not support adding multiple columns in one ALTER TABLE statement.
ALTER TABLE paper_figures ADD COLUMN caption TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN caption_bbox TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN axis_labels TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN axis_ticks TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN subfigure_regions TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN figure_type VARCHAR(32) DEFAULT 'other';
ALTER TABLE paper_figures ADD COLUMN table_data TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN source_text_span TEXT DEFAULT NULL;
ALTER TABLE paper_figures ADD COLUMN ocr_engine VARCHAR(32) DEFAULT 'llama.cpp';
ALTER TABLE paper_figures ADD COLUMN extraction_version VARCHAR(16) DEFAULT 'v1';
```

如果使用 Alembic，建议新建 migration：`add_paperfigure_structured_fields`。

### 4.5 风险与回退

| 风险 | 缓解 |
| --- | --- |
| 版面分析错误导致 `caption` 错位 | 保留 `caption_bbox`，人工标注时可快速校正；QF prompt 中标注“caption 可能不完整”。 |
| 坐标轴 OCR 噪声大 | `axis_ticks` 仅作为辅助证据，最终判断仍以 LLM 为主；可配置开关 `DEPTH_FIGURE_AXIS_ENABLED`。 |
| 多图拆分引入误分割 | 默认关闭子图级 QF，仅当 `subfigure_regions` 置信度高时启用。 |
| 表格图解析依赖外部库 | 将 `pytesseract` / `pdfplumber` 设为可选依赖；解析失败则 `table_data` 为空。 |
| 迁移后旧 figure 缺少新字段 | 旧记录 `extraction_version='v1'`，后台补跑时仅对 `v2` 以下记录重跑结构化提取。 |

---

## 5. 脚本工具

### 5.1 `scripts/export_figure_annotation_tasks.py`

用途：从本地 DB 导出待标注任务。

```bash
python scripts/export_figure_annotation_tasks.py \
  --paper-ids paper_ids.json \
  --output annotations/tasks.jsonl \
  --include-context
```

> 提示：只选择 `DepthReviewV4.final_verdict.figure_coverage == "analyzed"` 的论文/图，
> 避免对缺失或未分析图表浪费标注人力。使用 `--only-analyzed` 开关可自动过滤：
>
> ```bash
> python scripts/export_figure_annotation_tasks.py --only-analyzed --output annotations/tasks.jsonl
> ```
>
> 导出的 `caption` 会尽量跨行捕获图注；`caption_candidates` 字段保留了同一页上
> 所有候选图注，便于人工复核。

### 5.2 `scripts/export_qf_predictions.py`

用途：从 `DepthReviewV4` 导出与任务对齐的 QF 预测，供评估脚本使用。

```bash
python scripts/export_qf_predictions.py \
  --tasks annotations/tasks.jsonl \
  --output annotations/qf_predictions.jsonl
```

### 5.3 `scripts/evaluate_figure_annotations.py`

用途：合并人工标注与 QF 预测，计算指标。

```bash
python scripts/evaluate_figure_annotations.py \
  --human annotations/human.jsonl \
  --predicted annotations/qf_predictions.jsonl \
  --output annotations/metrics.json
```

支持传入第二位标注员文件计算 Cohen's Kappa（可选）：

```bash
python scripts/evaluate_figure_annotations.py \
  --human annotations/human_alice.jsonl \
  --human-2 annotations/human_bob.jsonl \
  --predicted annotations/qf_predictions.jsonl \
  --output annotations/metrics.json
```

---

## 6. 验收标准

- [ ] 完成 50 张图的人工标注，并产出 `figure_annotation_v1.jsonl`。
- [ ] 人工一致性 Cohen’s Kappa ≥ 0.6。
- [ ] `figure_consistency_score` 与人工一致性标签的 Spearman ≥ 0.5。
- [ ] 二值化 major-inconsistency 检测 ROC-AUC ≥ 0.75。
- [ ] `PaperFigure` 新增 `caption` / `axis_labels` / `subfigure_regions` 字段，且不破坏现有 `pipeline_figure_understanding.py`。
- [ ] 新增字段在 QF prompt 中可开关，默认先开启 caption 与 source_text_span。

---

## 7. 附录

### 7.1 人工标注与 QF 预测合并示例

```python
import json
from collections import Counter

LABEL_MAP = {
    "consistent": 0,
    "minor_inconsistency": 1,
    "major_inconsistency": 2,
    "cannot_judge": None,
}
```

### 7.2 建议目录结构

```
annotations/
├── tasks.jsonl
├── human_alice.jsonl
├── human_bob.jsonl
├── human_arbitrated.jsonl
├── qf_predictions.jsonl
└── metrics.json
```

### 7.3 后续可扩展方向

- 将人工标注数据集作为监督信号，微调轻量分类器来替代 LLM 的部分判断。
- 引入主动学习：对 QF score 接近 0.5 的图优先送人工标注。
- 与 `recommend_ranker` 联动，做跨论文图表一致性横向对比。
