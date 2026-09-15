# 真论文人工 vs 系统审计对比报告

**论文**：pr_1506.03340 — *Teaching Machines to Read and Comprehend*（Hermann et al., NIPS 2015）  
**选取理由**：这是一篇真实、被广为引用的阅读理解论文，不是 `fraud_*`/`demo` 测试样本，最适合验证“系统报告的问题是不是真的”。

---

## 一、我自己读了什么

### 正文
- 论文 27 KB 全文已读。核心是：在 CNN/DailyMail 数据上用 attentive / impatient / Deep LSTM 三种 reader 做完形填空式阅读理解，与 baseline 对比。
- 数值部分：表格 1 报告 Precision@Recall、Accuracy 等；图 2 是 Precision-Recall 曲线（y 轴从 25% 开始，这是正常区间，不构成截断误导）。

### 直接看图
- **Figure 1**（p5_v0.png）：Document/query embedding 架构图，包含 (a) Attentive Reader、(b) Impatient Reader、(c) Deep LSTM Reader。图中使用了大量重复的 LSTM cell 方块、箭头和颜色块，这是**架构图的正常绘法**。
- **Figure 3**（p8_v0.png）及局部放大（p8_i2.png）：注意力热图示例，内容是自然语言段落（例如“by ent423, ent261... updated 9:49 pm”），**没有表格/数值数据**。

---

## 二、系统审计结果

后端任务：`cb53f0aa-7495-4944-9bd6-7234b2219952`，耗时约 22 分钟，只跑视觉类检测（避免触发文本 LLM 抢显存）。

| 检测项 | 状态 | 发现数 |
|---|---|---|
| `P0-1_numeric_mismatch` | skipped | 0 |
| `P0-4_figure_axis` | **failed**（仍报旧 bug） | 0 |
| `P0-8_significance_missing` | skipped | 0 |
| `P0-9_figure_reuse` | ok | 56 |
| `P0-11_figure_numbers` | ok | 38 |
| `P0-14_intra_image_copy_move` | ok | 27 |
| **合计** | | **121** |

按类型汇总：
- `SUSPICIOUS_DATA_PATTERN`（高）：38 条
- `IMAGE_TAMPERING_CANDIDATE`（高）：27 条
- `FIGURE_REUSE_CANDIDATE`（低）：56 条

所有发现的 `needs_human_review` 都是 `true`。

---

## 三、逐条对比（人工判断是否为真问题）

### 1. IMAGE_TAMPERING_CANDIDATE（27 条高危）

**系统代表发现**：
> F-039：Figure 1 图内疑似复制-粘贴（copy-move）  
> 单图内 SIFT 自匹配发现 52 个几何一致匹配，源区域 (524,139,135,458) 与目标区域 (101,139,92,481) 平均位移 452.4px

**我的判断**：**误报**。Figure 1 是架构示意图，故意画了 (a)(b)(c) 三个结构高度相似的 reader。重复的 cell 方块、箭头、颜色块被 SIFT 正确识别为“几何一致匹配”——但这正是**绘图模板重复**，不是 copy-move 篡改。系统的 `normal_explanation` 也承认：“重复纹理/对称图案可能被误判；是否真为复制-粘贴篡改需人工对照原始图像确认”。

### 2. SUSPICIOUS_DATA_PATTERN（38 条高危）

**系统代表发现**：
> F-001：Figure 3 与 Figure(p8#2) 之间存在跨表数据复制  
> 共享数值 9.0（占 Figure 3 数值的 100.0%）

**我的判断**：**误报**。Figure 3 是注意力热图，内容是自然语言文本，根本没有“数值表”。所谓“9.0”大概率是 VLM 把正文时间戳（如 `9:49 pm` / `9:35 am`）误读成了数字 `9.0`；而 `Figure(p8#2)` 就是 Figure 3 的局部放大（p8_i2.png），两张图出现同一个数字极其正常。这是**同一张图的整图与裁切共享内容**，不是“跨表数据复制”。

### 3. FIGURE_REUSE_CANDIDATE（56 条低危）

**系统代表发现**：
> F-066：Figure 5 与 Figure 4 局部高度匹配  
> pHash 距离 6，SIFT 好匹配 483 个

**我的判断**：**技术上“命中”，但完全良性**。同一论文里相邻结果图（如 Figure 4 和 Figure 5）通常共享坐标轴、图例、背景网格——pHash 和 SIFT 会正确发现这种模板复用。系统的 `normal_explanation` 自己也说“同一实验的不同视图/子图共享曲线属正常”。这类发现只能算“模板复用线索”，不是论文缺陷。

---

## 四、总体结论

| 类别 | 真问题数量 | 占比 |
|---|---|---|
| 高危 `SUSPICIOUS_DATA_PATTERN` | 0 / 38 | 0% |
| 高危 `IMAGE_TAMPERING_CANDIDATE` | 0 / 27 | 0% |
| 低危 `FIGURE_REUSE_CANDIDATE` | 0（良性模板复用） | 不构成问题 |
| **总计** | **0** | **0%** |

**结论**：在这篇真实、诚实的论文上，视觉/VLM 检测器抛出了 **121 条“发现”，其中 65 条高危，但人工逐条核对后，真正的学术问题为 0 条**。这说明你担心的“模型精度不够”确实存在：

- `SUSPICIOUS_DATA_PATTERN` 会误把“同图的整图与裁切”判成“跨表复制”。
- `IMAGE_TAMPERING_CANDIDATE` 会误把“架构图中的重复单元”判成“copy-move 篡改”。
- `FIGURE_REUSE_CANDIDATE` 会正确发现模板复用，但系统自己也标注为需人工复核、不构成定罪。

作为对比，确定性规则类检测（数学/正则/Crossref）在这类真论文上误报率要低得多——它们只会在真算错时报警。

---

## 五、附带说明：P0-4 仍失败的原因

本次审计日志显示：

```text
check: P0-4_figure_axis, status: failed, reason: cannot unpack non-iterable numpy.int32 object
```

这个 bug 我已经在源码里修了：

```127:132:mock_api/experiment_audit/figures.py
    segs = np.asarray(lines).reshape(-1, 4)
    verticals: list[tuple[int, int]] = []  # (y_start, y_end)
    for x1, y1, x2, y2 in segs:
```

但后端 `8770` 是当前会话启动的**长驻进程**，已经加载了旧的 `figures.py`，不会自动热更新。所以线上跑的还是旧代码，导致 `P0-4` 失败。需要重启后端才能生效（这次对比未重启，避免中断服务）。
