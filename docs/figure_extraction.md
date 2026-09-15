# 图表提取方案指南

PaperForge 的 DEPTH v4.2 图文一致性（QF）节点依赖论文中的图表数据来评估图文匹配度。
本文档介绍 3 种图表提取方案，用户可根据需求选择。

## 方案对比

| 方案 | 特点 | 依赖 | 推荐场景 |
|------|------|------|----------|
| **内置 Vector Extractor** | 零配置，PDF 矢量图直接渲染 | PyMuPDF | 快速上手，大多数论文 |
| **Qwen3-VL-4B** | 多模态视觉理解，OCR + 语义摘要 | llama-server | 需要深层图表理解 |
| **PicAxe** | 轻量 Python 库，开箱即用 | pip install picaxe | 批量处理，无需 GPU |

## 方案 1：内置 Vector Extractor（默认）

PaperForge 内置的 PDF 矢量图渲染器，无需额外配置。

**原理**：从 PDF 中提取矢量图（cluster_drawings），渲染为 PNG，再通过正则匹配 caption。

**启用方式**：
```bash
# 默认已启用，无需配置
PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=True
PAPERFORGE_DEPTH_FIGURE_WEIGHT=0.1
```

**适用论文类型**：
- ✅ 计算机科学（算法图、架构图、实验结果图）
- ✅ 数学/物理（公式图、数据可视化）
- ⚠️ 医学/生物（扫描图可能无法提取矢量数据）

## 方案 2：Qwen3-VL-4B（视觉理解）

使用 Qwen3-VL-4B 多模态模型对图表进行 OCR 和语义理解。

**原理**：将渲染后的图表图片送入 VLM，获取 OCR 文本和语义摘要。

**部署方式**：
```bash
# 1. 下载模型
# Qwen3-VL-4B-Instruct-Q4_K_M.gguf
# mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf

# 2. 启动 llama-server
llama-server \
  -m Qwen3-VL-4B-Instruct-Q4_K_M.gguf \
  --mmproj mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf \
  --host 127.0.0.1 \
  --port 8082 \
  -ngl 35 \
  -c 4096 \
  --jinja

# 3. 配置 PaperForge
PAPERFORGE_VISION_HTTP_URL=http://127.0.0.1:8082
```

**优点**：
- 能识别图表中的文字、数字、坐标轴标签
- 提供图表的语义摘要（"这是一个柱状图，展示了..."）
- 支持 claim_validation（验证图表数据是否支持正文声明）

**缺点**：
- 需要 GPU（推荐 8GB+ VRAM）
- 与文本 Qwen 可能存在显存竞争（需配置 VRAM_EXCLUSIVE）

## 方案 3：PicAxe（轻量 Python 库）

[PicAxe](https://github.com/picaxe/picaxe) 是一个轻量级的图表提取库。

**安装**：
```bash
pip install picaxe
```

**使用**：
```python
from picaxe import extract_figures

# 从 PDF 提取图表
figures = extract_figures("paper.pdf")
for fig in figures:
    print(f"Page {fig.page}, Figure {fig.index}")
    print(f"Caption: {fig.caption}")
    print(f"Image: {fig.image_path}")
```

**优点**：
- 零 GPU 依赖
- 安装简单（pip install）
- 适合批量处理

**缺点**：
- 无 OCR/语义理解能力
- 需要自行集成到 PaperForge

## 集成到 PaperForge

无论选择哪种方案，最终都需要将图表数据写入 `paperfigures` 表：

```sql
-- 图表数据结构
CREATE TABLE paperfigures (
    id INTEGER PRIMARY KEY,
    paper_id TEXT NOT NULL,
    page INTEGER,           -- 页码
    figure_index INTEGER,   -- 图表序号
    figure_path TEXT,       -- 图片路径
    ocr_text TEXT,          -- OCR 文本（方案2）
    qwen_summary TEXT,      -- 语义摘要（方案2）
    caption_text TEXT,      -- 标题文本
    axis_info JSON,         -- 坐标轴信息（方案2）
    claim_validation JSON,  -- 声明验证（方案2）
    source TEXT,            -- 来源：vector / qwen / picaxe
    created_at TIMESTAMP
);
```

## 推荐配置

### 快速上手（零配置）
```bash
PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=True
PAPERFORGE_DEPTH_FIGURE_WEIGHT=0.1
# 使用内置 Vector Extractor，无需额外配置
```

### 完整功能（推荐）
```bash
PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=True
PAPERFORGE_DEPTH_FIGURE_WEIGHT=0.1
PAPERFORGE_VISION_HTTP_URL=http://127.0.0.1:8082
# Vector Extractor + Qwen3-VL-4B 视觉理解
```

### 批量处理（无 GPU）
```bash
PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=True
PAPERFORGE_DEPTH_FIGURE_WEIGHT=0.1
# 使用 PicAxe 或内置 Vector Extractor
# 无 GPU 需求
```

## 常见问题

### Q: 为什么我的论文没有图表数据？
A: 检查以下几点：
1. `PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=True` 是否设置
2. PDF 中是否有可提取的矢量图（扫描版 PDF 无法提取）
3. 图表是否在 `paperfigures` 表中有记录

### Q: QF 分数为什么全是 0.5（中性）？
A: 表示没有图表数据或图表与正文一致性中性。检查：
1. `paperfigures` 表是否有该论文的记录
2. `figure_evidence_count` 是否 > 0
3. `has_figures` 字段是否为 True

### Q: 如何批量提取图表？
A: 使用 `scripts/build_figure_index.py`：
```bash
python scripts/build_figure_index.py --input papers/ --output figures/
```
