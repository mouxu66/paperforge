"""论文分析、排序、推荐、引用关系图、被引情感抽取、跨文档对比。

待拆分 god-file（~700 行）：建议按功能域拆为 analysis_rank / analysis_citation / analysis_compare。
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..author_utils import parse_authors
from ..llm.base import ChatMessage
from ..llm.factory import get_factory
from ..models import Paper as PaperORM
from ..schemas import (
    AnalysisReport,
    AnalysisSummarySchema,
    AnalyzedPaperSchema,
    AnalyzeResponse,
    DimensionScores,
    DimensionStats,
    RankedPaper,
    RankResponse,
    RankWeights,
)
from .embeddings import count_embeddings, get_all_embeddings, semantic_search_by_vector
from .search import rrf_fuse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 英文停用词表（覆盖最常见的无信息词汇，避免污染 FTS5 检索）
# ---------------------------------------------------------------------------
_EN_STOPWORDS: set[str] = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "if",
    "then",
    "else",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    "can",
    "need",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "they",
    "them",
    "their",
    "we",
    "our",
    "us",
    "you",
    "your",
    "he",
    "she",
    "his",
    "her",
    "i",
    "my",
    "me",
    "not",
    "no",
    "nor",
    "so",
    "than",
    "too",
    "very",
    "about",
    "above",
    "after",
    "again",
    "all",
    "any",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "up",
    "down",
    "out",
    "off",
    "over",
    "under",
    "into",
    "via",
    "per",
    "using",
    "used",
    "use",
    "based",
    "shown",
    "show",
    "showed",
    "however",
    "thus",
    "hence",
    "therefore",
    "while",
    "where",
    "when",
    "which",
    "who",
    "whom",
    "what",
    "how",
    "why",
    "whether",
    "during",
    "between",
    "through",
    "among",
    "both",
    "either",
    "neither",
    "also",
    "many",
    "much",
    "several",
    "various",
    "one",
    "two",
    "three",
    "first",
    "second",
    "third",
    "last",
    "new",
    "novel",
    "recent",
    "previous",
    "prior",
    "current",
    "present",
    "future",
    "past",
    "et",
    "al",
    "fig",
    "figure",
    "table",
    "section",
    "eq",
    "equation",
    "ref",
    "tab",
    "abstract",
    "introduction",
    "conclusion",
    "result",
    "results",
    "method",
    "methods",
    "discussion",
}


def _extract_keywords(content: str, max_keywords: int = 8) -> list[str]:
    """从文本中提取关键词（停用词过滤 + 简单分词 + 频率排序）。

    - 按非字母数字字符分词
    - 过滤停用词、过短词（<3 字符）、纯数字
    - 按词频降序取前 max_keywords 个（去重）
    """
    if not content:
        return []
    # 按非字母数字（含下划线）切分；保留连字符词（如 "state-of-the-art"）
    raw_tokens = re.split(r"[^a-zA-Z0-9\-]+", content.lower())
    freq: dict[str, int] = {}
    for tok in raw_tokens:
        tok = tok.strip("-")
        if len(tok) < 3:
            continue
        if tok in _EN_STOPWORDS:
            continue
        if tok.isdigit():
            continue
        freq[tok] = freq.get(tok, 0) + 1
    # 按频率降序，同频按字母序
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [kw for kw, _ in ranked[:max_keywords]]


def _parse_authors(raw: object) -> list[str]:
    """将原始 SQL 查询返回的 authors 字段统一解析为 list[str]。

    ORM 查询返回 list；原始 SQL（text()）返回 JSON 字符串或 list。
    """
    return parse_authors(raw)


# ===========================================================================
# 引用智能推荐（基于章节内容检索相关论文）
# ===========================================================================
def recommend_papers_by_content(
    db: Session,
    content: str,
    top_k: int = 10,
) -> list[dict]:
    """基于章节内容推荐相关论文（混合检索：FTS5 关键词 + 向量语义）。

    检索逻辑：
    1. 从内容中提取关键词（停用词过滤 + 频率排序，取前 8 个）
    2. FTS5 全文检索（关键词 OR 组合，Top 20）
    3. 向量语义检索（用内容原文编码，Top 20）
    4. RRF 融合两路结果，返回 Top K

    降级策略：向量检索不可用时仅使用 FTS5。

    返回 [{"id", "title", "authors", "year", "score"}, ...]，按 score 降序。
    score 为 0-1 归一化值（向量相似度或 FTS 排名得分）。
    """
    content = (content or "").strip()
    if not content:
        return []

    keywords = _extract_keywords(content, max_keywords=8)
    if not keywords:
        return []

    # 取内容末尾 500 字符作为向量编码文本（兼顾时效性与性能）
    embed_text_input = content[-500:] if len(content) > 500 else content

    # --- 1. FTS5 关键词检索（OR 组合） ---
    # 用双引号包裹每个关键词避免特殊字符干扰，用 OR 连接
    fts_query = " OR ".join(f'"{kw}"' for kw in keywords)
    fts_sql = text(
        """
        SELECT p.id    AS id,
               p.title AS title,
               p.authors AS authors,
               p.year  AS year
        FROM paper_fts
        JOIN papers p ON p.id = paper_fts.paper_id
        WHERE paper_fts MATCH :q
          AND p.category != 'report'
        ORDER BY bm25(paper_fts)
        LIMIT :limit
        """
    )
    fts_ranked: list[str] = []
    fts_meta: dict[str, dict] = {}
    try:
        rows = db.execute(fts_sql, {"q": fts_query, "limit": 20}).all()
        for r in rows:
            fts_ranked.append(r.id)
            fts_meta[r.id] = {
                "id": r.id,
                "title": r.title,
                "authors": _parse_authors(r.authors),
                "year": r.year or 0,
            }
    except Exception:  # noqa: BLE001 - analysis 派生字段 - 单维度失败用默认值
        # FTS 表不存在 / 查询语法错误等，防御性跳过
        fts_ranked = []

    # --- 2. 向量语义检索 ---
    vec_ranked: list[tuple[str, float]] = []
    from ..semantic_search import embed_text, is_available

    if is_available() and count_embeddings(db) > 0:
        qvec = embed_text(embed_text_input)
        if qvec:
            vec_ranked = semantic_search_by_vector(db, qvec, top_k=20)

    # --- 3. 融合结果 ---
    vec_meta: dict[str, dict] = {}
    if vec_ranked:
        missing_ids = [pid for pid, _ in vec_ranked if pid not in fts_meta]
        if missing_ids:
            extra_rows = (
                db.query(PaperORM)
                .filter(PaperORM.id.in_(missing_ids), PaperORM.category != "report")
                .all()
            )
            for p in extra_rows:
                vec_meta[p.id] = {
                    "id": p.id,
                    "title": p.title,
                    "authors": parse_authors(p.authors),
                    "year": p.year or 0,
                }

    # 计算最终得分
    scored: dict[str, float] = {}

    if fts_ranked and vec_ranked:
        # RRF 融合
        fts_only = [pid for pid in fts_ranked]
        vec_only = [pid for pid, _ in vec_ranked]
        fused = rrf_fuse(fts_only, vec_only, k=60, top_n=top_k)
        # 构建 id → 向量相似度映射
        vec_sim_map = {pid: sim for pid, sim in vec_ranked}
        for pid, rrf_score in fused:
            # 综合得分：RRF 排名分（归一化到 0-0.5）+ 向量相似度（0-0.5）
            vec_sim = vec_sim_map.get(pid, 0.0)
            # RRF 最高分归一化：rrf_score 最大约 2/61 ≈ 0.033
            rrf_norm = min(rrf_score / 0.033, 1.0) * 0.5
            score = rrf_norm + vec_sim * 0.5
            scored[pid] = round(score, 4)
    elif vec_ranked:
        # 仅向量：直接用余弦相似度作为得分
        for pid, sim in vec_ranked[:top_k]:
            scored[pid] = round(float(sim), 4)
    elif fts_ranked:
        # 仅 FTS5：用排名得分（1/rank，归一化到 0-1）
        total = len(fts_ranked)
        for i, pid in enumerate(fts_ranked[:top_k]):
            scored[pid] = round(1.0 - (i / max(total, 1)) * 0.7, 4)
    else:
        return []

    # 按得分降序取 Top K
    ranked_ids = sorted(scored.keys(), key=lambda pid: scored[pid], reverse=True)[:top_k]

    result: list[dict] = []
    for pid in ranked_ids:
        meta = fts_meta.get(pid) or vec_meta.get(pid)
        if not meta:
            # 兜底查库
            paper = db.query(PaperORM).filter(PaperORM.id == pid).first()
            if not paper:
                continue
            meta = {
                "id": paper.id,
                "title": paper.title,
                "authors": parse_authors(paper.authors),
                "year": paper.year or 0,
            }
        result.append(
            {
                "id": meta["id"],
                "title": meta["title"],
                "authors": meta["authors"],
                "year": meta["year"],
                "score": scored[pid],
            }
        )
    return result


# ---------------------------------------------------------------------------
# WP-2.2: 旧版「自述情感」函数（已弃用）
# ---------------------------------------------------------------------------
# 注意：以下两个函数基于论文自己的 title+abstract 做关键词情感打分，
# 学术价值有限，仅保留以兼容旧数据。WP-2.2 新实现使用 citation_sentiments
# 表存储真正的「被引情感」。新代码不应再调用它们。
# ---------------------------------------------------------------------------
_SENTIMENT_POSITIVE = {
    "novel",
    "breakthrough",
    "outstanding",
    "excellent",
    "superior",
    "effective",
    "promising",
    "significant",
    "remarkable",
    "impressive",
    "strong",
    "robust",
    "successful",
    "advances",
    "state-of-the-art",
    "state of the art",
    "substantial",
    "comprehensive",
    "insightful",
}
_SENTIMENT_NEGATIVE = {
    "limitation",
    "limited",
    "weakness",
    "flaw",
    "poor",
    "inferior",
    "inadequate",
    "failure",
    "failed",
    "unsatisfactory",
    "shortcoming",
    "drawback",
    "insufficient",
    "unclear",
    "questionable",
}


def extract_sentiment(text: str) -> tuple[float, str, float]:
    """基于关键词计数返回情感分数、标签与置信度（MVP 实现）。

    Returns:
        (score, label, confidence)
        - score: -1.0 ~ 1.0
        - label: "positive" | "neutral" | "negative"
        - confidence: 0.0 ~ 1.0
    """
    if not text:
        return 0.0, "neutral", 0.0

    lowered = text.lower()
    pos = sum(1 for w in _SENTIMENT_POSITIVE if w in lowered)
    neg = sum(1 for w in _SENTIMENT_NEGATIVE if w in lowered)
    total = pos + neg
    if total == 0:
        return 0.0, "neutral", 0.0

    score = (pos - neg) / total
    # 置信度：命中词越多越可信，但上限 0.95
    confidence = min(0.95, 0.5 + 0.1 * total)
    label = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"
    return round(score, 4), label, round(confidence, 4)


def ensure_paper_sentiment(db: Session, paper: PaperORM) -> None:
    """若论文尚无情感分数，基于 title+abstract 计算并缓存到 ORM（不提交）。"""
    if paper.sentiment_score is not None:
        return
    text = f"{paper.title or ''} {paper.abstract or ''}"
    score, label, confidence = extract_sentiment(text)
    paper.sentiment_score = score
    paper.sentiment_label = label
    paper.sentiment_confidence = confidence
    db.add(paper)
    # 注意：由调用方统一 commit，避免在循环中多次提交


# ===========================================================================
# WP-2.2: 真正的「被引情感」——基于全文引用上下文 + LLM 抽取
# ===========================================================================

# 被引情感标签：支持 / 批评 / 背景引用
CITATION_SENTIMENT_LABELS = {"support", "criticize", "background"}


def _normalize_title(title: str) -> str:
    """将标题归一化为小写、去标点的字符串，用于模糊匹配。"""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _find_citation_contexts(
    full_text: str,
    target_title: str,
    target_authors: list[str] | None = None,
    target_year: int | None = None,
    window: int = 400,
) -> list[tuple[int, str]]:
    """在 source 论文全文中查找对 target 论文的引用位置及上下文片段。

    返回 [(match_index, snippet), ...]，按出现顺序。
    匹配策略（MVP，按优先级）：
    1. 标题精确子串匹配（不区分大小写，要求标题长度 >= 10 且带词边界）
    2. 第一作者姓氏 + 年份模式（如 "Smith et al. (2020)"）
    """
    if not full_text or not target_title:
        return []

    contexts: list[tuple[int, str]] = []
    text_lower = full_text.lower()

    # 1. 标题子串匹配（带基本过滤：标题过短或过于通用则跳过）
    title_lower = target_title.lower()
    # 标题至少 10 个字符且包含至少 2 个单词，避免 "A" "The" 等误匹配
    if len(title_lower) >= 10 and len(title_lower.split()) >= 2:
        start_idx = 0
        while True:
            idx = text_lower.find(title_lower, start_idx)
            if idx == -1:
                break
            # 要求标题前后不是字母数字，降低误匹配
            before = full_text[idx - 1] if idx > 0 else " "
            after = (
                full_text[idx + len(title_lower)]
                if idx + len(title_lower) < len(full_text)
                else " "
            )
            if before.isalnum() or after.isalnum():
                start_idx = idx + 1
                continue
            snippet_start = max(0, idx - window)
            snippet_end = min(len(full_text), idx + window)
            contexts.append((idx, full_text[snippet_start:snippet_end]))
            start_idx = idx + 1

    # 2. 作者-年份模式匹配
    if target_authors and target_year:
        first_author = target_authors[0].split()[-1] if target_authors[0] else None
        if first_author and len(first_author) > 1:
            # 匹配 "Author et al. (2020)" 或 "Author (2020)" 或 "Author, 2020"
            patterns = [
                rf"\b{re.escape(first_author)}\s+et\s+al\.\s*\(\s*{target_year}\s*\)",
                rf"\b{re.escape(first_author)}\s*\(\s*{target_year}\s*\)",
                rf"\b{re.escape(first_author)}\s*,\s*\(?\s*{target_year}\s*\)?",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, full_text, re.IGNORECASE):
                    idx = match.start()
                    snippet_start = max(0, idx - window)
                    snippet_end = min(len(full_text), idx + window)
                    contexts.append((idx, full_text[snippet_start:snippet_end]))

    # 去重并按位置排序
    seen: set[str] = set()
    unique_contexts: list[tuple[int, str]] = []
    for idx, snippet in sorted(contexts, key=lambda x: x[0]):
        if snippet not in seen:
            seen.add(snippet)
            unique_contexts.append((idx, snippet))

    return unique_contexts


def _classify_citation_sentiment(
    context: str, target_title: str, source_title: str
) -> tuple[str, float]:
    """使用 LLM 判断引用上下文情感。

    返回 (label, confidence)，label 为 support / criticize / background。
    当 LLM 不可用时，返回 ("background", 0.0)。
    """
    try:
        provider = get_factory().get_provider()
    except Exception:  # noqa: BLE001 - LLM 不可用时降级
        return "background", 0.0

    system_prompt = (
        "You are an academic citation analyst. Analyze the provided citation context "
        "and classify the citing paper's stance toward the cited paper.\n\n"
        "Output strictly JSON with keys:\n"
        "- 'intent': one of 'support', 'criticize', 'background'\n"
        "- 'confidence': float between 0 and 1\n\n"
        "Definitions:\n"
        "- support: the citing paper agrees with, builds upon, or praises the cited work\n"
        "- criticize: the citing paper disagrees with, points out limitations, or challenges the cited work\n"
        "- background: the cited work is mentioned only as related work or context, without clear stance\n\n"
        "Be conservative: if the intent is unclear, classify as 'background' with lower confidence."
    )

    user_prompt = (
        f"Cited paper title: {target_title}\n"
        f"Citing paper title: {source_title}\n"
        f"Citation context:\n---\n{context[:1500]}\n---\n"
        "Classify the citation intent."
    )

    try:
        result = provider.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=128,
        )
        content = (result.content or "").strip()
        if not content:
            return "background", 0.0

        # 尝试从 JSON 块中提取
        if "```json" in content:
            content = content.split("```json")[-1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()

        # 防御：如果 LLM 返回的不是 JSON，尝试查找第一个 { ... } 块
        if not content.startswith("{"):
            match = re.search(r"\{.*?\}", content, re.DOTALL)
            if match:
                content = match.group(0)
            else:
                return "background", 0.0

        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return "background", 0.0

        intent = str(parsed.get("intent", "background")).lower()
        confidence = float(parsed.get("confidence", 0.0))
        if intent not in CITATION_SENTIMENT_LABELS:
            intent = "background"
        confidence = max(0.0, min(1.0, confidence))
        return intent, confidence
    except Exception as exc:  # noqa: BLE001 - LLM 失败降级为背景引用并记录日志
        logger.warning("LLM 被引情感分类失败: %s", exc)
        return "background", 0.0


def extract_citation_sentiments_for_target(
    db: Session,
    target_paper_id: str,
    max_candidates: int = 200,
    progress_callback: callable | None = None,
) -> dict:
    """为指定目标论文抽取被引情感。

    流程：
    1. 查询目标论文信息
    2. 在库中查找 full_text 包含目标论文标题/作者-年份的其他论文
    3. 对每个匹配，提取上下文并用 LLM 分类
    4. 保存/更新 citation_sentiments 表

    Args:
        max_candidates: 最多扫描的候选论文数，防止大库 OOM/挂起。
        progress_callback: 可选回调 (current, total) 用于 worker 进度上报。

    返回 {"processed": int, "saved": int, "failed": int}。
    """
    from ..models import CitationSentiment as CitationSentimentORM

    target = db.query(PaperORM).filter(PaperORM.id == target_paper_id).first()
    if not target:
        return {"processed": 0, "saved": 0, "failed": 0, "error": "target not found"}

    # 只处理有 full_text 的其他论文，限制数量防止大库扫描失控
    candidates = (
        db.query(PaperORM)
        .filter(PaperORM.id != target_paper_id)
        .filter(PaperORM.full_text.isnot(None))  # type: ignore[arg-type]
        .filter(PaperORM.full_text != "")
        .limit(max_candidates)
        .all()
    )

    processed = 0
    saved = 0
    failed = 0
    total = len(candidates)

    try:
        for idx, source in enumerate(candidates):
            contexts = _find_citation_contexts(
                source.full_text or "",
                target.title,
                parse_authors(target.authors),
                target.year,
            )
            if not contexts:
                if progress_callback:
                    progress_callback(idx + 1, total)
                continue

            processed += 1
            # 对每个 source 只取第一个上下文片段进行分类（MVP 简化）
            _, snippet = contexts[0]

            try:
                label, confidence = _classify_citation_sentiment(
                    snippet, target.title, source.title
                )
            except Exception:  # noqa: BLE001 - LLM 单点失败不影响其他候选
                label, confidence = "background", 0.0
                failed += 1

            # 查找是否已存在记录
            existing = (
                db.query(CitationSentimentORM)
                .filter(CitationSentimentORM.source_paper_id == source.id)
                .filter(CitationSentimentORM.target_paper_id == target.id)
                .first()
            )
            if existing:
                existing.sentiment_label = label
                existing.confidence = confidence
                existing.context_snippet = snippet
            else:
                new_record = CitationSentimentORM(
                    source_paper_id=source.id,
                    target_paper_id=target.id,
                    sentiment_label=label,
                    confidence=confidence,
                    context_snippet=snippet,
                )
                db.add(new_record)
            saved += 1

            if progress_callback:
                progress_callback(idx + 1, total)

        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        # 提交失败时，所有已处理记录均未真正持久化
        failed = processed
        saved = 0

    return {"processed": processed, "saved": saved, "failed": failed}


def get_citation_sentiments_for_target(db: Session, target_paper_id: str) -> list[dict]:
    """获取某篇论文的被引情感记录列表。"""
    from ..models import CitationSentiment as CitationSentimentORM

    rows = (
        db.query(CitationSentimentORM)
        .filter(CitationSentimentORM.target_paper_id == target_paper_id)
        .all()
    )
    return [
        {
            "sourcePaperId": r.source_paper_id,
            "targetPaperId": r.target_paper_id,
            "sentimentLabel": r.sentiment_label,
            "confidence": r.confidence,
            "contextSnippet": r.context_snippet,
        }
        for r in rows
    ]


# ===========================================================================
# 引用关系可视化（基于现有 citations 字段 + 语义相似度推荐）
# ===========================================================================
def get_citation_relations(db: Session, paper_id: str) -> dict | None:
    """获取论文引用关系。

    返回 {"citations": int, "references": list[Paper]}：
    - citations: 该论文被引用次数（papers.citations 字段）
    - references: 基于标题关键词语义相似度推荐的 5 篇相关论文（mock 引用关系）
    论文不存在时返回 None。
    """
    from .search import retrieve_context

    paper: PaperORM | None = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return None
    # 基于标题关键词在库内检索相似论文（取前 5 条，排除自身）
    related = retrieve_context(db, paper.title or paper_id, paper_ids=[], top_k=6)
    references = [p for p in related if p.id != paper_id][:5]
    return {"citations": paper.citations or 0, "references": references}


# ===========================================================================
# WP-2.2: 关系图（真正的被引情感：中心论文 + 引用它的论文 + 情感边）
# ===========================================================================
def _sentiment_label_to_score(label: str) -> float:
    """将被引情感标签映射为 -1~1 的分数，用于前端展示。"""
    if label == "support":
        return 1.0
    if label == "criticize":
        return -1.0
    return 0.0


def get_relation_graph(
    db: Session,
    paper_id: str,
    top_k: int = 8,
) -> dict | None:
    """构建以 paper_id 为中心的关系图。

    返回 {"nodes": [...], "edges": [...]}：
    - nodes: 中心论文 + 最多 top_k 篇引用它的论文，含被引情感字段
    - edges: 边列表，type="citation"，weight 为情感强度，sentiment 为标签
    """
    from ..models import CitationSentiment as CitationSentimentORM

    paper: PaperORM | None = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return None

    # 中心节点
    nodes: list[dict] = [
        {
            "id": paper.id,
            "title": paper.title,
            "category": paper.category or "all",
            "year": paper.year or 0,
            "citations": paper.citations or 0,
            "sentimentScore": 0.0,
            "sentimentLabel": "background",
        }
    ]
    edges: list[dict] = []
    graph_mode = "citation"

    # 查询 citation_sentiments 表中 target 为当前论文的记录
    citation_records = (
        db.query(CitationSentimentORM)
        .filter(CitationSentimentORM.target_paper_id == paper_id)
        .all()
    )

    # 如果已有被引情感数据，优先使用
    if citation_records:
        source_ids = [r.source_paper_id for r in citation_records]
        source_orms = {
            p.id: p for p in db.query(PaperORM).filter(PaperORM.id.in_(source_ids)).all()
        }

        for record in citation_records[:top_k]:
            source = source_orms.get(record.source_paper_id)
            if source is None:
                continue
            nodes.append(
                {
                    "id": source.id,
                    "title": source.title,
                    "category": source.category or "all",
                    "year": source.year or 0,
                    "citations": source.citations or 0,
                    "sentimentScore": _sentiment_label_to_score(record.sentiment_label),
                    "sentimentLabel": record.sentiment_label,
                }
            )
            edges.append(
                {
                    "source": source.id,
                    "target": paper.id,
                    "type": "citation",
                    "weight": record.confidence,
                    "sentiment": record.sentiment_label,
                    "snippet": record.context_snippet,
                }
            )

    # 如果没有足够的引用情感数据，降级到语义相似论文（保持图不空白）
    if len(nodes) < 2:
        graph_mode = "similarity"
        center_text = f"{paper.title or ''} {paper.abstract or ''}"
        related_papers = recommend_papers_by_content(db, center_text, top_k=top_k + 1)
        related_ids = [rp["id"] for rp in related_papers if rp["id"] != paper_id]
        related_orms = {
            p.id: p for p in db.query(PaperORM).filter(PaperORM.id.in_(related_ids)).all()
        }
        for rp in related_papers:
            rid = rp["id"]
            if rid == paper_id:
                continue
            orm = related_orms.get(rid)
            nodes.append(
                {
                    "id": rid,
                    "title": rp["title"],
                    "category": (orm.category or "all") if orm else "all",
                    "year": rp["year"] or 0,
                    "citations": (orm.citations or 0) if orm else 0,
                    "sentimentScore": 0.0,
                    "sentimentLabel": "background",
                }
            )
            edges.append(
                {
                    "source": paper.id,
                    "target": rid,
                    "type": "similarity",
                    "weight": rp["score"],
                }
            )

    return {"nodes": nodes, "edges": edges, "mode": graph_mode}


# ===========================================================================
# 指定论文分析（选定论文对比分析）
# ===========================================================================
def analyze_papers(
    db: Session,
    paper_ids: list[str],
    topic: str | None = None,
    weights: RankWeights | None = None,
) -> AnalyzeResponse:
    """指定论文对比分析。

    1. 根据 paper_ids 从数据库查询论文信息
    2. 如果 topic 存在，用 BGE 向量计算每篇论文摘要与主题的余弦相似度
    3. 按综合得分（引用数归一化 + 时间归一化 + 主题匹配度）排序
    4. 返回排序后的论文列表和汇总统计
    """
    if weights is None:
        weights = RankWeights(
            citations=0.25,
            recency=0.25,
            similarity=0.25,
            journal=0.25,
        )

    # 1. 查询论文
    papers = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
    if not papers:
        return AnalyzeResponse(
            analyzed_papers=[],
            summary=AnalysisSummarySchema(
                top_paper="", total_citations=0, avg_year=0, distribution_by_category=None
            ),
        )

    # 2. 计算主题相似度（如果 topic 存在）
    topic_sims: dict[str, float] = {}
    if topic and topic.strip():
        try:
            topic_sims = _compute_topic_similarities(db, topic.strip(), paper_ids)
        except Exception:  # noqa: BLE001 - analysis 派生字段 - 单维度失败用默认值
            # 向量不可用时静默降级
            topic_sims = {}

    # 3. 计算各维度得分
    citations_list = [p.citations or 0 for p in papers if p.citations]
    years_list = [p.year for p in papers if p.year and p.year > 0]
    max_citations = max(citations_list) if citations_list else 1
    current_year = datetime.now().year
    min_year = min(years_list) if years_list else current_year
    year_range = max(current_year - min_year, 1)

    analyzed: list[AnalyzedPaperSchema] = []
    for p in papers:
        # 引用数归一化（log 平滑）
        c_score = (
            math.log(p.citations + 1) / math.log(max_citations + 1) if max_citations > 0 else 0.0
        )

        # 发表时间（越新越高）
        if p.year and p.year > 0:
            age = current_year - p.year
            t_score = max(0.0, 1.0 - (age / max(year_range, 10)))
        else:
            t_score = 0.25

        # 主题相似度
        sim = topic_sims.get(p.id, 0.0) if topic else 0.0

        # 期刊等级
        j_score = _compute_journal_tier(p.journal or "")

        # 综合得分
        if topic and topic.strip():
            total = (
                weights.citations * c_score
                + weights.recency * t_score
                + weights.similarity * sim
                + weights.journal * j_score
            )
        else:
            # topic 为空时仅按引用数 + 时间排序（均等权重）
            total = 0.5 * c_score + 0.5 * t_score

        total = min(1.0, max(0.0, round(total, 4)))

        analyzed.append(
            AnalyzedPaperSchema(
                id=p.id,
                title=p.title,
                authors=parse_authors(p.authors),
                year=p.year or 0,
                citations=p.citations or 0,
                journal=p.journal or "",
                similarity_score=round(sim, 4) if topic else None,
                total_score=total,
            )
        )

    # 4. 排序
    analyzed.sort(key=lambda a: a.total_score, reverse=True)

    # 5. 汇总统计
    total_citations = sum(a.citations for a in analyzed)
    years = [a.year for a in analyzed if a.year > 0]
    avg_year = round(statistics.mean(years), 1) if years else 0.0
    top_paper = analyzed[0].id if analyzed else ""

    # 学科分布（按 fields_of_study 或 category）
    dist: dict[str, int] = {}
    for p in papers:
        fos = p.fields_of_study if hasattr(p, "fields_of_study") and p.fields_of_study else None
        if fos:
            for f in fos:
                dist[f] = dist.get(f, 0) + 1
        else:
            cat = p.category or "other"
            dist[cat] = dist.get(cat, 0) + 1

    summary = AnalysisSummarySchema(
        top_paper=top_paper,
        total_citations=total_citations,
        avg_year=avg_year,
        distribution_by_category=dist if dist else None,
    )
    return AnalyzeResponse(
        analyzed_papers=analyzed,
        summary=summary,
    )


def _compute_topic_similarities(
    db: Session,
    topic: str,
    paper_ids: list[str] | None,
) -> dict[str, float]:
    """计算所有论文（或指定范围）与主题的语义相似度。

    优先使用已缓存的 embedding；缓存缺失时对 title+abstract 实时编码。
    返回 {paper_id: similarity (0~1)}。
    """
    from ..semantic_search import cosine_similarity, deserialize_vector, embed_text, is_available

    topic = (topic or "").strip()
    if not topic or not is_available():
        return {}

    qvec = embed_text(topic)
    if not qvec:
        return {}

    rows = get_all_embeddings(db)
    embed_map: dict[str, list[float]] = {}
    for pid, emb_str in rows:
        vec = deserialize_vector(emb_str)
        if vec:
            embed_map[pid] = vec

    # 限定范围
    if paper_ids:
        allowed = set(paper_ids)
        embed_map = {pid: v for pid, v in embed_map.items() if pid in allowed}

    if not embed_map:
        return {}

    result: dict[str, float] = {}
    for pid, vec in embed_map.items():
        sim = max(0.0, float(cosine_similarity(qvec, vec)))
        result[pid] = round(sim, 4)
    return result


def _compute_journal_tier(journal_name: str) -> float:
    """计算期刊等级分数 (0.0~1.0)。延迟导入以避免循环依赖。"""
    from ..semantic_scholar import get_journal_tier

    return get_journal_tier(journal_name)


def rank_papers(
    db: Session,
    topic: str,
    weights: RankWeights,
    paper_ids: list[str] | None = None,
    top_k: int = 20,
) -> RankResponse:
    """论文综合评分与排序。

    评分维度：
    1. 主题相似度 (0-1)：论文 title+abstract embedding 与 topic 的余弦相似度
    2. 引用数 (0-1)：log 归一化引用数
    3. 发表时间 (0-1)：越新越高（简单线性衰减，窗口 10 年）
    4. 期刊等级 (0-1)：基于期刊名匹配知名 venue

    总得分 = sum(weight_i * score_i)，结果归一化到 0-1。

    若向量模型不可用，相似度维度退化为 0。
    """
    # 加载论文（排除感悟报告）
    query = db.query(PaperORM).filter(PaperORM.category != "report")
    if paper_ids:
        query = query.filter(PaperORM.id.in_(paper_ids))
    papers = query.all()
    if not papers:
        return RankResponse(papers=[], total=0, topic=topic, weights=weights)

    # 计算主题相似度
    topic_sims = _compute_topic_similarities(db, topic, paper_ids)

    # 收集各维度原始值
    citations_list: list[int] = []
    years_list: list[int] = []
    for p in papers:
        citations_list.append(p.citations or 0)
        if p.year and p.year > 0:
            years_list.append(p.year)

    # 归一化参数
    max_citations = max(citations_list) if citations_list else 1
    current_year = datetime.now().year
    min_year = min(years_list) if years_list else current_year
    year_range = max(current_year - min_year, 1)

    ranked: list[RankedPaper] = []

    for p in papers:
        # 1. 主题相似度
        sim = topic_sims.get(p.id, 0.0)

        # 2. 引用数归一化（log 平滑）
        if max_citations > 0:
            citations_score = math.log(p.citations + 1) / math.log(max_citations + 1)
        else:
            citations_score = 0.0

        # 3. 发表时间（越新越高，线性衰减窗口 10 年）
        if p.year and p.year > 0:
            age = current_year - p.year
            recency_score = max(0.0, 1.0 - (age / max(year_range, 10)))
        else:
            recency_score = 0.25  # 未知年份给基础分

        # 4. 期刊等级
        journal_score = _compute_journal_tier(p.journal or "")

        # 综合得分
        composite = (
            weights.similarity * sim
            + weights.citations * citations_score
            + weights.recency * recency_score
            + weights.journal * journal_score
        )
        # 确保有相似度时不被过度压制
        composite = min(1.0, max(0.0, round(composite, 4)))

        ranked.append(
            RankedPaper(
                id=p.id,
                title=p.title,
                authors=parse_authors(p.authors),
                year=p.year or 0,
                journal=p.journal or "",
                citations=p.citations or 0,
                compositeScore=composite,
                dimensions=DimensionScores(
                    citations=round(citations_score, 4),
                    recency=round(recency_score, 4),
                    similarity=round(sim, 4),
                    journal=round(journal_score, 4),
                ),
            )
        )

    # 按综合得分降序
    ranked.sort(key=lambda r: r.compositeScore, reverse=True)
    ranked = ranked[:top_k]

    return RankResponse(
        papers=ranked,
        total=len(papers),
        topic=topic,
        weights=weights,
    )


def compare_papers(
    db: Session,
    paper_ids: list[str],
    question: str | None = None,
) -> dict:
    """跨文档对比表：对选中的论文做结构化抽取。

    使用 LLM 从每篇论文的摘要/全文中抽取方法、样本量、主要结果、
    评估指标、局限性和结论，返回统一结构的对比表。
    """
    from datetime import datetime

    papers = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
    if len(papers) < 2:
        raise ValueError("至少需要 2 篇论文进行对比")

    # 构建 prompt
    paper_texts = []
    for p in papers:
        text_parts = [
            f"Paper ID: {p.id}",
            f"Title: {p.title}",
            f"Authors: {', '.join(parse_authors(p.authors)[:8])}",
            f"Year: {p.year or ''}",
            f"Abstract: {p.abstract or ''}",
        ]
        # 如果有全文，追加截断后的内容
        if p.full_text:
            text_parts.append(f"Full text (truncated): {p.full_text[:3000]}")
        paper_texts.append("\n".join(text_parts))

    all_text = "\n\n---\n\n".join(paper_texts)

    q_hint = f"Focus on the following aspect/question: {question}\n" if question else ""

    system_prompt = (
        "You are an academic research assistant. Given the abstracts (and truncated full texts) "
        "of several papers, extract a structured comparison table.\n\n"
        "For each paper, output these fields in JSON:\n"
        "- method: the core method/model/technical approach (concise, ≤150 chars)\n"
        "- sample_size: dataset size, experimental setup, or evaluation scope (≤150 chars)\n"
        "- main_results: key quantitative/qualitative findings (≤250 chars)\n"
        "- metrics: evaluation metrics used (≤150 chars)\n"
        "- limitations: limitations acknowledged by the authors (≤250 chars)\n"
        "- conclusion: main contribution or conclusion (≤250 chars)\n\n"
        "Return strictly a JSON object with a single key 'rows' containing a list of objects.\n"
        "Each object must include: paper_id, title, authors (list), year, method, sample_size, "
        "main_results, metrics, limitations, conclusion.\n"
        "If a field is not mentioned, use an empty string. Do not include markdown formatting."
    )

    user_prompt = f"{q_hint}Papers:\n\n{all_text}\n\nExtract the comparison table."

    try:
        provider = get_factory().get_provider()
    except Exception as exc:
        logger.warning("跨文档对比：LLM 未配置: %s", exc)
        raise RuntimeError("LLM 未配置，请先在「模型管理」中添加并启用模型") from exc

    try:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]
        resp = provider.chat(messages, temperature=0.1, max_tokens=2048)
        content = (resp.content or "").strip()
    except Exception as exc:
        logger.warning("跨文档对比 LLM 调用失败: %s", exc)
        raise RuntimeError("对比抽取失败，请检查 LLM 配置") from exc

    # 解析 JSON
    try:
        if "```json" in content:
            content = content.split("```json")[-1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 返回的不是 JSON 对象")
        rows = parsed.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("LLM 返回的 rows 不是列表")
    except Exception as exc:
        logger.warning("跨文档对比结果解析失败: %s", exc)
        raise RuntimeError("对比结果解析失败") from exc

    # 补充缺失字段并映射到 schema
    result_rows = []
    paper_map = {p.id: p for p in papers}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("paper_id") or row.get("paperId") or row.get("id")
        paper = paper_map.get(pid)
        if paper is None:
            continue
        result_rows.append(
            {
                "paper_id": paper.id,
                "title": paper.title,
                "authors": parse_authors(paper.authors),
                "year": paper.year or 0,
                "method": str(row.get("method", "")),
                "sample_size": str(row.get("sample_size", row.get("sampleSize", ""))),
                "main_results": str(row.get("main_results", row.get("mainResults", ""))),
                "metrics": str(row.get("metrics", "")),
                "limitations": str(row.get("limitations", "")),
                "conclusion": str(row.get("conclusion", "")),
            }
        )

    return {
        "rows": result_rows,
        "generated_at": datetime.now().isoformat(),
        "model": getattr(resp, "model", "") if "resp" in locals() else "",
    }


def get_analysis_report(
    db: Session,
    topic: str,
    weights: RankWeights,
    paper_ids: list[str] | None = None,
    top_k: int = 50,
) -> AnalysisReport:
    """生成综合分析报告：排序结果 + 得分分布 + 各维度对比。

    包含：
    - papers: 排序后的论文列表
    - scoreDistribution: 综合得分在 5 个区间的分布
    - dimensionStats: 各维度的 min/max/mean/median/std
    - topJournals: 排名前 5 的期刊及其平均得分
    """
    rank_result = rank_papers(db, topic, weights, paper_ids, top_k)

    # 得分分布
    bins = [
        ("0.0-0.2", 0.0, 0.2),
        ("0.2-0.4", 0.2, 0.4),
        ("0.4-0.6", 0.4, 0.6),
        ("0.6-0.8", 0.6, 0.8),
        ("0.8-1.0", 0.8, 1.01),
    ]
    distribution: list[dict] = []
    for label, lo, hi in bins:
        count = sum(1 for p in rank_result.papers if lo <= p.compositeScore < hi)
        distribution.append({"range": label, "count": count})

    # 各维度统计
    dim_keys = ["citations", "recency", "similarity", "journal"]
    dim_values: dict[str, list[float]] = {k: [] for k in dim_keys}
    for p in rank_result.papers:
        d = p.dimensions
        dim_values["citations"].append(d.citations)
        dim_values["recency"].append(d.recency)
        dim_values["similarity"].append(d.similarity)
        dim_values["journal"].append(d.journal)

    dim_stats: dict[str, DimensionStats] = {}
    for k in dim_keys:
        vals = dim_values[k]
        if not vals:
            dim_stats[k] = DimensionStats(min=0, max=0, mean=0, median=0, std=0)
        else:
            dim_stats[k] = DimensionStats(
                min=round(min(vals), 4),
                max=round(max(vals), 4),
                mean=round(statistics.mean(vals), 4),
                median=round(statistics.median(vals), 4),
                std=round(statistics.stdev(vals) if len(vals) >= 2 else 0.0, 4),
            )

    # 期刊排行（按出现次数 + 平均得分）
    journal_agg: dict[str, list[float]] = {}
    for p in rank_result.papers:
        j = p.journal or "Unknown"
        if j not in journal_agg:
            journal_agg[j] = []
        journal_agg[j].append(p.compositeScore)

    journal_list = sorted(
        journal_agg.items(),
        key=lambda x: (-len(x[1]), -statistics.mean(x[1])),
    )[:5]
    top_journals: list[dict] = [
        {
            "journal": name,
            "count": len(scores),
            "avgScore": round(statistics.mean(scores), 4),
        }
        for name, scores in journal_list
    ]

    return AnalysisReport(
        papers=rank_result.papers,
        total=rank_result.total,
        topic=topic,
        weights=weights,
        scoreDistribution=distribution,
        dimensionStats=dim_stats,
        topJournals=top_journals,
    )
