"""utils/text_segment.py —— v4.2 从 depth_eval_v4.py 拆分的文本分段工具。

迁移函数：
- segment_paper_text
- _join_sections
- _smart_truncate
"""

from __future__ import annotations

import logging

from ..compute_mode import get_compute_mode_config
from ..extractors.text import _extract_section, _find_section_boundaries

logger = logging.getLogger(__name__)

# 分段常量（原 depth_eval_v4）
MAX_CHARS_SHORT = 4000
MAX_CHARS_FULL = 32000
FALLBACK_CHARS = 2000


def _join_sections(*sections: str) -> str:
    parts = [s.strip() for s in sections if s.strip()]
    return "\n\n".join(parts)


def _smart_truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for sep in ["。\n", "。", "！", "？", ".\n", ". ", "!", "?"]:
        pos = truncated.rfind(sep)
        if pos > max_chars * 0.6:
            return truncated[: pos + len(sep.rstrip())]
    return truncated


def segment_paper_text(
    full_text: str, abstract: str = "", max_short: int | None = None, max_full: int | None = None
) -> dict[str, str]:
    """将论文全文切分为三个视图。

    若章节标题不标准导致分段失败，使用固定长度 fallback。
    max_short / max_full 默认从动态算力配置读取，支持手动覆盖。
    """
    if max_short is None or max_full is None:
        cfg = get_compute_mode_config()
        if max_short is None:
            max_short = int(cfg.get("max_chars_short", MAX_CHARS_SHORT))
        if max_full is None:
            max_full = int(cfg.get("max_chars_full", MAX_CHARS_FULL))
    boundaries = _find_section_boundaries(full_text)

    if abstract and len(abstract) >= 20:
        abs_text = abstract.strip()
    else:
        abs_text = _extract_section(full_text, boundaries, ["abstract", "摘要"])

    intro = _extract_section(
        full_text,
        boundaries,
        ["introduction", "intro", "引言", "绪论", "前言"],
    )
    conclusion = _extract_section(
        full_text,
        boundaries,
        [
            "conclusion",
            "conclusions",
            "concluding",
            "discussion",
            "summary",
            "结论",
            "总结",
            "讨论",
        ],
    )

    if not intro and not conclusion and len(full_text) > FALLBACK_CHARS * 2:
        logger.warning("DEPTH v4.1 文本分段失败（无标准章节标题），使用固定长度 fallback")
        paper_abstract_intro = full_text[:FALLBACK_CHARS]
        paper_abstract_conclusion = full_text[-FALLBACK_CHARS:]
    else:
        paper_abstract_intro = _smart_truncate(_join_sections(abs_text, intro), max_short)
        paper_abstract_conclusion = _smart_truncate(_join_sections(abs_text, conclusion), max_short)

    return {
        "paper_abstract_intro": paper_abstract_intro,
        "paper_full_text": full_text[:max_full],
        "paper_abstract_conclusion": paper_abstract_conclusion,
    }
