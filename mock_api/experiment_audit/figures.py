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
from pathlib import Path
from typing import Any

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
    # 收集贴近图像左缘（x < 15% 宽）的近似垂直线段
    verticals: list[tuple[int, int]] = []  # (y_start, y_end)
    for x1, y1, x2, y2 in lines[:, 0]:
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
# 语义部分：Qwen3-VL（fail-open）
# ---------------------------------------------------------------------------
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

        vision_url = get_settings().vision_http_url
        if not vision_url:
            return ""
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        payload = {
            "model": "qwen3-vl",
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
        url = f"{vision_url.rstrip('/')}/v1/chat/completions"
        with vram_guard("vision"):
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - VLM 不可用属正常降级
        logger.warning("[audit] Qwen3-VL 语义审计失败: %s", exc)
        return ""


def _figure_id(fig: PaperFigure) -> str:
    return (
        f"Figure {fig.figure_number}"
        if fig.figure_number
        else f"Figure(p{fig.page}#{fig.figure_index})"
    )


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

        # ── 确定性：OpenCV 断轴 / 子图尺度 ──
        img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
        cv_risks: list[dict] = []
        if img_path and img_path.exists():
            for detector in (detect_axis_line_gaps, detect_panel_scale_inconsistency):
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
