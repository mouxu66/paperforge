"""验证 list_papers / get_papers 查询计划（EXPLAIN QUERY PLAN）。

目标：
- 关键词 + 分类 + 排序 + 分页 组合查询不走全表扫描
- FTS5 命中后通过 papers.id IN (...) 回表
- 排序能利用 ix_papers_year 等索引
"""

from __future__ import annotations

import pytest
from mock_api.crud.papers import (
    _apply_like_filter,
    _fts_match_count,
    _fts_match_subquery,
    _safe_fts_query,
    create_paper_from_upload,
)
from mock_api.database import Base
from mock_api.models import Paper as PaperORM  # noqa: F401  注册 ORM 映射
from sqlalchemy import text


@pytest.fixture
def db():
    """每个测试用全新的内存数据库，并写入足够测试的论文。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    # 手动创建 FTS5 虚拟表（与生产 schema 一致）
    with engine.connect() as c:
        c.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5("
                "paper_id UNINDEXED, title, abstract, authors, full_text, tags)"
            )
        )
        c.commit()

    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionFactory()

    # 写入测试数据
    for i in range(50):
        paper_id = f"p{i:04d}"
        create_paper_from_upload(
            session,
            paper_id=paper_id,
            title=f"machine learning paper {i}",
            authors=["Alice", "Bob"],
            year=2020 + (i % 5),
            abstract=f"abstract about neural networks and deep learning {i}",
            pdf_url="",
            full_text=f"full text {i}",
        )

    yield session
    session.close()


def _explain_query_plan(session, sql: str) -> list[str]:
    """执行 EXPLAIN QUERY PLAN 并返回 detail 列表。"""
    rows = session.execute(text(f"EXPLAIN QUERY PLAN {sql}")).all()
    return [row[-1] for row in rows]


def _get_papers_sql(session, **kwargs) -> str:
    """构造 get_papers 对应的 SQL（不执行），用于 EXPLAIN。"""
    # 通过调用 get_papers 拿到结果的同时，也拿到它内部的 query 对象比较麻烦。
    # 这里复现 get_papers 的查询逻辑，确保 EXPLAIN 的是同一条 SQL。

    keyword = kwargs.get("keyword")
    category = kwargs.get("category")
    sort = kwargs.get("sort")
    source = kwargs.get("source")
    page = kwargs.get("page", 1)
    page_size = kwargs.get("page_size", 8)

    kw = (keyword or "").strip().lower()

    query = session.query(PaperORM)

    if category and category != "all":
        query = query.filter(PaperORM.category == category)

    if source and source != "all":
        query = query.filter(PaperORM.source == source)

    if kw:
        safe_kw = _safe_fts_query(kw)
        if safe_kw:
            fts_count = _fts_match_count(session, safe_kw)
            if fts_count > 0:
                query = query.filter(PaperORM.id.in_(_fts_match_subquery(safe_kw)))
            elif fts_count == 0:
                query = _apply_like_filter(query, kw)
            else:
                query = _apply_like_filter(query, kw)
        else:
            query = _apply_like_filter(query, kw)

    sort_map = {
        "year_desc": PaperORM.year.desc(),
        "year_asc": PaperORM.year.asc(),
        "citations_desc": PaperORM.citations.desc(),
        "chunks_desc": PaperORM.chunk_count.desc(),
    }
    if sort in sort_map:
        query = query.order_by(sort_map[sort])

    page_rows = query.offset((page - 1) * page_size).limit(page_size)
    return str(page_rows.statement.compile(compile_kwargs={"literal_binds": True}))


def test_keyword_and_category_uses_index(db):
    """关键词 + 分类过滤应使用 papers.id 主键/索引，不走全表扫描。"""
    sql = _get_papers_sql(db, keyword="machine", category="upload", sort="year_desc", page=1, page_size=8)
    plan = _explain_query_plan(db, sql)
    plan_text = "\n".join(plan)

    # 不允许出现 SCAN TABLE papers（全表扫描）
    assert "SCAN TABLE papers" not in plan_text, f"发现全表扫描: {plan_text}"
    # 期望使用 papers 主键或索引（SQLite EXPLAIN 输出格式为 "SEARCH papers USING INDEX ..."）
    assert "SEARCH papers" in plan_text, f"未使用 papers 索引: {plan_text}"


def test_keyword_with_fts_uses_paper_ids(db):
    """FTS5 命中后应通过 papers.id IN (...) 回表。"""
    # 先确认 FTS5 能命中
    rows = db.execute(text("SELECT paper_id FROM paper_fts WHERE paper_fts MATCH 'machine'")).all()
    assert rows, "FTS5 未命中，无法验证回表计划"

    sql = _get_papers_sql(db, keyword="machine", sort="year_desc")
    plan = _explain_query_plan(db, sql)
    plan_text = "\n".join(plan)

    assert "SEARCH papers" in plan_text, f"未使用 papers 索引: {plan_text}"
    assert "SCAN TABLE papers" not in plan_text, f"发现全表扫描: {plan_text}"


def test_sort_by_year_uses_index(db):
    """按 year 排序应使用 ix_papers_year 索引（或至少不走全表扫描）。"""
    sql = _get_papers_sql(db, category="upload", sort="year_desc", page=1, page_size=8)
    plan = _explain_query_plan(db, sql)
    plan_text = "\n".join(plan)

    assert "SCAN TABLE papers" not in plan_text, f"发现全表扫描: {plan_text}"


def test_get_papers_real_total_and_pagination(db):
    """真实调用 get_papers 并校验 total / items / 分页行为。"""
    from mock_api.crud.papers import get_papers

    # 第一页
    items, total = get_papers(db, keyword="machine", category="interdisciplinary", sort="year_desc", page=1, page_size=8)
    assert total == 50, f"期望 total=50，实际 {total}"
    assert len(items) == 8
    # 排序校验：year 降序
    years = [p.year for p in items]
    assert years == sorted(years, reverse=True), f"year 降序校验失败: {years}"

    # 最后一页
    last_items, last_total = get_papers(
        db, keyword="machine", category="interdisciplinary", sort="year_desc", page=7, page_size=8
    )
    assert last_total == 50
    assert len(last_items) == 2  # 50 = 6*8 + 2
    assert last_total == total
