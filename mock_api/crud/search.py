"""论文搜索、混合检索与 RAG 上下文检索。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from ..author_utils import parse_authors
from ..models import Paper as PaperORM
from ..schemas import Paper
from ..semantic_search import embed_text, is_available
from .embeddings import count_embeddings, semantic_search_by_vector
from .papers import get_favorite_paper_ids, paper_to_schema


def suggest_papers(
    db: Session,
    q: str,
    limit: int = 5,
    hybrid: bool = False,
) -> list[dict]:
    """搜索建议：FTS5 全文检索，按 BM25 相关性排序，附带高亮片段。

    - q 长度 < 2 直接返回空列表（避免短词走 FTS 索引，FTS5 默认 token 长度≥3）。
    - MATCH 默认覆盖所有列（title / abstract / authors），即同时支持标题、摘要、作者检索。
    - 使用 snippet() 生成匹配片段，前后缀用 <mark></mark>，便于前端高亮；
      snippet 第二参数 -1 表示自动选择最佳匹配列。
    - 若 FTS 表为空或查询出错，防御性返回空列表。

    Args:
        hybrid: True 时启用混合检索（FTS5 + 向量 + RRF）。向量检索不可用
                时自动降级为纯 FTS5。混合模式下高亮片段取自 FTS5 命中行，
                未命中 FTS5 的向量召回结果 highlight 为空字符串。

    返回 [{"id": ..., "title": ..., "highlight": ..., "authors": [...]}, ...]，仅取前 limit 条。
    """
    q = (q or "").strip()
    if not q:
        return []

    # FTS5 MATCH: try phrase matching first; fall back to OR token matching on 0 results
    fts_query = f'"{q}"'

    sql = text(
        """
        SELECT p.id            AS id,
               p.title         AS title,
               p.authors       AS authors,
               snippet(paper_fts, -1, '<mark>', '</mark>', '...', 50) AS highlight
        FROM paper_fts
        JOIN papers p ON p.id = paper_fts.paper_id
        WHERE paper_fts MATCH :q
          AND p.category != 'report'
        ORDER BY bm25(paper_fts)
        LIMIT :limit
        """
    )

    eff_limit = limit * 2 if hybrid else limit
    rows: list[Row[Any]] = []
    try:
        rows = list(db.execute(sql, {"q": fts_query, "limit": eff_limit}).all())
    except Exception:  # noqa: BLE001 - FTS5 退化 - 异常时返回空列表而非 500
        rows = []

    # Fallback: phrase matching returned 0 results → retry with OR token matching
    if not rows:
        tokens = [t.strip() for t in q.split() if len(t.strip()) >= 2]
        if len(tokens) > 1:
            or_query = " OR ".join(f'"{t}"' for t in tokens)
            try:
                rows = list(db.execute(sql, {"q": or_query, "limit": eff_limit}).all())
            except Exception:  # noqa: BLE001 - FTS5 退化 - 异常时返回空列表而非 500
                rows = []

    fts_hits = {
        r.id: {
            "id": r.id,
            "title": r.title,
            "authors": parse_authors(r.authors),
            "highlight": r.highlight,
        }
        for r in rows
    }

    # 非混合模式：直接返回 FTS5 结果
    if not hybrid:
        return list(fts_hits.values())[:limit]

    # 混合模式：FTS5 + 向量 + RRF
    vec_ranked: list[str] = []
    if is_available() and count_embeddings(db) > 0:
        qvec = embed_text(q)
        if qvec:
            vec_hits = semantic_search_by_vector(db, qvec, top_k=limit * 2)
            vec_ranked = [pid for pid, _sim in vec_hits]

    fts_ranked = list(fts_hits.keys())
    if not fts_ranked and not vec_ranked:
        return []

    # RRF 融合
    if fts_ranked and vec_ranked:
        fused = rrf_fuse(fts_ranked, vec_ranked, k=60, top_n=limit)
        ranked_ids = [pid for pid, _score in fused]
    elif vec_ranked:
        ranked_ids = vec_ranked[:limit]
    else:
        ranked_ids = fts_ranked[:limit]

    # 加载论文元数据（向量召回但未命中 FTS5 的需要补查 title/authors）
    missing_ids = [pid for pid in ranked_ids if pid not in fts_hits]
    extra_map: dict[str, dict] = {}
    if missing_ids:
        extra_rows = db.query(PaperORM).filter(PaperORM.id.in_(missing_ids)).all()
        for r in extra_rows:
            extra_map[r.id] = {
                "id": r.id,
                "title": r.title,
                "authors": parse_authors(r.authors),
                "highlight": "",  # 向量召回无 FTS5 高亮片段
            }

    result = []
    for pid in ranked_ids:
        hit = fts_hits.get(pid) or extra_map.get(pid)
        if hit:
            result.append(hit)
    return result


def rrf_fuse(
    fts_ranked: list[str],
    vec_ranked: list[str],
    k: int = 60,
    top_n: int = 10,
) -> list[tuple[str, float]]:
    """倒数排名融合（Reciprocal Rank Fusion）。

    将 FTS5 与向量两路检索结果按 RRF 公式合并：

        RRF(d) = Σ 1 / (k + rank_i(d))

    其中 rank_i(d) 为文档 d 在第 i 路结果中的排名（从 1 开始），
    k=60 为平滑常数（经验值，缓解高排名文档的过度优势）。
    仅出现在一路结果中的文档仍参与融合（单路贡献）。

    Args:
        fts_ranked: FTS5 检索结果 paper_id 列表（按相关性降序）。
        vec_ranked: 向量检索结果 paper_id 列表（按相似度降序）。
        k: RRF 平滑常数，默认 60。
        top_n: 返回前 N 条，默认 10。

    Returns:
        [(paper_id, rrf_score), ...] 按 RRF 分数降序，最多 top_n 条。
    """
    fts_rank = {pid: i + 1 for i, pid in enumerate(fts_ranked)}
    vec_rank = {pid: i + 1 for i, pid in enumerate(vec_ranked)}

    all_ids = set(fts_rank) | set(vec_rank)
    scored: list[tuple[str, float]] = []
    for pid in all_ids:
        score = 0.0
        if pid in fts_rank:
            score += 1.0 / (k + fts_rank[pid])
        if pid in vec_rank:
            score += 1.0 / (k + vec_rank[pid])
        scored.append((pid, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def hybrid_search_papers(
    db: Session,
    query: str,
    top_k: int = 10,
    fts_top: int = 20,
    vec_top: int = 20,
    paper_ids: list[str] | None = None,
) -> list[Paper]:
    """混合检索：FTS5 + 向量 + RRF 融合，返回 Top K 论文。

    流程：
    1. FTS5 关键词检索（含 authors 字段）取前 fts_top 条。
    2. 若 fastembed 可用且向量表非空：编码查询 → 余弦相似度取前 vec_top 条。
    3. RRF（k=60）融合两路结果，返回前 top_k 条。
    4. 若向量检索不可用，直接返回 FTS5 结果前 top_k 条（降级）。

    Args:
        query: 用户查询文本（自然语言）。
        top_k: 最终返回数量，默认 10。
        fts_top / vec_top: 两路检索的召回数量，默认各 20。
        paper_ids: 可选，限定检索范围（仅 FTS5 侧生效；向量侧不裁剪）。
    """
    from ..semantic_search import embed_text, is_available

    # 1. FTS5 关键词检索（suggest_papers 已覆盖 title/abstract/authors）
    fts_hits = suggest_papers(db, query, limit=fts_top)
    fts_ranked = [h["id"] for h in fts_hits]

    # 2. 向量检索（可选）
    vec_ranked: list[str] = []
    if is_available() and count_embeddings(db) > 0:
        qvec = embed_text(query)
        if qvec:
            vec_hits = semantic_search_by_vector(db, qvec, top_k=vec_top)
            vec_ranked = [pid for pid, _sim in vec_hits]

    # 3. RRF 融合；若两路均空，返回 []
    if not fts_ranked and not vec_ranked:
        return []

    # 若仅有一路结果，直接用该路排名（无需融合）
    if not vec_ranked:
        ranked_ids = fts_ranked[:top_k]
    elif not fts_ranked:
        ranked_ids = vec_ranked[:top_k]
    else:
        fused = rrf_fuse(fts_ranked, vec_ranked, k=60, top_n=top_k)
        ranked_ids = [pid for pid, _score in fused]

    # 4. 按 paper_ids 范围过滤（可选）
    if paper_ids:
        allowed = set(paper_ids)
        ranked_ids = [pid for pid in ranked_ids if pid in allowed]

    # 5. 加载论文 ORM 行并按融合顺序返回 schema
    if not ranked_ids:
        return []
    rows = (
        db.query(PaperORM).filter(PaperORM.id.in_(ranked_ids), PaperORM.category != "report").all()
    )
    paper_map: dict[str, PaperORM] = {r.id: r for r in rows}
    fav_ids = get_favorite_paper_ids(db)
    result = []
    for pid in ranked_ids:
        p = paper_map.get(pid)
        if p:
            result.append(paper_to_schema(p, favorited=pid in fav_ids))
    return result


def retrieve_context(
    db: Session, question: str, paper_ids: list[str], top_k: int = 3
) -> list[Paper]:
    """RAG 检索：复用 FTS5 + 向量 RRF 混合检索，避免全表加载。

    若指定了 paper_ids 则只在该范围内检索；否则全库检索。
    当查询词为空（纯按 paper_ids 取上下文）时，回退为按 id 排序的有界查询，
    不再执行 db.query(PaperORM).all() 全表扫描。
    """
    q = (question or "").strip()

    # 有查询词：直接路由到已有的混合检索（FTS5 + 向量 RRF），复用索引
    if q:
        # 通过 mock_api.crud 包导入以支持测试 monkeypatch crud.hybrid_search_papers
        from mock_api.crud import hybrid_search_papers

        return hybrid_search_papers(db, q, top_k=top_k, paper_ids=paper_ids or None)

    # 无查询词：按 paper_ids 取上下文（有界查询，避免全表扫描）
    query = db.query(PaperORM).filter(PaperORM.category != "report").order_by(PaperORM.id)
    if paper_ids:
        query = query.filter(PaperORM.id.in_(paper_ids))
    rows = query.limit(top_k).all()  # ✅ 只取 top_k 行
    return [paper_to_schema(p) for p in rows]
