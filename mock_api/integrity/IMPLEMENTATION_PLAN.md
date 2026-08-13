# CS Paper Experiment Auditor — 落地实施指南

## 项目定位（一句话）

帮助 CS/ML 研究者核查论文中**正文、图表、表格之间的实验数字是否一致**，输出可追溯的审计报告，不判定造假。

---

## 落地现状（2026-08 同步，代码为准）

本文后续章节是**历史规划**，目录/编号与代码有漂移，实际实现位置如下（
代码在 `mock_api/experiment_audit/`，本文规划的 `integrity/xxx.py` 多数已改名）：

| 规划章节 | 规划文件 | 实际文件 | 状态 |
|---|---|---|---|
| 表格提取 | integrity/tables.py | `experiment_audit/tables.py` | ✅ PyMuPDF find_tables（非 camelot），lines 失败回退 text 策略 |
| 数字一致性 P0-1 | integrity/consistency.py | `experiment_audit/metrics.py` | ✅ |
| 指标验证 P0-2 | integrity/metrics.py | `experiment_audit/metrics.py` | ✅ |
| Ablation P0-3 | integrity/ablation.py | `experiment_audit/ablation.py` | ✅ |
| OpenCV 图表 P0-4 | integrity/figure_cv.py | `experiment_audit/figures.py` | ✅ 断轴+子图尺度；面板分割/图例颜色/柱线定位**未实现** |
| Qwen3-VL 语义 | integrity/figure_audit.py | `experiment_audit/figures.py` | ✅ 仅无 axis_info 兑底 |
| 双引擎交叉验证 | integrity/evidence.py | `experiment_audit/evidence.py` | ✅ 冲突标定+标注证据图 |
| 复现性清单 | integrity/reproducibility.py | `experiment_audit/reproducibility.py` | ✅（实际为 P0-5，非规划的 P0-6） |
| 代码审计 CONFIG_MISMATCH | integrity/code_audit.py | `experiment_audit/code_audit.py` | ✅ 端点驱动（未接入 run_paper_audit） |
| 报告 | integrity/report.py | `experiment_audit/report.py` | ✅ |
| 数据泄漏 P0-7 | — | `experiment_audit/data_leakage.py` | ✅ 端点驱动 |
| 图片复用 P0-9 | — | `experiment_audit/figure_reuse.py` | ✅ 论文内；跨论文召回见 `scripts/figure_reuse_index.py` |
| 引用完整性 | — | `integrity/citation_verifier.py` | ✅ |
| 金标基准 | 附录 C 验收指标 | `scripts/audit_benchmark.py` | ✅ 错误注入召回 + 候选 CSV |

当前 P0 编号事实源：`experiment_audit/__init__.py`（P0-1 数字一致、P0-2 指标自洽、
P0-3 Ablation、P0-4 图表轴、P0-5 复现清单、P0-6 Baseline 公平、P0-7 数据泄漏、
P0-8 标准差缺失、P0-9 图片复用、P0-10 报告）。

---

## 0. 开干之前先做三件事

### 0.1 收集 20 篇测试论文

去 arXiv 下载 20 篇 CS 论文（NeurIPS / ICML / CVPR / ACL 均可），要求：
- 10 篇是你觉得"写得很规范"的
- 5 篇是已知有数字问题或已撤稿的（网上搜 "retracted ML papers with data errors"）
- 5 篇随机

**目的**：在写任何代码之前，先手工标注这些论文里的数字不一致案例。这会让你在写第一行代码之前就看清所有技术难点。

### 0.2 定义 10 种 Finding 类型

先别急着写代码。在纸上完成这 10 个定义：

| Finding 类型 | 输入 | 检查方法 | 证据格式 | 风险等级 | 正常解释 |
|-------------|------|---------|---------|---------|---------|
| NUMERIC_MISMATCH | 正文数字 + 表格对应数字 | 提取→关联→比对 | 原文引用+计算过程 | HIGH | 百分比vs百分点/不同实验设置 |
| METRIC_INCONSISTENCY | Precision + Recall | F1 = 2PR/(P+R) | 公式+输入+输出 | HIGH | 四舍五入/数值精度 |
| ABLATION_UNSUPPORTED | 消融表 + 结论段落 | 逐行比对→结论强度判定 | 表格行+结论原文 | MEDIUM | 组合效应/作者表述不当 |
| CHART_AXIS_RISK | 图表 + Qwen3-VL 识别 | 检查截断/断轴/尺度 | 标注图+坐标范围 | LOW | 行业惯例/强调差异 |
| FIGURE_CAPTION_MISMATCH | 图注 + 图中实际内容 | Qwen3-VL 语义比对 | 图注原文+VLM描述 | MEDIUM | VLM识别误差 |
| MISSING_REPRO_INFO | 论文章节 | 对照 NeurIPS Checklist | 缺失项列表 | MEDIUM | 补充材料中有 |
| CONFIG_MISMATCH | 论文配置段 + 代码config | 键值对差异 | diff视图 | MEDIUM | 代码后更新/隐藏配置 |
| DATA_LEAKAGE_CANDIDATE | train+test文件 | hash比对 | 重复文件列表 | HIGH | 合法原因（同源数据） |
| FIGURE_REUSE_CANDIDATE | 多张图 | pHash+相似度 | 候选对+相似度 | LOW | 同一方法的不同视角 |
| REPRODUCTION_BLOCKER | 代码仓库 | 安装→导入→单batch | 错误日志 | MEDIUM | 环境差异 |

每种 Finding 必须有一个对应的**报告模板**。模板格式见附录 A。

### 0.3 确认复用现有能力

你已经有的：
- `pdf_parser.py`：pypdf 文本提取 + Figure 抽取（位图+矢量）→ **直接复用**
- `llm/figure_qwen.py`：Qwen3-VL HTTP 视觉理解 → **直接复用**
- `vram_scheduler.py`：text-Qwen ↔ vision-Qwen 互斥 → **不需要改**
- `database.py` + `models.py`：SQLAlchemy 数据层 → **新增表，不动旧表**
- `settings.py`：PAPERFORGE_ 环境变量体系 → **新增配置项**

**不要重写任何已有模块**。Auditor 是 PaperForge 的一个新功能，不是重写项目。

---

## 第一阶段：基础解析（第 1-3 周）

### 目标
上传 PDF → 提取所有数字、表格、图片 → 保存到数据库

### 第 1 周：表格提取

**做什么**：
1. 安装 camelot-py：`pip install camelot-py[cv]`
2. 写 `integrity/tables.py`，从 PDF 提取所有表格
3. 表格结构输出为：

```python
{
    "table_id": "table_3",
    "page": 5,
    "caption": "Table 3: Ablation study on...",
    "headers": ["Method", "F1", "Precision", "Recall"],
    "rows": [
        ["Baseline", "82.1", "81.0", "83.2"],
        ["+ Module A", "84.0", "83.5", "84.5"],
    ],
    "bbox": (x0, y0, x1, y1)
}
```

**验收标准**：对 20 篇测试论文，表格提取成功率 > 85%（19/20 篇论文的主要结果表被完整提取）。

**关键坑**：
- camelot 对无边框表格（三线表）有时识别失败，备选 tabula-py
- 跨页表格需要拼接
- 表格标题（caption）需要用 page text 关联，不是 camelot 自带

### 第 2 周：数字提取和文本关联

**做什么**：
1. 写 `integrity/numbers.py`：从正文提取所有数字及其上下文
2. 数字归类：百分比、指标值、整数、年份
3. 关联数字到最近的表格/图表引用（如 "Table 2 shows..."、"as shown in Figure 3"）

**数字提取规则**：
```python
# 只提取这些模式的数字
PATTERNS = [
    r"\d+\.\d+%",           # 84.6%
    r"\d+\.\d+",            # 84.6（上下文含指标关键词）
    r"\d+\s*/\s*\d+",       # 82/100
    r"±\s*\d+\.\d+",        # ±0.3
]
```

**关联逻辑**（确定性规则，不依赖 AI）：
```
数字在第 5 页第 3 段 → 向前搜索最近的 Table/Figure 引用 → 关联
如果本段内无引用 → 标记为 "unlinked"，留给用户手动关联
```

**验收标准**：正文中 90% 以上的指标数字被正确提取，70% 以上正确关联到对应表格。

### 第 3 周：数据模型 + API

**做什么**：
1. 在 `mock_api/integrity/models.py` 定义数据表
2. 在 `mock_api/integrity/schemas.py` 定义 API 模型
3. 写上传 + 解析的 API 端点

**数据表（第一版只需要 3 张）**：
```sql
integrity_audits: id, paper_id, status, created_at, summary_json

integrity_findings: id, audit_id, finding_type, severity,
                    title, description, page, bbox,
                    evidence_json, review_status

integrity_artifacts: id, audit_id, paper_id, kind(表格/图表/文本),
                     content_json, page, bbox
```

---

## 第二阶段：核心审计（第 4-7 周）

### 第 4-5 周：数字一致性检查（P0-1）

**做什么**：
写 `integrity/consistency.py`：

```python
def check_numeric_consistency(text_numbers, table_data):
    """
    核心逻辑：
    1. 找到正文中声称的提升值（如 "improves F1 by 3.4%"）
    2. 在关联的表格中找到对应的 baseline 和 method 行
    3. 计算实际差值
    4. 区分"百分点提升"和"相对提升"
    5. 误差容忍：±0.2 百分点 或 ±0.5% 相对
    """
```

**区分百分比 vs 百分点**（这是最容易误报的地方）：
```python
# 正文："improves by 3.4%"
# Table: 82.1 → 84.0
#
# 百分点提升: 84.0 - 82.1 = 1.9 pp
# 相对提升:   1.9 / 82.1 = 2.31%
#
# 无论是 1.9pp 还是 2.31%，都不等于 3.4% → 标记为不一致
```

**验收标准**：对 20 篇测试论文，人工注入 10 个数字错误，系统能检出 9 个，误报 < 2 个。

### 第 6 周：指标公式验证（P0-2）

**做什么**：
写 `integrity/metrics.py`：

```python
def verify_metrics(table_data):
    """
    对所有找到的 Precision/Recall 行：
    1. 提取 P, R, F1
    2. 计算 F1_calc = 2*P*R/(P+R)
    3. 比较 F1_reported vs F1_calc
    4. 误差容忍：|F1_calc - F1_reported| < 0.1

    同样逻辑用于 Accuracy (from TP/FP/FN/TN)
    """
```

**支持的指标（第一版）**：
- F1 = 2 × P × R / (P + R)
- Accuracy = (TP + TN) / (TP + TN + FP + FN)
- Precision = TP / (TP + FP)
- Recall = TP / (TP + FN)

**验收标准**：对正常论文误报为 0（所有指标验证通过），对人工篡改的数据 100% 检出。

### 第 7 周：Ablation 检查（P0-3）

**做什么**：
写 `integrity/ablation.py`：

```python
def check_ablation(table_data, claim_text):
    """
    1. 识别 ablation 表结构（第一行 Baseline，逐行加模块）
    2. 提取每行相对 Baseline 的变化量
    3. 找到论文中的 ablation 结论段落
    4. 比对"每行变化"和"结论强度"

    例如：
    表中：+Module A 下降 0.2，+Module B 下降 0.1
    结论："each component contributes positively"
    → 标记为 ABLATION_UNSUPPORTED
    """
```

**Ablation 表识别规则**（确定性）：
- 表名含 "ablation" / "消融" / "component analysis"
- 首行是 "Baseline" / "Base" / "w/o"
- 后续行是 "+ Module" / "w/ Module"
- 最后一行是 "Full" / "All" / "Ours"

---

## 第三阶段：视觉审计（第 8-10 周）

这个阶段用两个引擎协作完成图表审计：
- **OpenCV**：确定性检测（坐标轴范围、面板分割、曲线定位）——不会出错，但"看不懂"图
- **Qwen3-VL**：语义理解（图表类型、图例文字、趋势描述）——能看懂，但可能出错

两者输出的交集就是高置信度证据。

### 第 8 周：OpenCV 图表预处理（CPU，0 显存）

写 `integrity/figure_cv.py`，OpenCV 做以下确定性操作：

**8.1 图表区域定位**
```python
def extract_chart_region(figure_image):
    """
    1. 灰度化 → 自适应二值化 → 找最大轮廓
    2. 裁掉白边，定位绘图区域（坐标轴框内）
    3. 用 HoughLines 检测水平和垂直线，定位坐标轴边界
    4. 返回绘图区域的精确 bbox
    """
```

**8.2 多面板分割**
```python
def split_subplots(figure_image):
    """
    检测 Figure 2(a)(b)(c)(d) 这种多面板图：
    1. 水平/垂直投影找分割线
    2. 按 grid 结构切分成独立子图
    3. 每个子图单独送 Qwen3-VL 理解
    为什么要做：Qwen3-VL 输入一张 2×2 多面板图时容易混淆子图。
    """
```

**8.3 Y 轴截断/断轴检测**
```python
def detect_axis_truncation(figure_image):
    """
    这是 OpenCV 最有价值的检查项之一：
    1. HoughLines 定位 Y 轴位置
    2. 检测 Y 轴上是否有断轴标记（常见的是 // 或 zigzag 符号）
    3. 测量 Y 轴起点像素 vs 终点像素对应的数值范围
    4. 判断：刻度最小值是否为 0？如果不是 → CHART_AXIS_RISK

    这个检查纯靠像素计算，不依赖 Qwen3-VL 的"感觉"。
    误报率应该接近零。
    """
```

**8.4 子图尺度一致性检查**
```python
def check_subplot_scale_consistency(subplots):
    """
    同一张 Figure 中多个子图的 Y 轴尺度是否一致：
    1. 提取每个子图的 Y 轴像素长度
    2. 提取每个子图的 Y 轴刻度值范围
    3. 计算 pixels_per_unit = pixel_length / (max_val - min_val)
    4. 如果各子图 pixels_per_unit 差异 > 10% → 视觉尺度不一致

    典型场景：一个子图 Y=[0,100]，另一个 Y=[90,100]
    但两个子图被画成同样高度 → 第二个子图的差异被放大 10 倍。
    """
```

**8.5 图例颜色冲突检测**
```python
def detect_legend_color_clash(figure_image, legend_bbox):
    """
    1. 定位图例区域（通常在图表右上角或右下角）
    2. 用 K-Means 聚类提取每条图例的 RGB 主色
    3. 计算颜色间的最小欧氏距离
    4. 距离 < 30 → 两个方法颜色太接近，可能误导读图

    注意：这是辅助提示，不是错误。很多论文确实用相近颜色。
    """
```

**8.6 曲线和柱状区域定位**
```python
def locate_plot_elements(figure_image):
    """
    1. 柱状图：Canny 边缘检测 → findContours → 过滤矩形轮廓 → 记录每个 bar 的位置和像素高度
    2. 折线图：颜色空间分割 → 提取每条曲线的像素轨迹
    3. 散点图：找连通区域大小适中的圆点

    回来后做：
    - 柱状图 bar 高度比例 vs 表格数值比例是否一致
    - 折线图趋势 vs 论文描述趋势是否一致
    """
```

**验收标准**：
- Y 轴截断检测误报 < 2%（OpenCV 确定性检查，应该非常准）
- 多面板分割准确率 > 90%
- 子图尺度不一致检出率 > 95%

### 第 9 周：Qwen3-VL 图表语义理解（GPU，~3GB）

写 `integrity/figure_audit.py`，调用 Qwen3-VL 做 OpenCV 做不到的事：

**做什么**：
```python
def audit_figure_with_qwen(figure_image, caption, cv_info):
    """
    结合 OpenCV 的确定性信息（cv_info），调用 Qwen3-VL：
    1. 识别图表类型（bar/line/scatter/heatmap/diagram）
    2. 读取坐标轴标签文字和刻度值
    3. 识别图例文字（每条线/柱对应什么方法）
    4. 描述各方法之间的相对关系和主要趋势
    5. 比对图注文字和图中实际内容是否一致

    cv_info 包含：轴位置、面板分割结果、截断检测结果
    Qwen3-VL 在这些确定性信息基础上做语义理解，更准确。
    """
```

**Prompt 模板**（传给 Qwen3-VL 的）：
```
你是一个学术图表审计员。请分析图表并输出 JSON：
{
    "chart_type": "bar_chart | line_chart | scatter | heatmap | other",
    "x_axis": {"label": "...", "values": [...]},
    "y_axis": {"label": "...", "range": [min, max], "starts_from_zero": true/false},
    "legend_items": [{"name": "...", "color": "..."}],
    "main_trend": "...",
    "caption_match": true/false,
    "caption_mismatch_detail": "..."
}
图注：{caption}
[已知信息] OpenCV 检测到 Y 轴范围[{y_min}, {y_max}]，是否从0开始: {starts_from_zero}
```

**验收标准**：
- 图表类型识别 > 90%（bar/line/scatter 三种最常见类型）
- 坐标轴标签读取准确率 > 85%
- 图注比对（有明显不一致时检出率 > 80%）

### 第 10 周：双引擎交叉验证 + 视觉证据生成

写 `integrity/evidence.py`，将 OpenCV 确定性结果和 Qwen3-VL 语义结果交叉比对：

```python
def cross_validate(cv_result, qwen_result):
    """
    两个引擎独立分析同一张图，比对：
    - Y 轴范围：OpenCV 像素测量 vs Qwen3-VL 文字读取
      两者一致 → 高置信度
      两者不一致 → 标记为需要人工复核，优先相信 OpenCV
    - 图表类型：OpenCV 结构特征 vs Qwen3-VL 识别
      柱状图（OpenCV 检测到矩形群）但 Qwen3-VL 说 line_chart → 标记冲突
    """

def generate_visual_evidence(figure_image, findings):
    """
    OpenCV 绘制标注图作为证据：
    1. 在原始图表上绘制：
       - 红色虚线框标记 Y 轴截断区域
       - 黄色高亮标记坐标轴位置
       - 绿色/红色标注检查通过/未通过的地方
    2. 保存到 evidence 表，前端展示时直接引用

    这是最终报告里"可视化证据"的来源。
    """
```

**为什么 OpenCV + Qwen3-VL 双引擎**：

| 检查项 | OpenCV | Qwen3-VL | 用谁 |
|--------|--------|----------|------|
| Y 轴是否从 0 开始 | 像素测量，100% 确定 | 可能读错刻度值 | **OpenCV** |
| 子图尺度是否一致 | 像素比例计算，100% 确定 | 做不到 | **OpenCV** |
| 图表类型识别 | 结构特征（有矩形=bar） | 语义判断（整体视觉） | **交叉验证** |
| 坐标轴标签文字 | 做不到 | 文字识别 | **Qwen3-VL** |
| 图例文字 | 做不到 | 文字识别 | **Qwen3-VL** |
| 图注和图表是否一致 | 做不到 | 语义比对 | **Qwen3-VL** |
| 趋势描述 | 做不到 | 语义理解 | **Qwen3-VL** |
| 多面板分割 | 投影+检测，90%+ 准确 | 容易混淆 | **OpenCV** |

**验收标准**：
- 双引擎交叉验证后，CHART_AXIS_RISK 误报 < 5%
- 视觉证据图（标注后的图表）清晰可读

---

## 第四阶段：可复现性 + 代码审计（第 11-14 周）

### 第 11 周：复现性清单（P0-6）

**做什么**：
写 `integrity/reproducibility.py`：

对照 NeurIPS/ICML Checklist，逐项检查论文是否说明：
- 数据集版本和划分方式
- 随机种子
- 硬件和软件环境
- 超参数（lr, batch size, epochs, optimizer）
- 训练/验证/测试样本数量
- 是否报告均值和方差
- Best checkpoint 选择规则

**实现方式**：纯规则匹配。关键词搜索 + 正则。不依赖 AI。

**验收标准**：对照 NeurIPS Checklist 的 20 项，检查覆盖率达 100%。

### 第 12-14 周：GitHub 代码审计（P1-1）

**做什么**：
1. 写 `integrity/code_audit.py`：克隆仓库、扫描配置文件
2. 写 `integrity/config_diff.py`：论文配置 vs 代码配置比对

**配置文件扫描**：
```python
# 自动发现这些文件
CONFIG_FILES = [
    "config.py", "config.yaml", "config.json",
    "hparams.py", "defaults.py",
    "args.py", "arguments.py",
    "train.sh", "run.sh",
]
```

**配置项提取**（正则匹配）：
```python
CONFIG_KEYS = [
    "batch_size", "learning_rate", "lr", "epochs", "num_epochs",
    "optimizer", "weight_decay", "image_size", "seed",
    "dropout", "hidden_dim", "num_layers",
]
```

**输出**：
```json
{
    "batch_size": {"paper": 64, "code_default": 16, "match": false},
    "learning_rate": {"paper": "1e-4", "code_default": "3e-4", "match": false},
    "epochs": {"paper": 200, "code_default": 200, "match": true}
}
```

---

## 第五阶段：前端 + 报告（第 15-16 周）

### 第 15 周：审计页面

**文件**：`web/src/pages/IntegrityAuditPage.tsx`

**布局**：
```
┌──────────────────────────────────────────────────────┐
│  审计结果：论文标题                          [导出报告] │
├────────────────────────┬─────────────────────────────┤
│                        │  发现 (3 high, 2 medium)     │
│                        │                              │
│    PDF 原文            │  ● 高：F1 数值不一致          │
│    (高亮当前问题位置)   │  ● 高：正文声称 3.4% 实际 1.9% │
│                        │  ● 中：Ablation 结论过强      │
│                        │  ○ 低：Y 轴未从 0 开始       │
│                        │  ○ 低：未报告随机种子         │
│                        │                              │
│                        │  [选中项的详情]               │
│                        │  原文：...                    │
│                        │  计算过程：..                 │
│                        │  建议：..                     │
│                        │                              │
├────────────────────────┴─────────────────────────────┤
│  可复现性清单  │  图表审计  │  代码对比               │
└──────────────────────────────────────────────────────┘
```

### 第 16 周：报告导出

**输出格式**：HTML 报告 + JSON 数据

HTML 报告结构：
```
1. 总体摘要（饼图 + 统计数字）
2. 高优先级问题（红色标注）
3. 数字一致性详情
4. 指标自洽性验证
5. Ablation 分析
6. 图表审计
7. 可复现性清单
8. 代码配置对比
9. 原始证据索引
```

---

## 附录 A：Finding 报告模板

### NUMERIC_MISMATCH 模板

```
问题：正文数字与表格不一致

论文位置：第 {page} 页，{section}

正文声称：
{claim_text}

关联表格：{table_caption}

表格数据显示：
{method_name}: {metric} = {table_value}

重新计算：
{table_value2} - {table_value1} = {calculated_diff}
{百分比分析}

可能原因：
- {normal_explanation_1}
- {normal_explanation_2}
- {normal_explanation_3}

建议：{recommendation}

置信度：{confidence}
需要人工复核：{review_required}
```

### 通用字段（每条 Finding 都要有）

```python
{
    "finding_id": "uuid",
    "audit_id": "uuid",
    "finding_type": "NUMERIC_MISMATCH",
    "severity": "high" | "medium" | "low",
    "title": "一句话摘要",
    "description": "详细描述",
    "page": 5,
    "bbox": [x0, y0, x1, y1],
    "source_text": "原文引用",
    "computed_value": "系统计算值",
    "expected_value": "论文声称值",
    "evidence_ids": ["关联的证据项"],
    "normal_explanations": ["可能的正常解释"],
    "recommendation": "建议操作",
    "confidence": 0.0-1.0,
    "review_required": true,
    "review_status": "pending" | "confirmed" | "dismissed"
}
```

---

## 附录 B：目录结构

```
mock_api/
├── integrity/
│   ├── __init__.py
│   ├── models.py           # SQLAlchemy 数据表
│   ├── schemas.py          # Pydantic 模型
│   ├── service.py          # 审计流程编排
│   ├── numbers.py          # 数字提取 + 文本关联
│   ├── tables.py           # PDF 表格提取（camelot-py）
│   ├── consistency.py      # P0-1 数字一致性
│   ├── metrics.py          # P0-2 指标公式验证
│   ├── ablation.py         # P0-3 消融实验检查
│   ├── figure_cv.py        # P0-4 图表确定性检测（OpenCV：轴定位/截断/面板/颜色）
│   ├── figure_audit.py     # P0-4 图表语义理解（Qwen3-VL）
│   ├── reproducibility.py  # P0-6 复现性清单
│   ├── code_audit.py       # P1-1 代码配置对比
│   ├── evidence.py         # 证据管理
│   └── report.py           # 报告生成
├── routers/
│   └── integrity.py        # API 路由
└── workers/
    └── integrity.py        # 后台审计任务

web/src/
├── pages/
│   └── IntegrityAuditPage.tsx
└── components/
    └── integrity/
        ├── AuditUpload.tsx
        ├── AuditProgress.tsx
        ├── FindingList.tsx
        ├── FindingDetail.tsx
        ├── FigureEvidence.tsx
        ├── TableEvidence.tsx
        ├── ReproducibilityPanel.tsx
        └── AuditReportExport.tsx
```

---

## 附录 C：评测指标

不要一个笼统的"准确率"。分模块评估：

| 模块 | 指标 | 目标 |
|------|------|------|
| 表格提取 | 完整提取率（表头+所有行） | > 85% |
| 数字提取 | 召回率（应提取/已提取） | > 90% |
| 数字关联 | 正确关联到表格的比率 | > 70% |
| 数字一致性 | 检出率（人工注入错误） | > 90% |
| 数字一致性 | 误报率（正常论文） | < 5% |
| 指标验证 | 误报率 | < 1%（公式确定） |
| 图表类型 | Qwen3-VL 识别准确率 | > 90% |
| 坐标轴 | 范围和标签读取准确率 | > 85% |

**最重要的指标**：
> 每条 Finding 是否能在 30 秒内被用户人工验证（打开原文位置 → 看到证据 → 确认或驳回）

---

## 附录 D：第一版明确不做

- ❌ 论文工厂识别
- ❌ 全球论文图片库
- ❌ 自动判定学术造假
- ❌ 自动向学校/期刊举报
- ❌ 全自动复现论文
- ❌ 扫描版 PDF（旧 OCR 已退役）
- ❌ 双栏 PDF 的跨栏表格（第二版再做）
- ❌ DINOv2 / TruFor / MVSS-Net（第三阶段才考虑）
- ❌ 全自动论断抽取（改为用户半自动标注）
- ❌ 跨论文结果检索
- ❌ AI 生成文本检测

---

## 附录 E：weekly 速查表

| 周 | 做什么 | 产出 | 验收 |
|----|--------|------|------|
| 0 | 收集 20 篇论文 + 定义 10 种 Finding | 测试集 + finding_taxonomy.md | — |
| 1 | camelot-py 表格提取 | integrity/tables.py | 表格提取率 > 85% |
| 2 | 数字提取 + 文本关联 | integrity/numbers.py | 数字召回率 > 90% |
| 3 | 数据模型 + API | models.py, schemas.py, routers/ | API 可用 |
| 4-5 | 数字一致性检查 | integrity/consistency.py | 注入错误检出 > 90% |
| 6 | 指标公式验证 | integrity/metrics.py | 误报率 < 1% |
| 7 | Ablation 检查 | integrity/ablation.py | 覆盖主要 ablation 模式 |
| 8 | OpenCV 图表预处理：轴检测/面板分割/截断/尺度/颜色 | integrity/figure_cv.py | 轴截断误报<2%, 面板分割>90% |
| 9 | Qwen3-VL 图表语义理解：类型/标签/图例/趋势/图注 | integrity/figure_audit.py | 类型识别>90%, 标签>85% |
| 10 | 双引擎交叉验证 + 视觉证据标注 | integrity/evidence.py | 标记冲突、生成标注图 |
| 11 | 复现性清单 | integrity/reproducibility.py | 覆盖 NeurIPS Checklist |
| 12-14 | GitHub 代码审计 | integrity/code_audit.py | 配置提取 + 对比 |
| 15 | 前端审计页面 | IntegrityAuditPage.tsx + 组件 | 完整交互流程 |
| 16 | 报告导出 | report.py + AuditReportExport.tsx | HTML + JSON 导出 |
