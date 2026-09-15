"""P0-4 图表坐标轴审计（OpenCV 确定性部分 + Qwen3-VL 语义部分）。

分工（指南 Q1）：
- OpenCV（CPU，毫秒级）：断轴检测（轴线大间隙）、子图尺度一致性。
- axis_info（PaperFigure 已有结构化刻度）：y 轴截断判定（y_min > 0）。
- Qwen3-VL（GPU，按需）：无语义信息时的图注匹配/风险清单兜底，
  走 vision_http_url + vram_guard("vision")，不可用时仅出确定性结论。

所有 Finding 类型 CHART_AXIS_RISK（severity=low），needs_human_review=True。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..pdf_parser import _get_uploads_dir
from .evidence import annotate_axis_evidence, cross_validate_axis
from .schemas import make_finding

logger = logging.getLogger(__name__)

# 语义风险关键词（VLM 回复命中任一 → 产出 Finding）
_VLM_RISK_KEYWORDS = (
    "不从 0 开始",
    "不从0开始",
    "尺度不一致",
    "尺度不统一",
    "断轴",
    "图例缺失",
    "单位缺失",
    "误导",
)

_AXIS_AUDIT_PROMPT = """请分析这张论文实验图：
1. 图表类型（柱状图/折线图/散点图/...）
2. x 轴和 y 轴分别表示什么
3. 图中展示了哪些方法/模型
4. 主要趋势是什么
5. 图注说的是 "{caption}"，图中内容是否匹配？

如果存在以下风险请明确指出：
- y 轴不从 0 开始
- 不同子图尺度不一致
- 图例缺失或颜色冲突
- 单位缺失
"""

# 云端 GLM 专用：结构化输出 + OCR 锚点交叉验证（A+B 方案）。
# 与本地 _AXIS_AUDIT_PROMPT 解耦：要求 JSON、把 OCR 当锚点、要求列出轴刻度数值。
_GLM_AXIS_AUDIT_PROMPT = """分析这张论文实验图，严格只输出一个 JSON 对象（不要任何解释文字）：

{{
  "chart_type": "图表类型(柱状图/折线图/散点图/箱线图/矩阵图/流程图/无图表)",
  "x_axis": "x 轴含义(无则空串)",
  "y_axis": "y 轴含义(无则空串)",
  "tick_values": ["从图像中读到的所有轴刻度数值(字符串, 如 '0','1M','-0.5','9:49')，没有则空数组"],
  "printed_numbers": ["图中印刷的关键数字(非坐标轴, 如节点数/百分比/年份), 没有则空数组"],
  "trend": "主要趋势(一短句)",
  "caption_match": true/false,
  "ocr_matched": true/false
}}

关于图中「可见印刷文字(OCR 结果)」：
{ocr_block}
这是机器对图中文字的识别，可能含错。请核对图像后采用图像读数；若图像中读到的数字与下方 OCR 一致，置 ocr_matched=true，否则 false。
图注说的是：{caption}
"""


# ---------------------------------------------------------------------------
# 确定性部分 1：axis_info 截断判定（无需 OpenCV）
# ---------------------------------------------------------------------------
def check_truncated_y_axis(axis_info: dict[str, Any] | None) -> dict[str, Any] | None:
    """y 轴刻度最小值 > 0 → 截断风险。返回描述 dict 或 None。"""
    if not axis_info:
        return None
    y_ticks = axis_info.get("y_ticks") or []
    if not y_ticks:
        return None
    y_min = min(float(v) for v in y_ticks)
    if y_min > 0:
        return {
            "risk": "truncated_y_axis",
            "y_min": y_min,
            "y_max": max(float(v) for v in y_ticks),
            "description": f"y 轴从 {y_min:g} 开始而非 0，可能放大微小差异",
        }
    return None


# ---------------------------------------------------------------------------
# 确定性部分 2：OpenCV 轴线检测（断轴 / 子图尺度）
# ---------------------------------------------------------------------------
def opencv_available() -> bool:
    """OpenCV 是否可导入（断轴/子图尺度确定性检测的硬依赖）。

    供编排层在运行前探测：缺失时显式记 skipped，而非静默返回 ok/0 发现。
    """
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def _load_cv2():
    """懒加载 OpenCV；未安装返回 None（对应检测跳过）。"""
    try:
        import cv2

        return cv2
    except ImportError:
        logger.warning("opencv 未安装，P0-4 确定性图像检测跳过")
        return None


def detect_axis_line_gaps(image_path: str) -> dict[str, Any] | None:
    """断轴检测：左侧 y 轴线若被大间隙分割成多段 → broken_axis。

    返回 {"risk": "broken_axis", "segments": n, "description": ...} 或 None。
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape[:2]
    edges = cv2.Canny(img, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        math.pi / 180,
        threshold=max(h // 8, 30),
        minLineLength=h // 6,
        maxLineGap=3,
    )
    if lines is None:
        return None
    # 归一化为 (N, 4)：不同 cv2/numpy 版本 HoughLinesP 返回 (N,1,4) 或 (N,4)，
    # 直接用 lines[:, 0] 在 (N,4) 形状下会迭代出 numpy.int32 标量导致解包崩溃。
    segs = np.asarray(lines).reshape(-1, 4)
    # 收集贴近图像左缘（x < 15% 宽）的近似垂直线段
    verticals: list[tuple[int, int]] = []  # (y_start, y_end)
    for x1, y1, x2, y2 in segs:
        if abs(x1 - x2) <= 2 and x1 < w * 0.15 and abs(y1 - y2) >= h // 12:
            verticals.append((min(y1, y2), max(y1, y2)))
    if not verticals:
        return None
    verticals.sort()
    # 合并重叠段，统计间隙 > 图像高度 8% 的分裂
    merged: list[list[int]] = []
    for ys, ye in verticals:
        if merged and ys <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], ye)
        else:
            merged.append([ys, ye])
    gaps = [
        merged[i + 1][0] - merged[i][1]
        for i in range(len(merged) - 1)
        if merged[i + 1][0] - merged[i][1] > h * 0.08
    ]
    if len(merged) >= 2 and gaps:
        min_y = min(s[0] for s in merged)
        max_y = max(s[1] for s in merged)
        return {
            "risk": "broken_axis",
            "segments": len(merged),
            "description": f"y 轴检测到 {len(merged)} 段分离轴线（间隙 {max(gaps)}px），疑似断轴",
            "bbox": [0.0, float(min_y), float(w) * 0.15, float(max_y)],
        }
    return None


def detect_panel_scale_inconsistency(image_path: str) -> dict[str, Any] | None:
    """子图尺度一致性：多个大面积矩形面板高度差 > 25% → 风险。"""
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape[:2]
    edges = cv2.Canny(img, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    panels: list[tuple[int, int, int, int]] = []  # (x, y, w, h)
    min_area = (h * w) * 0.02
    for c in contours:
        x, y, pw, ph = cv2.boundingRect(c)
        if pw * ph >= min_area and 0.15 < pw / max(ph, 1) < 6:
            panels.append((x, y, pw, ph))
    if len(panels) < 2:
        return None
    # 只比较近似同行排列的面板（高度排序后极差）
    heights = sorted(p[3] for p in panels)
    lo, hi = heights[0], heights[-1]
    if hi > 0 and (hi - lo) / hi > 0.25 and len(panels) >= 2:
        x0 = min(p[0] for p in panels)
        y0 = min(p[1] for p in panels)
        x1 = max(p[0] + p[2] for p in panels)
        y1 = max(p[1] + p[3] for p in panels)
        return {
            "risk": "panel_scale_inconsistency",
            "description": f"检测到 {len(panels)} 个子图面板，高度差 {(hi - lo) / hi * 100:.0f}%，尺度可能不一致",
            "bbox": [float(x0), float(y0), float(x1), float(y1)],
        }
    return None


# ---------------------------------------------------------------------------
# 确定性部分 3：子图分割（split_subplots）
# ---------------------------------------------------------------------------
def split_subplots(image_path: str) -> list[dict[str, Any]]:
    """多面板分割：检测并分割图中的多个子图区域。

    返回 [{"bbox": [x, y, w, h], "area_ratio": float}, ...] 或空列表。
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return []
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []
    h, w = img.shape[:2]
    edges = cv2.Canny(img, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    panels: list[dict[str, Any]] = []
    min_area = (h * w) * 0.03  # 至少占图像 3%
    max_area = (h * w) * 0.45  # 最多占图像 45%（避免把整个图当子图）
    for c in contours:
        x, y, pw, ph = cv2.boundingRect(c)
        area = pw * ph
        if min_area <= area <= max_area and 0.2 < pw / max(ph, 1) < 5:
            panels.append(
                {
                    "bbox": [float(x), float(y), float(pw), float(ph)],
                    "area_ratio": round(area / (h * w), 4),
                }
            )
    # 合并重叠区域
    panels = _merge_overlapping_panels(panels)
    return panels


def _merge_overlapping_panels(
    panels: list[dict[str, Any]], iou_threshold: float = 0.3
) -> list[dict[str, Any]]:
    """合并重叠度高的面板区域。"""
    if len(panels) <= 1:
        return panels
    merged = []
    used = set()
    for i, p1 in enumerate(panels):
        if i in used:
            continue
        bbox1 = p1["bbox"]
        for j, p2 in enumerate(panels):
            if j <= i or j in used:
                continue
            bbox2 = p2["bbox"]
            if _iou(bbox1, bbox2) > iou_threshold:
                # 合并两个 bbox
                x = min(bbox1[0], bbox2[0])
                y = min(bbox1[1], bbox2[1])
                x2 = max(bbox1[0] + bbox1[2], bbox2[0] + bbox2[2])
                y2 = max(bbox1[1] + bbox1[3], bbox2[1] + bbox2[3])
                bbox1 = [x, y, x2 - x, y2 - y]
                used.add(j)
        merged.append({"bbox": bbox1, "area_ratio": p1["area_ratio"]})
    return merged


def _iou(bbox1: list[float], bbox2: list[float]) -> float:
    """计算两个 bbox 的 IoU（交并比）。"""
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[0] + bbox1[2], bbox2[0] + bbox2[2])
    y2 = min(bbox1[1] + bbox1[3], bbox2[1] + bbox2[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area1 = bbox1[2] * bbox1[3]
    area2 = bbox2[2] * bbox2[3]
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# 确定性部分 4：图例颜色冲突检测（detect_legend_color_clash）
# ---------------------------------------------------------------------------
def detect_legend_color_clash(image_path: str) -> dict[str, Any] | None:
    """图例颜色冲突检测：图例区域中颜色过于相似 → 难以区分。

    返回 {"risk": "legend_color_clash", "similar_pairs": [...], "description": ...} 或 None。
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    img = cv2.imread(image_path)
    if img is None:
        return None
    h, w = img.shape[:2]
    # 图例通常在右上角或底部，先尝试右上角 30% 区域
    legend_regions = [
        img[0 : int(h * 0.3), int(w * 0.6) : w],  # 右上角
        img[int(h * 0.7) : h, 0:w],  # 底部
    ]
    for region in legend_regions:
        if region.size == 0:
            continue
        colors = _extract_legend_colors(region)
        if len(colors) < 2:
            continue
        similar_pairs = _find_similar_colors(colors)
        if similar_pairs:
            return {
                "risk": "legend_color_clash",
                "similar_pairs": similar_pairs,
                "description": f"图例中检测到 {len(similar_pairs)} 对颜色过于相似，可能难以区分",
            }
    return None


def _extract_legend_colors(region: Any, n_colors: int = 10) -> list[tuple[int, int, int]]:
    """从图例区域提取主要颜色（使用 k-means 聚类）。"""
    cv2 = _load_cv2()
    if cv2 is None:
        return []
    pixels = region.reshape(-1, 3).astype(np.float32) if hasattr(region, "reshape") else []
    if len(pixels) == 0:
        return []
    # 过滤白色/黑色/灰色背景
    mask = ~((pixels[:, 0] > 230) & (pixels[:, 1] > 230) & (pixels[:, 2] > 230))
    mask &= ~((pixels[:, 0] < 25) & (pixels[:, 1] < 25) & (pixels[:, 2] < 25))
    # 过滤灰色（RGB 三通道差异小）
    mask &= ~((np.max(pixels, axis=1) - np.min(pixels, axis=1)) < 30)
    filtered = pixels[mask]
    if len(filtered) < 10:
        return []
    # 简单采样取众数颜色
    step = max(1, len(filtered) // n_colors)
    colors = [tuple(int(c) for c in filtered[i]) for i in range(0, len(filtered), step)][:n_colors]
    return colors


def _find_similar_colors(colors: list[tuple[int, int, int]], threshold: float = 30.0) -> list[dict]:
    """找出颜色距离小于阈值的配对。"""
    similar = []
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            dist = _color_distance(colors[i], colors[j])
            if dist < threshold:
                similar.append(
                    {
                        "color1": list(colors[i]),
                        "color2": list(colors[j]),
                        "distance": round(dist, 1),
                    }
                )
    return similar


def _color_distance(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    """计算 RGB 颜色空间的欧氏距离。"""
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


# ---------------------------------------------------------------------------
# 确定性部分 5：曲线/柱状/散点区域定位（locate_plot_elements）
# ---------------------------------------------------------------------------
def locate_plot_elements(image_path: str) -> dict[str, Any]:
    """定位图中的曲线、柱状、散点区域。

    返回 {"has_lines": bool, "has_bars": bool, "has_scatter": bool, "element_count": int}。
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return {"has_lines": False, "has_bars": False, "has_scatter": False, "element_count": 0}
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"has_lines": False, "has_bars": False, "has_scatter": False, "element_count": 0}
    h, w = img.shape[:2]
    edges = cv2.Canny(img, 50, 150)
    # 检测直线（曲线图的特征）
    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, threshold=50, minLineLength=30, maxLineGap=5)
    has_lines = lines is not None and len(lines) > 5
    # 检测矩形（柱状图的特征）
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bar_count = 0
    for c in contours:
        x, y, pw, ph = cv2.boundingRect(c)
        aspect = ph / max(pw, 1)
        area = pw * ph
        if 0.5 < aspect < 10 and area > (h * w) * 0.005:
            bar_count += 1
    has_bars = bar_count >= 3
    # 检测圆形/椭圆形（散点图的特征）
    circles = cv2.HoughCircles(
        edges, cv2.HOUGH_GRADIENT, dp=1, minDist=10, param1=50, param2=30, minRadius=3, maxRadius=15
    )
    has_scatter = circles is not None and len(circles[0]) > 5
    element_count = (len(lines) if lines else 0) + bar_count + (len(circles[0]) if circles else 0)
    return {
        "has_lines": has_lines,
        "has_bars": has_bars,
        "has_scatter": has_scatter,
        "element_count": element_count,
    }


def analyze_figure_semantic(image_path: str, caption: str = "", timeout: int = 120) -> str:
    """Qwen3-VL 图表语义分析；不可用/失败返回空串。"""
    path = Path(image_path)
    if not path.exists():
        return ""
    try:
        import base64

        import requests

        from ..settings import get_settings
        from ..vram_scheduler import vram_guard

        st = get_settings()
        vision_url = st.vision_http_url
        if not vision_url:
            return ""
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        payload = {
            "model": st.vision_model or "qwen3-vl",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": _AXIS_AUDIT_PROMPT.format(caption=caption[:300])},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if st.vision_api_key:
            headers["Authorization"] = f"Bearer {st.vision_api_key}"
        url = f"{vision_url.rstrip('/')}/v1/chat/completions"
        with vram_guard("vision"):
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - VLM 不可用属正常降级
        logger.warning("[audit] Qwen3-VL 语义审计失败: %s", exc)
        return ""


# ── 独立云端视觉后端：智谱 GLM 免费视觉（与本地 Qwen3-VL 解耦）────────────
# 关键差异：云端推理不占本地显存 → 不走 vram_guard("vision")、不参与同卡互斥。
# 速率约束靠信号量 + 指数退避（实测 glm-4v-flash 并发≤8 无 429，thinking 版≤4 稳）。
_GLM_SEM: threading.Semaphore | None = None


def _glm_sem(st) -> threading.Semaphore:
    """懒初始化并发信号量（按配置 max_concur）。"""
    global _GLM_SEM
    if _GLM_SEM is None:
        _GLM_SEM = threading.Semaphore(max(1, st.glm_vision_max_concur or 4))
    return _GLM_SEM


def analyze_figure_semantic_glm(
    image_path: str,
    caption: str = "",
    ocr_text: str = "",
    timeout: int = 120,
    model: str | None = None,
    focus_hint: str = "",
) -> str:
    """云端 GLM 免费视觉图表语义分析；不可用/失败返回空串。

    结构化输出（JSON），并把 ocr_text 作为锚点喂入（A+B 方案核心）：
    模型核对图像读数 vs OCR，冲突时以图像为准并在 ocr_matched 标注。

    与 analyze_figure_semantic（本地 Qwen3-VL）完全独立：云端端点、/v4 路径、
    带指数退避重试与并发信号量，不占本地显存。由 PAPERFORGE_GLM_VISION_ENABLED 切换。
    返回：成功时为 JSON 字符串（含 tick_values/printed_numbers/ocr_matched 等）；
          失败/关闭时为 ""（调用方按老行为降级）。
    """
    path = Path(image_path)
    if not path.exists():
        return ""
    try:
        import base64
        import time

        import requests

        from ..settings import get_settings

        st = get_settings()
        if not st.glm_vision_enabled or not st.glm_vision_api_key:
            return ""
        provider = (st.glm_vision_provider or "glm").lower()
        # provider 决定默认端点/模型（显式配置优先于回落默认）
        if provider == "agnes":
            default_base = "https://apihub.agnes-ai.com/v1"
            default_model = "agnes-2.5-flash"
            # Agnes Free 计划 20 RPM → 单请求节流 ~3s，避免 429 拖垮批量审计
            min_interval = 3.0
        else:  # glm（默认）
            default_base = "https://open.bigmodel.cn/api/paas/v4"
            default_model = "glm-4v-flash"
            min_interval = 0.0
        ocr_block = (
            "OCR 识别到的图中文字：\n" + ocr_text.strip()
            if ocr_text and ocr_text.strip()
            else "（无 OCR 文本，请仅依据图像读数）"
        )
        prompt = _GLM_AXIS_AUDIT_PROMPT.format(ocr_block=ocr_block, caption=(caption or "")[:300])
        if focus_hint and focus_hint.strip():
            prompt = (
                prompt.rstrip()
                + "\n\n【本次重点聚焦】"
                + focus_hint.strip()
                + "（只围绕该重点给出 tick_values/printed_numbers/risk_flags，其余从简）"
            )
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        payload = {
            "model": model or st.glm_vision_model or default_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "stream": False,
        }
        # response_format=json_object 是 GLM 专属能力；Agnes 不支持/返回空，故仅 glm 强制，
        # agnes 改为在 prompt 内要求纯 JSON（见 _GLM_AXIS_AUDIT_PROMPT），返回由 _parse_glm_json 容错。
        if provider == "glm":
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {st.glm_vision_api_key}",
        }
        base_url = st.glm_vision_base_url or default_base
        url = f"{base_url.rstrip('/')}/chat/completions"
        # 指数退避：覆盖 429/1302(用户限速)/1305(平台过载)
        last_err = ""
        for attempt in range(4):
            # 单请求节流（Agnes 20 RPM）： aquí 进入临界区前先等足最小间隔
            with _glm_sem(st):
                if min_interval > 0:
                    # 单请求节流（Agnes 20 RPM）：进入临界区前先等足最小间隔
                    time.sleep(min_interval)
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                except Exception as exc:  # noqa: BLE001
                    last_err = repr(exc)
                    time.sleep(min(2**attempt * 1.5, 12))
                    continue
                if resp.status_code == 200:
                    content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
                    if content:
                        return content
                    # 200 但空 content：agnes 偶发抽风（不支持 json_object 时的退化），
                    # 微扰 temperature 重试一次；glm 不应出现空，直接降级。
                    if provider == "agnes" and attempt < 1:
                        payload["temperature"] = 0.3
                        last_err = "agnes 返回空 content，已微扰重试"
                        time.sleep(min_interval + 1.0)
                        continue
                    return ""
                if resp.status_code in (429, 502, 503, 504):
                    time.sleep(min(2**attempt * 2.0, 16))
                    continue
                break  # 非限流错误（如鉴权失败）不重试
        logger.warning(
            "[audit] 云端视觉语义审计失败（provider=%s，已退避重试）: %s", provider, last_err
        )
        return ""
    except Exception as exc:  # noqa: BLE001 - 云端不可用属正常降级
        logger.warning("[audit] GLM 云端视觉语义审计异常: %s", exc)
        return ""


def _figure_id(fig: PaperFigure) -> str:
    return (
        f"Figure {fig.figure_number}"
        if fig.figure_number
        else f"Figure(p{fig.page}#{fig.figure_index})"
    )


# 数值抽取：整数/小数/百分/时间比例(9:49)。用于 OCR×GLM 数字级交叉验证。
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?\s?%?|:\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?")


def _extract_numbers(text: str) -> set[str]:
    if not text:
        return set()
    return {m.group(0).strip() for m in _NUM_RE.finditer(text)}


def _parse_glm_json(raw: str) -> dict:
    """从 GLM 结构化输出解析 JSON；容错（模型可能多包 ```json、前后文字、或把对象包成数组）。

    thinking 模型常把结果包成 ``[{...}]`` 数组；这里取首个 dict 元素。
    解析失败一律退回 ``{}``，让上游按"信息不足、不误报"处理。
    """
    if not raw:
        return {}
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
    txt = txt.strip()
    import json as _json

    try:
        data = _json.loads(txt)
    except Exception:  # noqa: BLE001
        # 退化：取第一个 { 到最后一个 }（容忍前后杂质文本）
        s, e = txt.find("{"), txt.rfind("}")
        if s >= 0 and e > s:
            try:
                data = _json.loads(txt[s : e + 1])
            except Exception:  # noqa: BLE001
                return {}
        else:
            return {}
    # 兼容 thinking 模型返回 [{...}] 数组：取首个 dict 元素
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                return item
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _norm_num(s: str) -> float | None:
    """数字串归一为 float 用于比对；去 %、近零归零（消除 -0↔0 噪声）、非法返回 None。"""
    s = (s or "").strip().rstrip("%").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if abs(f) < 1e-9:  # -0 / 0 / 0.0000001 视为同一点
        return 0.0
    return f


def _collect_glm_numbers(glm_json: object, glm_raw: str = "") -> set[str]:
    """从 GLM 结构化输出递归抽取数字串（兼容 flash/thinking 任意字段名与数组包装）。

    不依赖特定 key（tick_values/printed_numbers），递归扫描所有字符串/数值；
    结构化取不到时回落到原始文本 glm_raw，保证 A+B 覆盖不漏触发。
    """
    out: set[str] = set()

    def walk(o: object) -> None:
        nonlocal out
        if isinstance(o, str):
            out |= _extract_numbers(o)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            out.add(str(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(glm_json)
    if not out and glm_raw:
        out |= _extract_numbers(glm_raw)
    return out


def _compare_ocr_vs_glm(glm_json: dict, ocr_text: str, glm_raw: str = "") -> dict | None:
    """A+B 核心：OCR 数字 × GLM 视觉读数 交叉验证（flash/thinking 通用）。

    增强点（相较早期仅 flash 版）：
    - 字段名无关：递归扫描 JSON 任意字段，兼容 thinking 的数组包裹与自由命名。
    - 数字归一比对：转 float 后比对，消除 -0↔0 符号噪声；年份(1900-2099)剔除。
    - 文本兜底：结构化字段无数字时回落 GLM 原始输出文本抽数，保证 A+B 覆盖。
    - ocr_matched==true 视为一致，不报冲突。

    返回冲突/一致 dict 或 None（无 OCR / 无可比数字 / 信息不足）。
    """
    ocr = (ocr_text or "").strip()
    if not ocr:
        return None
    # 锚点净化：剔除年份(1900-2099)这类明显非轴刻度数字，避免误冲突。
    _YEAR = re.compile(r"^(19|20)\d{2}$")
    ocr_nums = {n for n in _extract_numbers(ocr) if not _YEAR.match(n.replace("%", ""))}
    if not ocr_nums:
        return None
    glm_nums = _collect_glm_numbers(glm_json, glm_raw)
    if not glm_nums:
        return None
    matched = glm_json.get("ocr_matched") if isinstance(glm_json, dict) else None
    # 归一集合比对（去符号歧义对 0 的干扰）
    ocr_set = {_norm_num(n) for n in ocr_nums}
    glm_set = {_norm_num(n) for n in glm_nums}
    missing_norm = ocr_set - glm_set  # OCR 有、GLM 没读到（归一后）
    extra_norm = glm_set - ocr_set
    missing_raw = [n for n in ocr_nums if _norm_num(n) in missing_norm]  # 用于展示的原串
    extra_raw = [n for n in glm_nums if _norm_num(n) in extra_norm]
    if missing_norm and matched is not True:
        return {
            "conflict": True,
            "trust": "ocr",
            "ocr_matched": bool(matched),
            "ocr_numbers": sorted(ocr_nums),
            "glm_numbers": sorted(glm_nums),
            "missing_in_glm": sorted(missing_raw),
            "detail": (
                f"OCR 锚点检出数字 {sorted(missing_raw)}，GLM 视觉未读到（或符号/精度不一致）→ "
                f"疑似 GLM 漏读/误读，优先信 OCR 锚点，需人工核对"
            ),
        }
    return {
        "conflict": False,
        "trust": "glm+ocr",
        "ocr_matched": bool(matched),
        "ocr_numbers": sorted(ocr_nums),
        "glm_numbers": sorted(glm_nums),
        "extra_in_glm": sorted(extra_raw),
        "detail": "OCR 数字与 GLM 视觉读数一致，双源互相印证",
    }


def check_figure_axis_risks(db: Session, paper_id: str, *, allow_vlm: bool = True) -> list[dict]:
    """P0-4 入口：对论文所有 PaperFigure 做坐标轴审计。

    无 figure 记录时返回空（上游 figure 抽取未完成，不误报）。
    """
    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page, PaperFigure.figure_index)
        .all()
    )
    if not figs:
        return []

    uploads = _get_uploads_dir() / "figures" / paper_id
    findings: list[dict] = []
    for fig in figs:
        fid = _figure_id(fig)
        evidence = [{"type": "figure", "figure_id": fid, "page": fig.page}]

        # ── 确定性：axis_info 截断 ──
        axis_info = fig.axis_info if isinstance(fig.axis_info, dict) else None
        trunc = check_truncated_y_axis(axis_info)
        if trunc:
            findings.append(
                make_finding(
                    "CHART_AXIS_RISK",
                    title=f"{fid} y 轴截断",
                    page=fig.page,
                    claim=trunc["description"],
                    computed=f"y_ticks 范围 [{trunc['y_min']:g}, {trunc['y_max']:g}]",
                    method="axis_info.y_ticks 最小值 > 0",
                    evidence_sources=evidence,
                    normal_explanation=(
                        "当所有方法数值都集中在窄区间时，截断轴是常见做法；"
                        "是否误导需结合差异幅度人工判断"
                    ),
                    needs_human_review=True,
                )
            )

        # ── 确定性：OpenCV 断轴 / 子图尺度 / 图例颜色冲突 ──
        img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
        cv_risks: list[dict] = []
        if img_path and img_path.exists():
            for detector in (
                detect_axis_line_gaps,
                detect_panel_scale_inconsistency,
                detect_legend_color_clash,
            ):
                risk = detector(str(img_path))
                if risk:
                    cv_risks.append(risk)
                    ev = list(evidence)
                    ann = annotate_axis_evidence(
                        str(img_path),
                        risk,
                        str(
                            uploads / "_audit" / f"{Path(fig.figure_path).stem}_{risk['risk']}.png"
                        ),
                    )
                    if ann:
                        ev.append(
                            {"type": "figure", "figure_id": fid, "page": fig.page, "snippet": ann}
                        )
                    findings.append(
                        make_finding(
                            "CHART_AXIS_RISK",
                            title=f"{fid} {risk['risk']}",
                            page=fig.page,
                            claim=risk["description"],
                            method="OpenCV Canny + HoughLinesP/contour 确定性检测",
                            evidence_sources=ev,
                            normal_explanation=(
                                "检测基于几何启发式，可能把多面板布局误判为断轴，请人工核对原图"
                            ),
                            needs_human_review=True,
                        )
                    )

        # ── 语义兜底：无 axis_info 时问 VLM ──
        if axis_info is None and allow_vlm and img_path and img_path.exists():
            # 云端 GLM 免费视觉（独立开关，默认关）；否则走本地 Qwen3-VL。
            from ..settings import get_settings

            st = get_settings()
            if st.glm_vision_enabled and st.glm_vision_api_key:
                # A+B：把 OCR 当锚点喂入，要求结构化 JSON 输出。
                response = analyze_figure_semantic_glm(
                    str(img_path), fig.caption_text or "", ocr_text=fig.ocr_text or ""
                )
                glm_json = _parse_glm_json(response)
                # ── A+B 核心：OCR × GLM 数字交叉验证 ──
                # 传入原始 response 作文本兜底：thinking 模型结构化字段取不到数字时回落文本。
                ocr_cross = _compare_ocr_vs_glm(glm_json, fig.ocr_text or "", glm_raw=response)
                if ocr_cross and ocr_cross.get("conflict"):
                    findings.append(
                        make_finding(
                            "CHART_AXIS_RISK",
                            title=f"{fid} OCR×GLM 数字冲突（OCR 优先）",
                            page=fig.page,
                            claim=ocr_cross["detail"],
                            computed=(
                                f"OCR数字={ocr_cross['ocr_numbers']}；"
                                f"GLM数字={ocr_cross['glm_numbers']}；"
                                f"GLM漏读={ocr_cross['missing_in_glm']}"
                            ),
                            method="PaddleOCR 锚点 × 云端 GLM 视觉读数交叉验证（A+B）",
                            evidence_sources=evidence,
                            normal_explanation=(
                                "专用 OCR 在印刷数字上通常比通用 VLM 可靠；"
                                "冲突处为误读高发区，需人工核对原图"
                            ),
                            needs_human_review=True,
                        )
                    )
                elif ocr_cross and not ocr_cross.get("conflict"):
                    # 一致：双源印证，仅记一条低噪音提示（不强制人工复核）。
                    findings.append(
                        make_finding(
                            "CHART_AXIS_RISK",
                            title=f"{fid} OCR×GLM 数字一致",
                            page=fig.page,
                            claim=ocr_cross["detail"],
                            computed=(
                                f"OCR数字={ocr_cross['ocr_numbers']}；"
                                f"GLM数字={ocr_cross['glm_numbers']}"
                            ),
                            method="PaddleOCR 锚点 × 云端 GLM 视觉读数交叉验证（A+B）",
                            evidence_sources=evidence,
                            normal_explanation="OCR 与 GLM 读数相互印证，置信提升",
                            needs_human_review=False,
                        )
                    )
                elif glm_json:
                    # 无 OCR 或无可比数字：退化为纯 GLM 语义提示。
                    if any(k in response for k in _VLM_RISK_KEYWORDS):
                        findings.append(
                            make_finding(
                                "CHART_AXIS_RISK",
                                title=f"{fid} VLM 提示坐标轴风险",
                                page=fig.page,
                                claim=response[:500],
                                method="云端 GLM 视觉语义审计（无 OCR 锚点）",
                                evidence_sources=evidence,
                                normal_explanation="GLM 判断为启发式信号，置信度低于确定性检测",
                                needs_human_review=True,
                            )
                        )
            else:
                response = analyze_figure_semantic(str(img_path), fig.caption_text or "")
                cross = cross_validate_axis(cv_risks[-1] if cv_risks else None, response)
                if cross and cross.get("conflict"):
                    findings.append(
                        make_finding(
                            "CHART_AXIS_RISK",
                            title=f"{fid} 双引擎冲突（OpenCV 优先）",
                            page=fig.page,
                            claim=cross["detail"],
                            computed=f"OpenCV: {cv_risks[-1]['risk']}；VLM: {response[:200]}",
                            method="OpenCV 确定性结果与 Qwen3-VL 语义交叉验证",
                            evidence_sources=evidence,
                            normal_explanation="VLM 视觉理解可能误读轴刻度，像素检测更可靠",
                            needs_human_review=True,
                        )
                    )
                elif response and any(k in response for k in _VLM_RISK_KEYWORDS):
                    findings.append(
                        make_finding(
                            "CHART_AXIS_RISK",
                            title=f"{fid} VLM 提示坐标轴风险",
                            page=fig.page,
                            claim=response[:500],
                            method="Qwen3-VL 语义审计（无 axis_info 兜底路径）",
                            evidence_sources=evidence,
                            normal_explanation="VLM 判断为启发式信号，置信度低于确定性检测",
                            needs_human_review=True,
                        )
                    )
    return findings
