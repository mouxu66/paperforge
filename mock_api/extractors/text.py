"""extractors/text.py —— v4.2 从 depth_eval_v4.py 拆分的文本提取函数。

迁移函数：
- _find_section_boundaries, _extract_section
- _extract_llm_fields, _strip_cot
- _extract_key_terms
- _extract_objective_features
- SECTION_PATTERNS
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..depth_eval_v4 import ObjectiveFeatures

from ..settings import get_settings

_settings = get_settings()
OIM_MIN_TEXT_CHARS = _settings.oim_min_text_chars

# ── SECTION_PATTERNS（原 depth_eval_v4 L375-380）─────────────────
SECTION_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:abstract|ABSTRACT)\s*\n", re.IGNORECASE),
    re.compile(
        r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:introduction|INTRODUCTION|1\.?\s*intro)\s*\n", re.IGNORECASE
    ),
    re.compile(
        r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:conclusion|conclusions|CONCLUDING REMARKS|"
        r"summary|discussion)\s*\n",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|\n)\s*摘\s*要\s*\n"),
    re.compile(r"(?:^|\n)\s*(?:引言|绪论|前言|1\.?\s*引)\s*\n"),
    re.compile(r"(?:^|\n)\s*(?:结论|总结|讨论|总\s*结)\s*\n"),
]


def _find_section_boundaries(text: str) -> list[tuple[int, str]]:
    boundaries: list[tuple[int, str]] = []
    for pattern in SECTION_PATTERNS:
        for match in pattern.finditer(text):
            boundaries.append((match.start(), match.group().strip()))
    boundaries.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str]] = []
    for pos, name in boundaries:
        if not deduped or pos - deduped[-1][0] > 10:
            deduped.append((pos, name))
    return deduped


def _extract_section(
    text: str, boundaries: list[tuple[int, str]], section_keywords: list[str]
) -> str:
    for i, (pos, name) in enumerate(boundaries):
        if any(kw in name.lower() for kw in section_keywords):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            return text[pos:end].strip()
    return ""


# ── LLM 输出解析 ─────────────────────────────────────────────────


def _extract_llm_fields(
    raw: str | None,
    patterns: dict[str, str],
    *,
    none_to_empty: bool = True,
) -> dict[str, str]:
    """从 LLM 原始输出中提取结构化字段。

    Args:
        raw: LLM 原始输出文本；None 时返回空字典。
        patterns: 字段名 -> 正则模式的映射。
        none_to_empty: 是否将 "none" 视为空字符串。
    """
    if raw is None:
        return {}
    data: dict[str, str] = {}
    for key, pat in patterns.items():
        m = re.search(pat, raw)
        if not m:
            continue
        val = m.group(1).strip()
        if none_to_empty and val.lower() == "none":
            val = ""
        data[key] = val
    return data


def _strip_cot(raw: str) -> str:
    """剔除 <thinking>...</thinking> 思维链块（P1-3）。"""
    if not raw:
        return raw
    return re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL | re.IGNORECASE)


# ── 关键词提取 ───────────────────────────────────────────────────


def _extract_key_terms(text: str) -> set[str]:
    """从文本中提取关键术语用于辩护-质疑匹配。

    中英文混合处理：
    - 英文：提取长度 ≥ 3 的单词（过滤停用词）
    - 中文：提取 2-gram 特征（重叠词序列）
    """
    _EN_STOP_WORDS = {
        "the",
        "and",
        "for",
        "was",
        "not",
        "has",
        "can",
        "are",
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "will",
        "also",
        "were",
    }
    terms: set[str] = set()
    en_words = re.findall(r"[a-zA-Z]{3,}", text)
    for w in en_words:
        wl = w.lower()
        if wl not in _EN_STOP_WORDS:
            terms.add(wl)
    cjk_chars = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text)
    for i in range(len(cjk_chars) - 1):
        terms.add(cjk_chars[i] + cjk_chars[i + 1])
    for ch in cjk_chars:
        terms.add(ch)
    return terms


# ── OIM 客观特征 ─────────────────────────────────────────────────


def _extract_objective_features(full_text: str) -> ObjectiveFeatures | None:
    """从论文全文中抽取 OIM 客观特征（P0-1）。

    所有特征均通过本地正则/规则派生，不依赖外部 API。
    文本长度 < OIM_MIN_TEXT_CHARS 时返回 None，调用方应退化到纯 LLM 行为。
    """
    if not full_text or len(full_text) < OIM_MIN_TEXT_CHARS:
        return None

    from ..depth_eval_v4 import ObjectiveFeatures
    from .evidence import _detect_ablation_evidence

    text = full_text.lower()

    has_ablation, _ = _detect_ablation_evidence(full_text)
    ablation = 1.0 if has_ablation else 0.0

    repro_patterns = [
        r"github\.com/\S+",
        r"gitlab\.com/\S+",
        r"bitbucket\.org/\S+",
        r"code.*available",
        r"open.source",
        r"源代码",
        r"代码已开源",
        r"dataset.*available",
        r"we release.*data",
        r"we release.*code",
        r"appendix.*\d+",
        r"supplementary material",
    ]
    repro_hits = sum(1 for pat in repro_patterns if re.search(pat, text))
    repro_signal = min(1.0, repro_hits / 3.0)

    bracket_refs = len(re.findall(r"\[\d+\]", full_text))
    paren_refs = len(re.findall(r"\([a-z\-]+(?:\s+et\s+al\.?)?,\s*\d{4}[a-z]?\)", text))
    total_refs = bracket_refs + paren_refs
    citation_density = min(1.0, total_refs / max(len(full_text) / 200, 1.0))

    structure_keywords = [
        "abstract",
        "introduction",
        "method",
        "experiment",
        "result",
        "conclusion",
        "reference",
        "摘要",
        "引言",
        "方法",
        "实验",
        "结果",
        "结论",
        "参考文献",
    ]
    struct_hits = sum(1 for kw in structure_keywords if re.search(rf"\b{re.escape(kw)}\b", text))
    structure = min(1.0, struct_hits / 5.0)

    formalism_patterns = [
        r"\\begin\{theorem\}",
        r"\\begin\{proof\}",
        r"\\begin\{equation\}",
        r"proof\.",
        r"theorem\s+\d+",
        r"lemma\s+\d+",
        r"proposition\s+\d+",
        r"证明",
        r"定理",
        r"引理",
    ]
    formal_hits = sum(1 for pat in formalism_patterns if re.search(pat, text))
    formalism = min(1.0, formal_hits / 3.0)

    return ObjectiveFeatures(
        ablation=round(ablation, 4),
        repro_signal=round(repro_signal, 4),
        citation_density=round(citation_density, 4),
        structure=round(structure, 4),
        formalism=round(formalism, 4),
    )
