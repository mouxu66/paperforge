"""extractors/evidence.py —— v4.2 从 depth_eval_v4.py 拆分的证据检测函数。

迁移函数：
- _detect_ablation_evidence
- _detect_code_release
- _ABLATION_PATTERNS
- _REPO_RELEASE_PATTERNS
"""

from __future__ import annotations

import re

# ── 消融实验检测模式 ──────────────────────────────────────────────
_ABLATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ablation\s*(study|studies|experiment|analysis)?", re.IGNORECASE),
    re.compile(r"(we|we\s+also)\s+(vary|varied|remove|removed|removing)", re.IGNORECASE),
    re.compile(
        r"(without|w/o)\s+(the\s+)?(\w+\s+)?(module|component|branch|head|layer|block)",
        re.IGNORECASE,
    ),
    re.compile(r"component\s*(analysis|contribution|wise)", re.IGNORECASE),
    re.compile(r"factor\s*analysis", re.IGNORECASE),
    re.compile(
        r"(contribution|effect)\s+of\s+(each|every|individual)\s+(module|component|part)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(remov|removing)\s+(the\s+)?(\w+\s+)?(module|component|branch|head|layer|block|feature)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(drop|dropping|disable|disabling|turn\s*off)\s+(the\s+)?(\w+\s+)?(module|component|branch|head)",
        re.IGNORECASE,
    ),
    re.compile(r"消融\s*(实验|研究|分析)?"),
    re.compile(r"(去掉|移除|删除|关闭|禁用)\s*(了|掉)?\s*(\S{1,4})\s*(模块|组件|分支|层|头)"),
    re.compile(r"(逐个|逐一|分别)\s*(分析|评估|测试|验证)\s*(每个|各)\s*(模块|组件)"),
    re.compile(r"贡献度\s*(分析|评估)"),
]


def _detect_ablation_evidence(full_text: str) -> tuple[bool, list[str]]:
    """检测论文全文是否包含消融实验证据。

    Returns:
        (found: bool, matched_patterns: list[str])
    """
    if not full_text:
        return False, []
    matched: list[str] = []
    for pat in _ABLATION_PATTERNS:
        m = pat.search(full_text)
        if m:
            matched.append(m.group(0)[:80])
    found = len(matched) >= 2
    return found, matched


# ── 代码/数据发布信号检测 ────────────────────────────────────────
_REPO_RELEASE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://github\.com/\S+", re.I),
    re.compile(r"https?://huggingface\.co/\S+", re.I),
    re.compile(r"https?://gitlab\.com/\S+", re.I),
    re.compile(
        r"(?:our|the)\s+code\s+(?:is\s+)?(?:available|released|publicly\s+available|open[\s-]?source)",
        re.I,
    ),
    re.compile(
        r"(?:we\s+)?(?:release|open[\s-]?source|publicly\s+release)\s+(?:our\s+)?(?:code|implementation|source)",
        re.I,
    ),
    re.compile(r"代码[已]?\s*(?:开源|公开|发布|可获取|公开可用|公开可用)"),
]


def _detect_code_release(full_text: str) -> bool:
    """检测正文是否包含代码/数据/模型权重的公开获取信号。"""
    if not full_text:
        return False
    return any(pat.search(full_text) for pat in _REPO_RELEASE_PATTERNS)
