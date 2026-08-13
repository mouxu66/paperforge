"""P0-11 图内数值造假指纹审计（Qwen3-VL 数值转写 + 统计指纹）。

链路：PaperFigure 图片 → Qwen3-VL 转写数值（结构化）→ 统计指纹：

- 等差数字 / 重复数字 / 恒定偏移 / Benford 偏离（复用 depth_eval_v4 的零 LLM helper）
- 跨组重复值（同一数值出现在两个不同组/行——复制粘贴痕迹）
- 末位数字偏好（人肉随机数指纹）

所有 Finding 类型 SUSPICIOUS_DATA_PATTERN（severity=high），needs_human_review=True：
这些是「造假线索」而非定罪证据，最终由人工核对原图/原始数据确认。
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..pdf_parser import _get_uploads_dir
from .figures import _figure_id
from .schemas import make_finding

logger = logging.getLogger(__name__)

_TABLE_NUMBERS_PROMPT = """图里可能包含一张数据表或带数值的统计图。请把图中可见的所有数值逐行转写出来，每行固定格式：

组别|数值

例如：WT|0.763641。要求：
- 组别用该行/该列最左侧或上方的文字标签（如 WT、H186R、对照组、组1 等）；
- 数值照抄，精确到原图小数位，不要四舍五入、不要加单位；
- 一个数值一行，按表格从上到下、从左到右的顺序；看不清的格子跳过不写。"""


def vision_available() -> bool:
    """视觉模型端点是否已配置（P0-11 的硬依赖，缺则显式 skipped）。"""
    try:
        from ..settings import get_settings

        return bool(get_settings().vision_http_url)
    except Exception:  # noqa: BLE001 - 探测失败视同不可用
        return False


def transcribe_figure_numbers(image_path: str, caption: str = "", timeout: int = 120) -> str:
    """Qwen3-VL 转写图内数值；不可用/失败返回空串（fail-open）。"""
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
                        {"type": "text", "text": _TABLE_NUMBERS_PROMPT},
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
        logger.warning("[audit] 图内数值转写失败: %s", exc)
        return ""


# 负向 lookbehind 避免把 label 里的数字（如 H186R、Group1）误当数值
_NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _to_float(s: str) -> float | None:
    """把一格文本解析为数值；非数值/NA/空白返回 None（label 里的数字不误认）。"""
    s = s.strip()
    if not s or s.lower() in {"na", "n/a", "?", "-", "—"}:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_number_series(transcript: str) -> list[tuple[str, list[float]]]:
    """把 VLM 转写文本解析为 [(label, [vals]), ...]，按 label 聚合同名行数值。

    兼容两种格式：
    - 「label|value」逐行（_TABLE_NUMBERS_PROMPT 约定）
    - markdown 表格行「| label | v1 | v2 | ... |」
    """
    series: dict[str, list[float]] = {}
    order: list[str] = []

    def add(label: str, vals: list[float]) -> None:
        label = label.strip()
        if not label or not vals:
            return
        if label not in series:
            series[label] = []
            order.append(label)
        series[label].extend(vals)

    for raw in transcript.splitlines():
        line = raw.strip().strip("`")
        if not line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            vals = [v for v in (_to_float(c) for c in cells[1:]) if v is not None]
            add(cells[0], vals)
            continue

        # 「label|value」：先按分隔符拆，label 里含数字也不误读（如 H186R|0.8）
        m_sep = re.match(r"^(.*?)\s*[|\t,，:：]\s*(.+)$", line)
        if m_sep:
            label, rest = m_sep.group(1), m_sep.group(2)
            vals = [float(x.group(0)) for x in _NUM_RE.finditer(rest)]
            add(label, vals)
            continue

        # 无分隔符：按空白切，第一个数值出现之前的部分为 label
        label_parts: list[str] = []
        vals: list[float] = []
        for tok in line.split():
            v = _to_float(tok)
            if v is not None:
                vals.append(v)
            elif not vals:
                label_parts.append(tok)
        add(" ".join(label_parts), vals)

    return [(l, series[l]) for l in order]


def detect_cross_group_duplicates(series: list[tuple[str, list[float]]]) -> list[str]:
    """同一数值出现在 ≥2 个不同组 → 跨组复制粘贴痕迹。"""
    owners: dict[str, list[str]] = defaultdict(list)
    display: dict[str, str] = {}
    for label, vals in series:
        for v in vals:
            key = f"{v:.6f}"
            owners[key].append(label)
            display.setdefault(key, str(v))
    flags: list[str] = []
    for key, labels in owners.items():
        uniq = list(dict.fromkeys(labels))
        if len(uniq) >= 2:
            flags.append(
                f"[跨组重复] 数值 {display[key]} 同时出现在 {len(uniq)} 个不同组"
                f"（{'、'.join(uniq)}），不同组精确到 6 位小数完全相同，疑似复制粘贴"
            )
    return flags


def detect_digit_preference(values: list[float]) -> str | None:
    """末位数字分布偏离均匀（卡方 p<0.05，n≥8）→ 人肉随机数指纹。"""
    last = [str(v).split(".")[1][-1] for v in values if "." in str(v) and str(v).split(".")[1]]
    n = len(last)
    if n < 8:
        return None
    obs = Counter(last)
    exp = n / 10.0
    chi2 = sum(((obs.get(d, 0) - exp) ** 2) / exp for d in "0123456789")
    if chi2 <= 16.92:  # 9 自由度，p=0.05 临界值
        return None
    top = max("0123456789", key=lambda d: obs.get(d, 0))
    return (
        f"[末位偏好] {n} 个数值中 {obs.get(top, 0)} 个末位为 {top}"
        f"（期望≈{exp:.1f}，χ²={chi2:.1f}，p<0.05），"
        f"末位分布严重不均匀，人手编造「随机数」的典型指纹"
    )


def check_figure_number_patterns(
    db: Session, paper_id: str, *, allow_vlm: bool = True
) -> list[dict]:
    """P0-11 入口：对论文所有 PaperFigure 做「图内数值 → 统计指纹」审计。

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
        img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
        if not (img_path and img_path.exists()):
            continue
        if not allow_vlm:
            continue

        transcript = transcribe_figure_numbers(str(img_path), fig.caption_text or "")
        if not transcript:
            continue
        series = parse_number_series(transcript)
        if not series:
            continue

        all_vals = [v for _, vals in series for v in vals]

        # 复用 depth_eval_v4 的零 LLM 统计指纹（等差/重复/恒定偏移/Benford，单一事实源）
        try:
            from ..depth_eval_v4 import statistical_flags_from_series

            flags = statistical_flags_from_series(series)
        except Exception as exc:  # noqa: BLE001 - 指纹复用失败不影响本地指纹
            logger.warning("[audit] 复用统计指纹失败: %s", exc)
            flags = []
        flags += detect_cross_group_duplicates(series)
        pref = detect_digit_preference(all_vals)
        if pref:
            flags.append(pref)
        if not flags:
            continue

        fid = _figure_id(fig)
        findings.append(
            make_finding(
                "SUSPICIOUS_DATA_PATTERN",
                title=f"{fid} 图内数值呈人造数据指纹",
                page=fig.page,
                claim=flags[0],
                computed="\n".join(flags),
                method="Qwen3-VL 图内数值转写 + 等差/跨组重复/末位偏好/Benford 统计指纹",
                evidence_sources=[
                    {
                        "type": "figure",
                        "figure_id": fid,
                        "page": fig.page,
                        "snippet": transcript[:300],
                    }
                ],
                normal_explanation=(
                    "这些是统计上的造假线索而非定罪证据，需人工核对原图/原始数据；"
                    "VLM 数值转写可能有个别数字抄错，请以原文表格为准"
                ),
                needs_human_review=True,
            )
        )
    return findings
