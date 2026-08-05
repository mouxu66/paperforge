"""感悟报告 → 原论文自动绑定。

从报告头部提取的原论文题目，去 papers 库（真实论文，category!='report'）
做标题模糊匹配，返回绑定的 paper_id。实现「绑定具体原论文」的自动落地，
免去手动选 paper_ids。

匹配策略（修正版）：
- 去掉标点/停用词，取标题核心词集合
- 对所有非 report 论文计算 Jaccard 重合度 + 查询词包含度，取最高分
- 仅当最高分 >= threshold 才返回，避免「首个命中即返回」的误绑
"""

from __future__ import annotations

import re

# 停用词：避免 "and/new/architecture" 等高频词导致误绑
STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "by",
    "from",
    "at",
    "as",
    "is",
    "are",
    "be",
    "this",
    "that",
    "these",
    "those",
    "new",
    "using",
    "based",
    "architecture",
    "system",
    "model",
    "approach",
    "method",
    "study",
    "paper",
    "we",
    "our",
    "its",
    "their",
    "via",
    "into",
    "over",
    "under",
    "between",
    "toward",
    "towards",
}


def _norm_tokens(t: str) -> list:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff\s]", " ", t)
    return [w for w in t.split() if len(w) >= 3 and w not in STOP]


def match_paper_by_title(title: str, db, threshold: float = 0.40) -> str | None:
    """按标题核心词重合度（Jaccard + 包含度）选最优匹配，返回 paper_id 或 None。

    不再「首个命中即返回」，而是对所有候选打分、取最高分；仅当最高分
    达到阈值才认定绑定成功，防止把报告绑到毫不相干的论文上。
    """
    if not title or not title.strip():
        return None
    from .models import Paper as PaperORM

    q_toks = set(_norm_tokens(title))
    if not q_toks:
        return None

    try:
        rows = db.query(PaperORM).filter(PaperORM.category != "report").all()
    except Exception:  # noqa: BLE001 - reflection 绑定 - 跨模块异常兜底
        return None

    best_id: str | None = None
    best_score = 0.0
    for r in rows:
        rt = r.title or ""
        if not rt.strip():
            continue
        p_toks = set(_norm_tokens(rt))
        if not p_toks:
            continue
        inter = len(q_toks & p_toks)
        union = len(q_toks | p_toks)
        jac = inter / union if union else 0.0
        contain = inter / len(q_toks) if q_toks else 0.0
        score = max(jac, contain)
        if score > best_score:
            best_score = score
            best_id = r.id

    return best_id if best_score >= threshold else None
