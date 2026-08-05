"""测试 mock_api/crud/analysis.py 中的 recommend_papers_by_content。

覆盖场景：
- 空内容 / 仅停用词 → 返回空列表
- FTS5 关键词检索路径（向量不可用时降级）
- 向量语义检索路径（FTS5 无结果时）
- FTS5 + 向量 RRF 融合路径
- 结果元数据兜底查库
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from mock_api import models  # noqa: F401  # 注册所有 ORM 映射
from mock_api.crud.analysis import recommend_papers_by_content
from mock_api.crud.embeddings import upsert_paper_embedding
from mock_api.database import Base
from mock_api.models import Paper as PaperORM
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """每个测试用全新的内存数据库，并创建 FTS5 虚拟表。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    # 创建 FTS5 虚拟表（与 database.py 中保持一致）
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
) -> PaperORM:
    """插入论文并同步写入 paper_fts。"""
    paper = PaperORM(
        id=paper_id,
        title=title,
        authors=authors or [],
        year=year,
        abstract=abstract,
        category="cs",
    )
    db.add(paper)
    db.flush()

    # 同步写入 FTS 索引
    authors_str = " ".join(authors or [])
    db.execute(
        text(
            "INSERT INTO paper_fts(paper_id, title, abstract, authors, full_text) "
            "VALUES (:pid, :title, :abstract, :authors, :full_text)"
        ),
        {
            "pid": paper_id,
            "title": title,
            "abstract": abstract,
            "authors": authors_str,
            "full_text": abstract,
        },
    )
    db.commit()
    return paper


def test_recommend_empty_content_returns_empty(db: Session):
    """空内容 → 返回空列表。"""
    assert recommend_papers_by_content(db, "") == []
    assert recommend_papers_by_content(db, "   ") == []


def test_recommend_only_stopwords_returns_empty(db: Session):
    """内容只有停用词时无法提取关键词 → 返回空列表。"""
    assert recommend_papers_by_content(db, "the and of") == []


def test_recommend_fts_only_when_vector_unavailable(db: Session):
    """向量不可用时仅走 FTS5 路径。"""
    _add_paper(db, "p1", "Transformer Architecture for Neural Networks")
    _add_paper(db, "p2", "Convolutional Neural Networks for Image Recognition")

    with patch("mock_api.semantic_search.is_available", return_value=False):
        results = recommend_papers_by_content(db, "Transformer neural networks", top_k=5)

    assert len(results) == 2
    ids = [r["id"] for r in results]
    assert "p1" in ids
    assert "p2" in ids
    # 排名第一的应包含 transformer
    assert results[0]["id"] == "p1"
    assert results[0]["score"] > 0
    assert "title" in results[0]
    assert "authors" in results[0]
    assert "year" in results[0]


def test_recommend_vector_only_when_fts_empty(db: Session):
    """FTS5 无结果但向量可用时，走向量路径。"""
    # 论文的 title/abstract 与查询关键词不重叠，确保 FTS5 无命中
    _add_paper(db, "p1", "Paper One", abstract="biology research genetics")
    _add_paper(db, "p2", "Paper Two", abstract="chemistry organic reactions")

    # 为论文预置向量
    upsert_paper_embedding(db, "p1", [1.0, 0.0, 0.0])
    upsert_paper_embedding(db, "p2", [0.0, 1.0, 0.0])

    def _fake_embed(input_text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", side_effect=_fake_embed),
    ):
        results = recommend_papers_by_content(db, "machine learning paper", top_k=5)

    assert len(results) == 2
    # p1 与查询向量完全一致，应排第一
    assert results[0]["id"] == "p1"
    assert results[0]["score"] > 0


def test_recommend_hybrid_fusion_rrf(db: Session):
    """FTS5 和向量都有结果时，RRF 融合并返回 Top K。"""
    _add_paper(db, "p1", "Transformer Architecture", abstract="neural networks")
    _add_paper(db, "p2", "Neural Networks Survey", abstract="deep learning")

    upsert_paper_embedding(db, "p1", [1.0, 0.0, 0.0])
    upsert_paper_embedding(db, "p2", [0.0, 1.0, 0.0])

    def _fake_embed(input_text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", side_effect=_fake_embed),
    ):
        results = recommend_papers_by_content(db, "Transformer neural networks", top_k=1)

    assert len(results) == 1
    # p1 在 FTS 和向量中都更相关
    assert results[0]["id"] == "p1"


def test_recommend_top_k_truncation(db: Session):
    """top_k 正确截断结果数量。"""
    for i in range(5):
        _add_paper(db, f"p{i}", f"Paper {i} about neural networks")

    with patch("mock_api.semantic_search.is_available", return_value=False):
        results = recommend_papers_by_content(db, "neural networks", top_k=3)

    assert len(results) == 3


def test_recommend_result_keys(db: Session):
    """返回结果包含预期字段。"""
    _add_paper(db, "p1", "Deep Learning Revolution", authors=["LeCun", "Bengio"], year=2020)

    with patch("mock_api.semantic_search.is_available", return_value=False):
        results = recommend_papers_by_content(db, "Deep Learning Revolution", top_k=5)

    assert len(results) == 1
    r = results[0]
    assert r["id"] == "p1"
    assert r["title"] == "Deep Learning Revolution"
    assert r["authors"] == ["LeCun", "Bengio"]
    assert r["year"] == 2020
    assert isinstance(r["score"], float)


def test_recommend_ghost_id_from_vector_is_skipped(db: Session):
    """向量返回的 id 在数据库中不存在时，兜底查库为空，应跳过。"""
    upsert_paper_embedding(db, "p1", [1.0, 0.0, 0.0])

    # 模拟向量检索返回一个不存在的 paper_id
    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", return_value=[1.0, 0.0, 0.0]),
        patch("mock_api.crud.analysis.semantic_search_by_vector", return_value=[("ghost_id", 0.9)]),
    ):
        results = recommend_papers_by_content(db, "machine learning paper", top_k=5)

    assert results == []


def test_recommend_no_fts_no_vector_returns_empty(db: Session):
    """FTS5 和向量都为空时 → 返回空列表。"""
    with patch("mock_api.semantic_search.is_available", return_value=False):
        results = recommend_papers_by_content(db, "machine learning paper", top_k=5)

    assert results == []


def test_recommend_hybrid_rrf_reranking(db: Session):
    """FTS5 和向量结果部分重叠时，RRF 融合提升重叠文档排名。"""
    _add_paper(db, "p1", "Transformer Survey", abstract="attention mechanism")
    _add_paper(db, "p2", "BERT Paper", abstract="language model")
    _add_paper(db, "p3", "GPT Paper", abstract="generative pretraining")

    upsert_paper_embedding(db, "p1", [1.0, 0.0, 0.0])
    upsert_paper_embedding(db, "p2", [0.0, 1.0, 0.0])
    upsert_paper_embedding(db, "p3", [0.0, 0.0, 1.0])

    def _fake_embed(input_text: str) -> list[float]:
        # 查询向量与 p1 一致
        return [1.0, 0.0, 0.0]

    with (
        patch("mock_api.semantic_search.is_available", return_value=True),
        patch("mock_api.semantic_search.embed_text", side_effect=_fake_embed),
    ):
        results = recommend_papers_by_content(db, "Transformer attention", top_k=2)

    ids = [r["id"] for r in results]
    # p1 在 FTS 和向量中都出现，应排第一
    assert ids[0] == "p1"
    assert len(ids) == 2


def test_recommend_hybrid_with_paper_ids_filter(db: Session):
    """hybrid_search_papers 的 paper_ids 参数正确过滤结果。"""
    from mock_api.crud.search import hybrid_search_papers

    _add_paper(db, "p1", "Transformer Survey")
    _add_paper(db, "p2", "BERT Paper")

    # 查询词同时匹配 p1 和 p2，但 paper_ids 只保留 p2
    with patch("mock_api.semantic_search.is_available", return_value=False):
        results = hybrid_search_papers(db, "Survey Paper", top_k=5, paper_ids=["p2"])

    assert len(results) == 1
    assert results[0].id == "p2"
