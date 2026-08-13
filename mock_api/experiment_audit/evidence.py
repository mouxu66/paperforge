"""双引擎交叉验证 + 可视化证据标注（指南第 10 周）。

- cross_validate_axis：OpenCV 确定性结果 与 Qwen3-VL 语义文本 交叉比对，
  不一致时**优先信 OpenCV**（像素测量不会错），只把 VLM 提示作为低置信线索。
- annotate_axis_evidence：在原始图上画标注框，生成可引用的证据图。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# VLM 文本中表示「y 轴从 0 开始/连续轴」的线索（与 OpenCV broken_axis 冲突）
_VLM_CONTINUOUS_AXIS_HINTS = ("从 0 开始", "从0开始", "starts from 0", "y 轴从 0")
# VLM 文本中的风险关键词（无 OpenCV 佐证时的低置信提示）
_VLM_RISK_HINTS = ("断轴", "尺度不一致", "尺度不统一", "截断", "不从 0 开始", "误导")


def cross_validate_axis(cv_risk: dict[str, Any] | None, vlm_text: str) -> dict[str, Any] | None:
    """OpenCV 与 VLM 交叉比对，返回冲突/提示描述 dict 或 None。

    判定：
    - cv_risk 有断轴/尺度风险 且 VLM 文本声称轴连续 → conflict，信 OpenCV。
    - cv_risk 有风险且 VLM 无相反陈述 → agree（OpenCV 高置信，无需额外动作）。
    - cv_risk 无风险但 VLM 命中风险词 → vlm_only 低置信提示。
    """
    text = vlm_text or ""
    if not cv_risk and not text:
        return None

    if cv_risk:
        risk = cv_risk.get("risk")
        if risk == "broken_axis" and any(h in text for h in _VLM_CONTINUOUS_AXIS_HINTS):
            return {
                "conflict": True,
                "trust": "opencv",
                "detail": "OpenCV 检出断轴，VLM 却称轴从 0 连续 → 优先信 OpenCV，需人工看原图",
            }
        return {
            "conflict": False,
            "trust": "opencv",
            "detail": f"OpenCV 确定性检出 {risk}，VLM 未反驳",
        }

    if any(h in text for h in _VLM_RISK_HINTS):
        return {
            "conflict": False,
            "trust": "vlm_only",
            "detail": "仅 VLM 提示坐标轴风险，无 OpenCV 确定性佐证，置信度低",
        }
    return None


def annotate_axis_evidence(
    image_path: str, cv_risk: dict[str, Any] | None, out_path: str
) -> str | None:
    """在原始图上标注风险区域，生成证据 PNG。成功返回 out_path，失败返回 None。

    断轴 → 红框标注左缘轴线区域；子图尺度不一致 → 黄框标注面板外接框。
    无 cv_risk 时不画框，仅原样复制（供 VLM-only 证据用）。
    """
    if cv_risk is None:
        return None
    try:
        import cv2
    except ImportError:
        logger.warning("OpenCV 未安装，证据图生成跳过")
        return None

    img = cv2.imread(str(image_path))
    if img is None:
        return None
    h, w = img.shape[:2]

    risk = cv_risk.get("risk")
    color = (0, 0, 255) if risk == "broken_axis" else (0, 255, 255)
    bbox = cv_risk.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        x0, y0, x1, y1 = [int(round(v)) for v in bbox]
        x0 = max(0, min(x0, w - 1))
        y0 = max(0, min(y0, h - 1))
        x1 = max(x0 + 1, min(x1, w - 1))
        y1 = max(y0 + 1, min(y1, h - 1))
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 3)

    label = cv_risk.get("risk", "risk")[:40]
    cv2.putText(
        img,
        f"audit: {label}",
        (8, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), img):
        return None
    return str(out_path)
