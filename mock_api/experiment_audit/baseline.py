"""P0-6 Baseline 公平性初筛。

策略：
- 确定性线索：扫描实验设置描述中的差异化关键词（resolution/pretrain/
  input size/training budget 等）出现在 baseline 语境 → 候选。
- LLM 判定：将候选句子送文本 Qwen 判断是否构成不公平比较；LLM 不可用时
  回退到规则判定（要求更强信号：多个条件差异关键词共现）。
- 产出的 BASELINE_UNFAIR 一律 needs_human_review=True。
"""

from __future__ import annotations

import logging
import re

from .claims import _parse_json_object, _text_qwen_chat
from .metrics import split_sentences
from .schemas import make_finding

logger = logging.getLogger(__name__)

# baseline 语境 + 条件差异关键词共现 → 送 LLM 判定
_BASELINE_CONTEXT_RE = re.compile(
    r"baseline|compared (?:with|to|against)|previous (?:methods?|works?)"
    r"|prior (?:methods?|works?)",
    re.IGNORECASE,
)
_CONFIG_DIFF_RE = re.compile(
    r"(different|differing|not the same)\s+.{0,40}?"
    r"(resolution|input size|image size|pretrain\w*|backbone|training budget"
    r"|compute|data augmentation|schedule)"
    r"|(resolution|input size|pretrain\w*|backbone|training budget).{0,40}?"
    r"(different|mismatch|inconsisten)",
    re.IGNORECASE,
)

FAIRNESS_PROMPT = """你是 ML 论文审稿人。请判断下面的句子是否表明 baseline 比较条件不公平
（例如 baseline 使用不同分辨率/预训练数据/训练预算/数据增强，而本方法使用更有利条件）。

句子：
{sentence}

只输出 JSON：
{{"unfair": true/false, "reason": "一句话理由（中文）"}}
"""


def _collect_candidate_sentences(full_text: str, max_n: int = 10) -> list[str]:
    """收集同时含 baseline 语境与条件差异关键词的句子。"""
    candidates = []
    for sent in split_sentences(full_text):
        if _BASELINE_CONTEXT_RE.search(sent) and _CONFIG_DIFF_RE.search(sent):
            candidates.append(sent)
            if len(candidates) >= max_n:
                break
    return candidates


def check_baseline_fairness(full_text: str) -> list[dict]:
    """P0-6 入口。LLM 不可用时回退到规则判定（要求更强信号）。"""
    candidates = _collect_candidate_sentences(full_text)
    if not candidates:
        return []

    findings: list[dict] = []
    llm_available = True  # 跟踪 LLM 可用性，避免重复尝试

    for sent in candidates:
        # LLM 判定
        if llm_available:
            raw = _text_qwen_chat(FAIRNESS_PROMPT.format(sentence=sent[:1500]))
            if not raw:
                llm_available = False  # LLM 不可用，后续句子用规则回退
            else:
                item = _parse_json_object(raw)
                if not item.get("unfair"):
                    continue
                findings.append(
                    make_finding(
                        "BASELINE_UNFAIR",
                        title="Baseline 比较条件可能不公平",
                        claim=sent.strip()[:500],
                        computed=str(item.get("reason") or "")[:300],
                        method="LLM 判定 baseline 语境下的条件差异描述",
                        evidence_sources=[{"type": "text", "snippet": sent.strip()}],
                        normal_explanation=(
                            "差异条件可能是该 baseline 原论文的默认设置；公平性需结合 baseline 出处判断"
                        ),
                        needs_human_review=True,
                    )
                )
                continue

        # 规则回退：要求更强信号（多个条件差异关键词共现才触发）
        if _rule_based_fairness_check(sent):
            findings.append(
                make_finding(
                    "BASELINE_UNFAIR",
                    title="Baseline 比较条件可能不公平（规则检测）",
                    claim=sent.strip()[:500],
                    computed="句子同时包含 baseline 语境和多个条件差异关键词（LLM 不可用，规则回退）",
                    method="规则检测 baseline 语境下的条件差异描述（LLM 不可用）",
                    evidence_sources=[{"type": "text", "snippet": sent.strip()}],
                    normal_explanation=(
                        "差异条件可能是该 baseline 原论文的默认设置；公平性需结合 baseline 出处判断；"
                        "LLM 不可用时规则检测可能有误报，请人工复核"
                    ),
                    needs_human_review=True,
                )
            )
    return findings


# 更强的条件差异信号（规则回退时使用，减少误报）
_STRONG_DIFF_RE = re.compile(
    r"(different|differing|not the same)\s+.{0,30}?"
    r"(resolution|input size|pretrain\w*|backbone|training budget)",
    re.IGNORECASE,
)
_EXPLICIT_MISMATCH_RE = re.compile(
    r"(mismatch|inconsisten|unfair|advantage|favorable)",
    re.IGNORECASE,
)


def _rule_based_fairness_check(sentence: str) -> bool:
    """规则回退：要求更强信号才触发（减少误报）。

    条件：同时满足 baseline 语境 + (多个条件差异关键词 OR 显式不公平描述)
    """
    # 必须有 baseline 语境
    if not _BASELINE_CONTEXT_RE.search(sentence):
        return False
    # 强信号1：明确的条件差异描述（different + resolution/pretrain 等）
    if _STRONG_DIFF_RE.search(sentence):
        return True
    # 强信号2：显式不公平描述（mismatch/unfair/advantage 等）
    if _EXPLICIT_MISMATCH_RE.search(sentence):
        return True
    # 强信号3：多个条件差异关键词共现
    diff_matches = _CONFIG_DIFF_RE.findall(sentence)
    if len(diff_matches) >= 2:
        return True
    return False
