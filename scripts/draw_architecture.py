"""用 PIL 绘制 PaperForge 架构图 PNG（手机可看）。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "deliverables" / "paperforge_architecture.png"

# 画布
W, H = 2400, 1700
img = Image.new("RGB", (W, H), "#FAFAFA")
d = ImageDraw.Draw(img)

# 字体
try:
    font_title = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 42)
    font_lbl = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26)
    font_sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    font_small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
except Exception:
    font_title = ImageFont.load_default()
    font_lbl = ImageFont.load_default()
    font_sub = ImageFont.load_default()
    font_small = ImageFont.load_default()

# 颜色
C_INPUT = "#BBDEFB"
C_PARSE = "#C8E6C9"
C_DAG = "#FFE0B2"
C_LLM = "#E1BEE7"
C_SUP = "#FFCCBC"
C_OUT = "#CFD8DC"
C_BORDER = "#37474F"
C_TEXT = "#1A237E"
C_SUB = "#455A64"
C_ARROW = "#546E7A"
C_WAVE = "#BF360C"

def box(x, y, w, h, fill, text, sub_lines=None, lbl_font=None, sub_font=None):
    d.rectangle([x, y, x+w, y+h], fill=fill, outline=C_BORDER, width=2)
    lf = lbl_font or font_lbl
    sf = sub_font or font_sub
    # 居中
    bbox = d.textbbox((0,0), text, font=lf)
    tw = bbox[2] - bbox[0]
    d.text((x + (w-tw)//2, y + 10), text, fill=C_TEXT, font=lf)
    if sub_lines:
        sy = y + 50
        for line in sub_lines:
            bbox = d.textbbox((0,0), line, font=sf)
            tw = bbox[2] - bbox[0]
            d.text((x + (w-tw)//2, sy), line, fill=C_SUB, font=sf)
            sy += 26

def layer_bg(x, y, w, h, label):
    d.rectangle([x, y, x+w, y+h], fill="#FAFAFA", outline="#CFD8DC", width=1)
    d.text((x+15, y+10), label, fill=C_TEXT, font=font_lbl)

def arrow(x1, y1, x2, y2, dashed=False):
    if dashed:
        # 虚线
        import math
        dx, dy = x2-x1, y2-y1
        length = math.sqrt(dx*dx + dy*dy)
        ux, uy = dx/length, dy/length
        step = 12
        pos = 0
        while pos < length - 10:
            sx = x1 + ux*pos
            sy = y1 + uy*pos
            ex = x1 + ux*min(pos+6, length-10)
            ey = y1 + uy*min(pos+6, length-10)
            d.line([sx, sy, ex, ey], fill=C_ARROW, width=2)
            pos += step
    else:
        d.line([x1, y1, x2, y2], fill=C_ARROW, width=3)
    # 箭头头
    import math
    angle = math.atan2(y2-y1, x2-x1)
    size = 10
    ax1 = x2 - size * math.cos(angle - math.pi/6)
    ay1 = y2 - size * math.sin(angle - math.pi/6)
    ax2 = x2 - size * math.cos(angle + math.pi/6)
    ay2 = y2 - size * math.sin(angle + math.pi/6)
    d.polygon([(x2, y2), (ax1, ay1), (ax2, ay2)], fill=C_ARROW)

# 标题
d.text((W//2 - 300, 20), "PaperForge v4.2 系统架构", fill=C_TEXT, font=font_title)
sub_title = "PDF → 解析 → DEPTH DAG 审稿 → 校准 → 报告（8GB VRAM 双模型互斥）"
bbox = d.textbbox((0,0), sub_title, font=font_sub)
tw = bbox[2] - bbox[0]
d.text((W//2 - tw//2, 80), sub_title, fill=C_SUB, font=font_sub)

# 第1层：输入层
layer_bg(40, 130, 2320, 140, "① 输入层")
box(100, 180, 400, 80, C_INPUT, "PDF 上传", ["routers/upload.py"], font_lbl, font_small)
box(600, 180, 400, 80, C_INPUT, "TaskManager 派发", ["tasks.py + concurrency.lock"], font_lbl, font_small)
box(1100, 180, 400, 80, C_INPUT, "depth_review_worker", ["workers/reviews.py"], font_lbl, font_small)
box(1600, 180, 440, 80, C_INPUT, "run_depth_review_sync", ["depth_tasks.py"], font_lbl, font_small)
box(2100, 180, 220, 80, C_INPUT, "DepthReviewer", ["depth_eval_v4.py"], font_lbl, font_small)

# 第2层：解析与 Figure 抽取层
layer_bg(40, 290, 2320, 200, "② 解析与 Figure 抽取层")
box(100, 340, 540, 130, C_PARSE, "pdf_parser.process_one_pdf", ["标题/作者/年份/摘要", "+ full_text + chunks(512+64)", "+ FTS5 索引"], font_lbl, font_small)
box(700, 340, 540, 130, C_PARSE, "extract_figures_for_paper", ["skip_ocr=True（已弃用 OCR）", "pymupdf 抽图 + 关联 caption", "bitmap/vector 双源"], font_lbl, font_small)
box(1300, 340, 540, 130, C_PARSE, "figure_qwen._ask_vision_on_figure", ["HTTP POST 8082 Qwen3-VL-4B", "base64 img + caption prompt", "→ qwen_summary（含文字/轴/语义）"], font_lbl, font_small)
box(1900, 340, 420, 130, C_PARSE, "upsert_figure → DB", ["paper_figures 表", "ocr_text/qwen_summary/", "caption/axis_info/embedding"], font_lbl, font_small)

# 第3层：DEPTH DAG 核心
layer_bg(40, 510, 1880, 600, "③ DEPTH v4.2 DAG 审稿流水线（depth_pipeline.build_depth_dag）")
d.text((W//2 - 400, 530), "DAG 自动拓扑校验 + 同波 asyncio.gather 并发 + 失败级联取消 + 节点计时", fill=C_SUB, font=font_small)

# Q0 Q1
box(100, 630, 240, 100, C_DAG, "Q0", ["整体印象", "实质贡献?"], font_lbl, font_small)
box(420, 630, 240, 100, C_DAG, "Q1", ["类型判别", "A/B/C/D + conf"], font_lbl, font_small)
d.text((700, 660), "Wave 1: Q0 ∥ Q1 并发", fill=C_WAVE, font=font_lbl)
arrow(220, 730, 520, 730)
arrow(520, 730, 520, 780)

# QE
box(420, 780, 240, 100, C_DAG, "QE", ["证据池 5-7 条", "+ 图表证据"], font_lbl, font_small)
d.text((700, 810), "Wave 2 → Wave 3", fill=C_WAVE, font=font_lbl)
arrow(540, 880, 540, 920)

# Q234 QF
box(100, 920, 360, 100, C_DAG, "Q234（合并评分）", ["单次 LLM 调用", "novelty/rigor/infl/repro"], font_lbl, font_small)
box(560, 920, 320, 100, C_DAG, "QF（图文一致）", ["无图→中性 0.5", "depth_figure_weight=0"], font_lbl, font_small)
arrow(280, 1020, 280, 1070)
arrow(720, 1020, 720, 1070)
arrow(280, 1070, 1100, 1070)
arrow(720, 1070, 1100, 1070)
arrow(1100, 1070, 1100, 1090)

# Q5a Q5b Q5c
box(960, 1090, 300, 60, C_DAG, "Q5a 质疑者", None, font_lbl, font_small)
box(1300, 1090, 300, 60, C_DAG, "Q5b 辩护者", None, font_lbl, font_small)
box(1640, 1090, 260, 60, C_DAG, "Q5c 主席校准", None, font_lbl, font_small)
arrow(1260, 1120, 1300, 1120)
arrow(1600, 1120, 1640, 1120)
d.text((1100, 1170), "Wave 4 → 5 → 6 串行辩论", fill=C_WAVE, font=font_lbl)

d.text((600, 1240), "_compute_dwm 加权 + apply_score_offset 校准 + fatal_veto 一票否决 + verdict 阈值", fill=C_SUB, font=font_small)

# LLM 调用层
layer_bg(1960, 510, 400, 600, "④ LLM 调用层")
box(2000, 600, 320, 120, C_LLM, "call_llm", ["depth_eval_v4.py:521", "缓存 300s + 重试 3 次", "+ ThreadPool 超时"], font_lbl, font_small)
box(2000, 740, 320, 120, C_LLM, "LLMFactory", ["OpenAIProvider", "+ 并发信号量", "保护单实例"], font_lbl, font_small)
box(2000, 880, 320, 120, C_LLM, "Qwen3.5-9B (8080)", ["纯文本模型", "Q3_K_M.gguf", "+ dflash 投机解码"], font_lbl, font_small)
box(2000, 1020, 320, 120, C_LLM, "Qwen3-VL-4B (8082)", ["视觉多模态", "Q4_K_M + mmproj", "→ qwen_summary"], font_lbl, font_small)

# 第5层：支撑与治理层
layer_bg(40, 1140, 2320, 240, "⑤ 支撑与治理层")
box(100, 1190, 360, 170, C_SUP, "VRAMScheduler", ["vram_scheduler.py", "IDLE/QWEN/OCR 状态机", "request_qwen/request_ocr", "互斥切换 + autostart"], font_lbl, font_small)
box(500, 1190, 360, 170, C_SUP, "LlamaServerManager", ["llama_server_manager.py", "端口预检 + /health", "冷启动宽限", "taskkill /F /T 释放"], font_lbl, font_small)
box(900, 1190, 360, 170, C_SUP, "depth_calibration", ["三层校准", "① 全局偏移 -0.09", "② 分档表 peerread=0.0（0.6 阈值重扫）", "③ 校准集回归"], font_lbl, font_small)
box(1300, 1190, 360, 170, C_SUP, "Verdict 阈值", ["accept=0.6 / reject=0.5", "FATAL_VETO_MIN=2", "FATAL_VETO_FLOOR=0.9", "delta[-0.25,+0.25]"], font_lbl, font_small)
box(1700, 1190, 360, 170, C_SUP, "circuit_breaker", ["+ retry_utils", "tenacity 指数退避", "3 次 (1s/2s/4s)", "熔断保护"], font_lbl, font_small)
box(2100, 1190, 220, 170, C_SUP, "SQLite + FTS5", ["paperforge_mock.db", "papers", "depth_reviews_v4", "+ 384d 向量"], font_lbl, font_small)

# 第6层：输出层
layer_bg(40, 1400, 2320, 220, "⑥ 输出与校验层")
box(100, 1450, 520, 140, C_OUT, "depth_reviews_v4 表", ["Q0~Q5c 节点结果 JSON", "+ final_verdict", "+ calibrated_score"], font_lbl, font_small)
box(660, 1450, 520, 140, C_OUT, "有效性闸口", ["证据池空 + critique 空", "+ 全 0.5 → 抛异常", "DepthReviewInvalidError"], font_lbl, font_small)
box(1220, 1450, 520, 140, C_OUT, "GET /api/depth/v4/result", ["routers/depth.py:258", "前端拉取结果", "+ SSE 推送"], font_lbl, font_small)
box(1780, 1450, 520, 140, C_OUT, "校准产物", ["calib_offset.json", "+ calib_pool_415.json", "+ threshold_scan"], font_lbl, font_small)

# 主数据流箭头（层间）
arrow(300, 260, 300, 340)  # 1→2
arrow(780, 260, 780, 340)
arrow(1380, 260, 1380, 340)
arrow(1980, 260, 1980, 340)
arrow(2200, 260, 2200, 510)  # 1→4

arrow(300, 470, 300, 510)  # 2→3
arrow(780, 470, 780, 510)
arrow(1500, 470, 2000, 600)  # 2→4

arrow(1700, 1150, 1700, 1190)  # 3→5
arrow(2160, 1150, 2160, 1190)

arrow(300, 1360, 300, 1450)  # 5→6
arrow(780, 1360, 780, 1450)
arrow(1380, 1360, 1380, 1450)
arrow(1980, 1360, 1980, 1450)

# LLM 调用箭头
arrow(1820, 660, 2000, 660)
arrow(1820, 960, 2000, 960)
arrow(1820, 1120, 2000, 1080)

img.save(str(OUT), "PNG", optimize=True)
print(f"[ok] 已生成: {OUT}")
print(f"  尺寸: {W}x{H}")
print(f"  大小: {OUT.stat().st_size // 1024} KB")
