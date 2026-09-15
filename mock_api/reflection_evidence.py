"""报告条件化证据检索（evidence pack）—— 感悟评审的原论文上下文增强。

背景（2026-08-22 痛点）：
    ADR-014 P9 全文补充已停用——把论文全文喂给本机 9B 模型会压扁打分区分度
    （11 篇同批实验：全文跨度 0.112 vs 标题+摘要 0.425），所以 LLM 现在只看
    「标题+摘要」，报告中引用的论文数字/方法/结论无法当面核对；向量层
    fidelity/coverage 只能算相似度，不能辩证判断「报告说的 92% 是否真在论文里」。

思路：
    不喂整篇论文，只喂「与这份报告相关的原文段落」：
    1. 报告分句 → 与论文句做 N×M 余弦矩阵（复用 reflection_fidelity 的嵌入设施，
       与 fidelity 层同一套模型，零新增显存、零新增模型）；
    2. 挑选证据段：断言性句子（含数字/断言词）优先取证 —— 其中「低相似断言句」
       （疑似编造处）最优先，其次高相似锚点句；照抄句（sim≥COPY_SIM_THR）跳过，
       报告里已有逐字原文，不占预算；
    3. 组装为预算内（默认 2400 字符）的证据包文本，由 reflection_pipeline 经
       depth_eval_reflection 已有的 paper_supplement 通道注入【原论文参考内容】区块。

开关：PAPERFORGE_REFLECTION_EVIDENCE=1 开启（默认关，向后兼容，行为与旧版一致）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .reflection_fidelity import (
    COPY_SIM_THR,
    MIN_COPY_CHARS,
    STRAY_THR,
    _cosine_matrix,
    _has_assertive,
    _paper_sentences,
    split_sentences,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, "") or default), 1)
    except (TypeError, ValueError):
        return default


EVIDENCE_BUDGET_CHARS = _env_int("PAPERFORGE_REFLECTION_EVIDENCE_CHARS", 2400)
EVIDENCE_MAX_BLOCKS = _env_int("PAPERFORGE_REFLECTION_EVIDENCE_MAX_BLOCKS", 12)
BLOCK_MAX_CHARS = 320  # 单个证据块裁剪上限（合并相邻句后仍受此约束）
_BLOCK_PREFIX_RESERVE = 6  # 每块编号前缀（"[12] "）预留字符

PACK_HEADER = "【与该报告相关的原论文章节选】（依据报告内容自动检索，仅供对照核验）"


@dataclass
class EvidencePackResult:
    """build_evidence_pack 的返回值。pack_text 为空串表示本次无证据注入。"""

    pack_text: str = ""
    blocks: list = field(default_factory=list)  # [{text, sim, query}]，按论文行文序
    status: str = "ok"  # ok | no_paper | no_report | no_embedder
    message: str = ""
    budget_chars: int = 0


def evidence_enabled() -> bool:
    """总开关：PAPERFORGE_REFLECTION_EVIDENCE=1 开启（默认关）。"""
    return os.environ.get("PAPERFORGE_REFLECTION_EVIDENCE", "").strip() == "1"


def _default_embedder(texts: list[str]):
    """生产嵌入入口（测试可 monkeypatch 此函数注入 fake）。"""
    from .reflection_fidelity import _embed_many_ml

    return _embed_many_ml(texts)


def build_evidence_pack(
    report_sections: dict,
    paper_full_text: str,
    *,
    budget_chars: int | None = None,
    max_blocks: int | None = None,
    batch_embedder=None,
) -> EvidencePackResult:
    """按报告内容检索原论文章段，组装预算内的证据包文本。

    Args:
        report_sections: reflection_docx_parser 解析出的四段（q/tech/exp/reflection）。
        paper_full_text: 绑定原论文全文。
        budget_chars: 证据包正文总字符预算（不含标题行），默认 env 可调 2400。
        max_blocks: 证据块数量上限，默认 env 可调 12。
        batch_embedder: 测试 seam；默认走 reflection_fidelity._embed_many_ml。

    Returns:
        EvidencePackResult。任何前置条件不满足都返回空 pack（fail-open，
        绝不影响评审主流程）；status/message 说明原因供诊断字段透传。
    """
    budget = budget_chars or EVIDENCE_BUDGET_CHARS
    cap = max_blocks or EVIDENCE_MAX_BLOCKS

    if not paper_full_text or not paper_full_text.strip():
        return EvidencePackResult(
            status="no_paper", message="未绑定有效原论文", budget_chars=budget
        )

    rep_text = "\n".join(report_sections.get(k, "") for k in ("q", "tech", "exp", "reflection"))
    rep_sents = [s for s in split_sentences(rep_text) if len(s) >= 8]
    if not rep_sents:
        return EvidencePackResult(
            status="no_report", message="报告无可切分句子", budget_chars=budget
        )

    paper_sents = _paper_sentences(paper_full_text)
    if not paper_sents:
        return EvidencePackResult(
            status="no_paper", message="原论文无可切分句子", budget_chars=budget
        )

    embedder = batch_embedder or _default_embedder
    rep_vecs = embedder(rep_sents)
    paper_vecs = embedder(paper_sents)
    if rep_vecs is None or paper_vecs is None:
        return EvidencePackResult(
            status="no_embedder", message="嵌入模型不可用，跳过证据检索", budget_chars=budget
        )

    mat = _cosine_matrix(rep_vecs, paper_vecs)

    # 候选打分：断言句优先取证。
    #   priority 0：低相似断言句（疑似编造处，最需要论文原文当面对质）；
    #   priority 1：其余断言句（正在被讨论的数字/结论，按相似度降序）；
    #   priority 2：高相似锚点句（报告正确复述的部分，提供对照上下文）。
    # 照抄句跳过：报告已逐字含原文，再注入纯属浪费预算。
    candidates: list[tuple[int, float, int, int]] = []  # (priority, sort_key, rep_i, paper_j)
    for i, sent in enumerate(rep_sents):
        row = mat[i]
        j = int(row.argmax())
        sim = float(row[j])
        if sim >= COPY_SIM_THR and len(sent) >= MIN_COPY_CHARS:
            continue
        if _has_assertive(sent):
            if sim < STRAY_THR:
                candidates.append((0, sim, i, j))
            else:
                candidates.append((1, -sim, i, j))
        else:
            candidates.append((2, -sim, i, j))
    candidates.sort(key=lambda t: (t[0], t[1]))

    # 贪心装填：去重、预算内尽量多收（放不下当前候选时继续尝试更短的下一个）。
    selected: list[int] = []
    sel_meta: dict[int, tuple[float, str]] = {}  # paper_j -> (sim, query_sent)
    used = 0
    for priority, _key, i, j in candidates:
        if len(selected) >= cap or used >= budget:
            break
        if j in sel_meta:
            continue
        cost = min(len(paper_sents[j]), BLOCK_MAX_CHARS) + _BLOCK_PREFIX_RESERVE
        if used + cost > budget:
            continue
        selected.append(j)
        sel_meta[j] = (float(mat[i][j]), rep_sents[i][:80])
        used += cost

    if not selected:
        return EvidencePackResult(
            status="ok",
            message="无符合条件的候选证据段（全部为照抄句或超出预算）",
            budget_chars=budget,
        )

    # 合并论文中相邻的被选句（连续下标 → 一个块），给 LLM 局部上下文；
    # 输出按论文行文排序，诊断 sim/query 取该块首个被选句的元数据。
    selected.sort()
    groups: list[list[int]] = []
    for j in selected:
        if groups and j == groups[-1][-1] + 1:
            groups[-1].append(j)
        else:
            groups.append([j])

    blocks: list[dict] = []
    lines: list[str] = []
    total = 0
    for idx, group in enumerate(groups, 1):
        sim, query = sel_meta[group[0]]
        text = " ".join(paper_sents[k] for k in group).strip()
        allow = budget - total - _BLOCK_PREFIX_RESERVE
        if allow <= 24:
            break
        if len(text) > min(BLOCK_MAX_CHARS, allow):
            text = text[: min(BLOCK_MAX_CHARS, allow)].rstrip() + "…"
        blocks.append({"text": text, "sim": round(sim, 3), "query": query})
        lines.append(f"[{idx}] {text}")
        total += len(text) + _BLOCK_PREFIX_RESERVE

    return EvidencePackResult(
        pack_text=PACK_HEADER + "\n" + "\n".join(lines),
        blocks=blocks,
        status="ok",
        budget_chars=budget,
    )
