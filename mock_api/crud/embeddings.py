"""论文向量嵌入 CRUD。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import PaperEmbedding as PaperEmbeddingORM
from ..semantic_search import (
    EMBEDDING_DIM,
    cosine_similarity,
    deserialize_vector,
    serialize_vector,
)


def upsert_paper_embedding(db: Session, paper_id: str, vec: list[float]) -> None:
    """新增或更新论文向量（upsert）。"""
    row = db.query(PaperEmbeddingORM).filter(PaperEmbeddingORM.paper_id == paper_id).first()
    serialized = serialize_vector(vec)
    if row:
        row.embedding = serialized
        row.dim = len(vec) or EMBEDDING_DIM
    else:
        db.add(
            PaperEmbeddingORM(
                paper_id=paper_id,
                embedding=serialized,
                dim=len(vec) or EMBEDDING_DIM,
            )
        )
    db.commit()


def get_paper_embedding(db: Session, paper_id: str) -> list[float] | None:
    """取论文的代表向量（标题+摘要），用于感悟报告 fidelity 比对。

    对应 reflection_pipeline._get_paper_text_emb 的调用；此前该函数未实现，
    导致 _get_paper_text_emb 始终返回 None、fidelity 算不出来（显示成 0%/未计算）。
    返回反序列化后的 384 维向量；无记录则返回 None（compute_fidelity 会退化为现场 embed）。
    """
    row = db.query(PaperEmbeddingORM).filter(PaperEmbeddingORM.paper_id == paper_id).first()
    if row is None or not row.embedding:
        return None
    try:
        return deserialize_vector(row.embedding)
    except Exception:  # noqa: BLE001 - embedding upsert - 失败留待下次重试
        return None


def get_all_embeddings(db: Session) -> list[tuple[str, str]]:
    """获取所有论文向量 (paper_id, serialized_embedding)。空表返回 []。"""
    rows = db.query(PaperEmbeddingORM.paper_id, PaperEmbeddingORM.embedding).all()
    return [(r[0], r[1]) for r in rows]


def count_embeddings(db: Session) -> int:
    """返回已生成向量的论文数。"""
    return db.query(PaperEmbeddingORM).count() or 0


def semantic_search_by_vector(
    db: Session, query_vec: list[float], top_k: int = 10
) -> list[tuple[str, float]]:
    """向量检索：计算查询向量与所有论文向量的余弦相似度，返回 Top K。

    返回 [(paper_id, similarity), ...]。空表返回 []。
    注意：此函数不区分 category，返回全量 paper_id（含 report）。
    调用方如需排除感悟报告，请在回查 PaperORM 时自行过滤。
    """
    rows = get_all_embeddings(db)
    if not rows:
        return []

    scored: list[tuple[str, float]] = []
    for paper_id, emb_str in rows:
        vec = deserialize_vector(emb_str)
        if not vec:
            continue
        sim = cosine_similarity(query_vec, vec)
        scored.append((paper_id, sim))

    # 按相似度降序取前 top_k
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
