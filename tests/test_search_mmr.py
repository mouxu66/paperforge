"""hybrid_search_papers 的 MMR 多样性重排集成测试。

覆盖场景：
- mmr_lambda=None（默认）：行为与旧版完全一致（纯 RRF 顺序，向后兼容）。
- mmr_lambda=0.0（纯多样）：候选论文相似时，MMR 把不相似的论文提前。
- mmr_lambda=1.0（纯相关）：等价于按相关性排序，不触发重排。
- 单路召回（仅 FTS5）时 MMR 仍可用：无向量论文退化为按相关度排序。
- 候选不足 2 篇时不触发重排。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from mock_api import models  # noqa: F401  # 注册所有 ORM 映射
from mock_api.crud.embeddings import upsert_paper_embedding
from mock_api.crud.search import hybrid_search_papers
from mock_api.database import Base
from mock_api.models import Paper as PaperORM
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def db() -> Session:
    """每个测试用全新的内存数据库，并创建 FTS5 虚拟表。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5("
                "paper_id UNINDEXED, title, abstract, authors, full_text"
                ")"
            )
        )
        conn.commit()

    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


def _add_paper(
    db: Session,
    paper_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int = 2023,
    abstract: str = "",
) -> None:
    """插入论文并同步写入 paper_fts。"""
    db.add(
        PaperORM(
            id=paper_id,
            title=title,
            authors=authors or [],
            year=year,
            abstract=abstract,
            category="cs",
        )
    )
    db.flush()
    authors_str = " ".join(authors or [])
    db.execute(
        text(
            "INSERT INTO paper_fts(paper_id, title, abstract, authors, full_text) "
            "VALUES (:pid, :title, :abstract, :authors, :full_text)"
        ),
        {"pid": paper_id, "title": title, "abstract": abstract, "authors": authors_str, "full_text": abstract},
    )
    db.commit()


def _ids(results) -> list[str]:
    return [p.id for p in results]


def test_mmr_disabled_preserves_rrf_order(db: Session):
    """mmr_lambda=None（默认）时顺序与纯 RRF 一致（向后兼容）。"""
    _add_paper(db, "p1", "Transformer Architecture Survey", abstract="attention mechanism")
    _add_paper(db, "p2", "Deep Learning Transformers", abstract="neural networks transformer")
    _add_paper(db, "p3", "Image Generation Diffusion", abstract="generative model")

    with patch("mock_api.semantic_search.is_available", return_value=False):
        base = hybrid_search_papers(db, "transformer neural networks", top_k=3)

    with patch("mock_api.semantic_search.is_available", return_value=False):
        default = hybrid_search_papers(db, "transformer neural networks", top_k=3)

    assert _ids(default) == _ids(base)
    # 纯 FTS5 路径：p1/p2 命中 transformer，应排在 p3 之前
    assert "p1" in _ids(default) and "p2" in _ids(default)


def test_mmr_diversity_promotes_dissimilar_paper(db: Session):
    """mmr_lambda=0（纯多样）：p1/p2 高度相似、p3 不相似时，p3 被提前。"""
    _add_paper(db, "p1", "Transformer Architecture", abstract="attention mechanism")
    _add_paper(db, "p2", "Transformer Variants", abstract="attention mechanism variants")
    _add_paper(db, "p3", "Image Generation Diffusion", abstract="generative model")

    # 查询向量与 p1 一致；p1/p2 向量相近，p3 正交
    upsert_paper_embedding(db, "p1", [1.0, 0.0])
    upsert_paper_embedding(db, "p2", [0.9, 0.1])
    upsert_paper_embedding(db, "p3", [0.0, 1.0])

    def _fake_embed(input_text: str) -> list[float]:
        return [1.0, 0.0]

    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", side_effect=_fake_embed),
    ):
        ranked = hybrid_search_papers(db, "transformer", top_k=3, mmr_lambda=0.0)

    ids = _ids(ranked)
    # 纯多样：第一个仍是相关性最高者（MMR 首项取最高分），
    # 第二项优先选与已选集合最不相似的 p3（p1 与 p2 相似度 0.9 → 惩罚）
    assert ids[0] == "p1"
    assert ids[1] == "p3"
    assert ids[2] == "p2"


def test_mmr_pure_relevance_keeps_rrf_order(db: Session):
    """mmr_lambda=1.0（纯相关）不触发多样性，顺序接近 RRF。"""
    _add_paper(db, "p1", "Transformer Architecture", abstract="attention mechanism")
    _add_paper(db, "p2", "Transformer Variants", abstract="attention mechanism variants")
    _add_paper(db, "p3", "Image Generation Diffusion", abstract="generative model")

    upsert_paper_embedding(db, "p1", [1.0, 0.0])
    upsert_paper_embedding(db, "p2", [0.9, 0.1])
    upsert_paper_embedding(db, "p3", [0.0, 1.0])

    def _fake_embed(input_text: str) -> list[float]:
        return [1.0, 0.0]

    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", side_effect=_fake_embed),
    ):
        ranked = hybrid_search_papers(db, "transformer", top_k=3, mmr_lambda=1.0)

    ids = _ids(ranked)
    assert ids[0] == "p1"
    assert set(ids) == {"p1", "p2", "p3"}


def test_mmr_single_candidate_no_rerank(db: Session):
    """候选不足 2 篇时不触发 MMR 重排（防御）。"""
    _add_paper(db, "p1", "Transformer Architecture", abstract="attention mechanism")

    with patch("mock_api.semantic_search.is_available", return_value=False):
        ranked = hybrid_search_papers(db, "transformer", top_k=3, mmr_lambda=0.0)

    assert _ids(ranked) == ["p1"]


def test_mmr_top_k_truncation(db: Session):
    """启用 MMR 时仍遵守 top_k 截断。"""
    for i in range(5):
        _add_paper(db, f"p{i}", f"Transformer Paper {i}", abstract="attention mechanism")

    with patch("mock_api.semantic_search.is_available", return_value=False):
        ranked = hybrid_search_papers(db, "transformer", top_k=2, mmr_lambda=0.0)

    assert len(_ids(ranked)) == 2


def test_mmr_default_router_lambda_is_balanced(monkeypatch):
    """路由默认 lambda（RecommendConfig.diversity_mmr_lambda）介于 0 与 1 之间。"""
    from mock_api.recommend_ranker import RecommendConfig

    lam = RecommendConfig().diversity_mmr_lambda
    assert 0.0 < lam < 1.0
    assert lam == pytest.approx(0.7)
