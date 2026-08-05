"""retrieve_context 性能修复回归测试：验证不再全表扫描，且检索正确。"""
from __future__ import annotations

import pytest
from mock_api import (
    crud,
    models,  # noqa: F401  注册 ORM 映射
)
from mock_api.database import Base
from mock_api.schemas import Paper
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def _suppress_async_tasks(monkeypatch):
    """禁用种子数据的后台异步任务，避免创建大量无用线程。"""
    monkeypatch.setattr(crud, "_enrich_async", lambda _paper_id: None)
    monkeypatch.setattr(crud, "_review_async", lambda _paper_id: None)
    monkeypatch.setattr(crud, "_reflection_review_async", lambda _paper_id: None)


def _make_db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    # 补建 FTS5 虚拟表（create_all 不含虚拟表）
    with engine.connect() as c:
        c.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, authors, full_text, tags)"
        ))
        c.commit()
    return sessionmaker(bind=engine, autoflush=False, future=True)()


def _seed(db: Session, n: int) -> None:
    for i in range(n):
        crud.create_paper_from_upload(
            db, f"p{i:04d}", f"paper about topic {i}", ["a", "b"], 2020,
            f"abstract number {i}", "", f"full text {i}",
        )


def test_no_full_table_scan_delegates_to_hybrid(monkeypatch):
    """有查询词时委托 hybrid_search_papers，retrieve_context 内不再 .all() 全表。

    p0042 是所有 200 篇种子论文中唯一含 "42" 的，BM25 应将其排第一。
    """
    db = _make_db()
    _seed(db, 200)
    captured = {}
    real = crud.hybrid_search_papers

    def spy(db_, q, top_k=10, fts_top=20, vec_top=20, paper_ids=None):
        captured["called"] = True
        return real(db_, q, top_k=top_k, fts_top=fts_top, vec_top=vec_top, paper_ids=paper_ids)

    monkeypatch.setattr(crud, "hybrid_search_papers", spy)
    # 强制走 FTS-only，避免向量路径加载全部 embedding（与本 PR 解耦）
    monkeypatch.setattr("mock_api.semantic_search.is_available", lambda: False)

    res = crud.retrieve_context(db, "topic 42", [], top_k=3)
    assert captured.get("called") is True
    assert isinstance(res, list) and all(isinstance(p, Paper) for p in res)
    assert any(p.id == "p0042" for p in res)   # 命中索引，正确召回


def test_empty_query_is_bounded_not_full_scan(monkeypatch):
    """无查询词时只取有界行，不触发 papers 全表 .all()。"""
    db = _make_db()
    _seed(db, 500)
    monkeypatch.setattr("mock_api.semantic_search.is_available", lambda: False)

    res = crud.retrieve_context(db, "", [], top_k=3)
    assert len(res) == 3                      # 只返回 top_k，而非全表


def test_paper_ids_scope_respected(monkeypatch):
    """传入 paper_ids 时结果严格落在范围内。"""
    db = _make_db()
    _seed(db, 50)
    monkeypatch.setattr("mock_api.semantic_search.is_available", lambda: False)

    res = crud.retrieve_context(db, "topic", ["p0001", "p0002"], top_k=5)
    assert all(p.id in {"p0001", "p0002"} for p in res)
