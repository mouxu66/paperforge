"""PaperForge mock 后端数据库操作函数（CRUD）。

所有查询/过滤/排序/分页逻辑集中于此，路由层只做参数透传。
为与原内存版行为完全一致，关键词过滤（含 JSON 列 authors/tags）
与排序在 Python 侧完成；分类过滤在 SQL 侧完成以减少传输量。
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .models import (
    Chapter as ChapterORM,
    ChapterContinuation as ChapterContinuationORM,
    ChapterVersion as ChapterVersionORM,
    Favorite,
    Paper as PaperORM,
    PaperNote as PaperNoteORM,
    UserTemplate as UserTemplateORM,
    WritingProject as WritingProjectORM,
    WritingSnapshot as WritingSnapshotORM,
)
from .schemas import (
    ChapterCreate,
    ChapterResponse,
    ChapterRestoreResponse,
    ChapterTreeNode,
    ChapterUpdate,
    ChapterVersionDetail,
    ChapterVersionResponse,
    DailyWordTrend,
    ExportReference,
    LibraryStats,
    NoteCreate,
    NoteResponse,
    NoteUpdate,
    Paper,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    TemplateChapter,
    TemplateResponse,
    UserTemplateCreate,
    UserTemplateResponse,
    WordCountItem,
    WordCountResponse,
    WritingStatsResponse,
)
from .data import SEED_PAPERS, TEMPLATES


# ---------------------------------------------------------------------------
# ORM -> Pydantic 转换
# ---------------------------------------------------------------------------
def paper_to_schema(p: PaperORM, favorited: bool = False) -> Paper:
    """将 ORM 行转换为 camelCase 的 Pydantic 响应模型。"""
    return Paper(
        id=p.id,
        title=p.title,
        authors=list(p.authors or []),
        year=p.year,
        abstract=p.abstract or "",
        category=p.category,
        tags=list(p.tags or []),
        citations=p.citations,
        chunkCount=p.chunk_count,
        indexSize=p.index_size,
        pdfUrl=p.pdf_url or "",
        source=p.source,
        journal=p.journal or "",
        favorited=favorited,
        # B2: Semantic Scholar 富化字段（ORM 列可为 None）
        influentialCitations=p.influential_citations,
        fieldsOfStudy=list(p.fields_of_study) if p.fields_of_study else None,
    )


# ---------------------------------------------------------------------------
# 收藏集合
# ---------------------------------------------------------------------------
def get_favorite_paper_ids(db: Session) -> set[str]:
    rows = db.query(Favorite.paper_id).all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# 论文查询
# ---------------------------------------------------------------------------
def _keyword_match(p: PaperORM, kw: str) -> bool:
    if kw in (p.title or "").lower():
        return True
    if kw in (p.id or "").lower():
        return True
    if any(kw in (a or "").lower() for a in (p.authors or [])):
        return True
    if any(kw in (t or "").lower() for t in (p.tags or [])):
        return True
    return False


def get_papers(
    db: Session,
    keyword: Optional[str],
    category: Optional[str],
    sort: Optional[str],
    page: int,
    page_size: int,
) -> tuple[list[Paper], int]:
    query = db.query(PaperORM)

    # 分类过滤（SQL 侧）
    if category and category != "all":
        query = query.filter(PaperORM.category == category)

    rows = query.all()

    # 关键词过滤（Python 侧，覆盖 JSON 列）
    kw = (keyword or "").strip().lower()
    if kw:
        rows = [p for p in rows if _keyword_match(p, kw)]

    # 排序（Python 侧）
    sort = sort or "year_desc"
    if sort == "year_desc":
        rows.sort(key=lambda p: p.year, reverse=True)
    elif sort == "year_asc":
        rows.sort(key=lambda p: p.year)
    elif sort == "citations_desc":
        rows.sort(key=lambda p: p.citations, reverse=True)
    elif sort == "chunks_desc":
        rows.sort(key=lambda p: p.chunk_count, reverse=True)

    total = len(rows)

    # 分页
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    fav_ids = get_favorite_paper_ids(db)
    items = [paper_to_schema(p, favorited=p.id in fav_ids) for p in page_rows]
    return items, total


def get_paper(db: Session, paper_id: str) -> Optional[Paper]:
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return None
    fav_ids = get_favorite_paper_ids(db)
    return paper_to_schema(p, favorited=p.id in fav_ids)


# ---------------------------------------------------------------------------
# 收藏操作
# ---------------------------------------------------------------------------
def get_favorite_papers(db: Session) -> list[Paper]:
    rows = (
        db.query(PaperORM)
        .join(Favorite, Favorite.paper_id == PaperORM.id)
        .order_by(Favorite.id.desc())
        .all()
    )
    return [paper_to_schema(p, favorited=True) for p in rows]


def add_favorite(db: Session, paper_id: str) -> bool:
    """添加收藏。返回 True 表示论文存在且已收藏；论文不存在返回 False。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return False
    exists = (
        db.query(Favorite)
        .filter(Favorite.paper_id == paper_id)
        .first()
    )
    if not exists:
        db.add(Favorite(paper_id=paper_id))
        db.commit()
    return True


def remove_favorite(db: Session, paper_id: str) -> bool:
    """取消收藏。返回 True 表示已删除；未收藏返回 False。"""
    row = (
        db.query(Favorite)
        .filter(Favorite.paper_id == paper_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 搜索建议（FTS5 全文索引版）
# ---------------------------------------------------------------------------
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
    if len(q) < 2:
        return []

    # FTS5 MATCH 语法：空格分词为 AND；这里直接透传原始 q。
    # 对包含特殊字符的查询做最简转义：用双引号包裹整体，按短语匹配。
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
        ORDER BY bm25(paper_fts)
        LIMIT :limit
        """
    )

    try:
        rows = db.execute(sql, {"q": fts_query, "limit": limit * 2 if hybrid else limit}).all()
    except Exception:
        # FTS 表不存在 / 查询语法错误等，防御性返回空
        rows = []

    fts_hits = {
        r.id: {
            "id": r.id,
            "title": r.title,
            "authors": list(r.authors or []),
            "highlight": r.highlight,
        }
        for r in rows
    }

    # 非混合模式：直接返回 FTS5 结果
    if not hybrid:
        return list(fts_hits.values())[:limit]

    # 混合模式：FTS5 + 向量 + RRF
    from .semantic_search import embed_text, is_available

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
                "authors": list(r.authors or []),
                "highlight": "",  # 向量召回无 FTS5 高亮片段
            }

    result = []
    for pid in ranked_ids:
        hit = fts_hits.get(pid) or extra_map.get(pid)
        if hit:
            result.append(hit)
    return result


# ---------------------------------------------------------------------------
# PDF 上传入库
# ---------------------------------------------------------------------------
def create_paper_from_upload(
    db: Session,
    paper_id: str,
    title: str,
    authors: list[str],
    year: int,
    abstract: str,
    pdf_url: str = "",
    full_text: str = "",
) -> Paper:
    """将解析后的 PDF 元数据写入 papers 表（source='upload'）。

    若 paper_id 已存在则更新，否则新增。
    B1：同时写入 full_text 字段并同步 FTS5 索引，确保上传后立即可检索全文。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if p is None:
        p = PaperORM(
            id=paper_id,
            title=title,
            authors=list(authors),
            abstract=abstract,
            category="upload",
            tags=[],
            year=year,
            journal="",
            pdf_url=pdf_url,
            citations=0,
            chunk_count=0,
            index_size=0,
            source="upload",
            full_text=full_text or None,
        )
        db.add(p)
    else:
        p.title = title
        p.authors = list(authors)
        p.abstract = abstract
        p.year = year
        p.full_text = full_text or None
    db.commit()
    db.refresh(p)

    # B1: 同步 FTS5 索引（INSERT OR REPLACE 覆盖已有记录）
    try:
        authors_str = " ".join(authors or [])
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text)"
            ),
            {
                "pid": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors_str,
                "full_text": full_text or "",
            },
        )
        db.commit()
    except Exception:
        # FTS 表可能未就绪，忽略同步失败不影响主流程
        db.rollback()

    return paper_to_schema(p, favorited=False)


# ---------------------------------------------------------------------------
# 外部论文导入（arXiv 等）
# ---------------------------------------------------------------------------
def import_external_paper(
    db: Session,
    paper_id: str,
    title: str,
    authors: list[str],
    year: int,
    abstract: str,
    pdf_url: str = "",
    source: str = "arxiv",
    category: str = "arxiv",
    tags: list[str] | None = None,
) -> Paper:
    """Import an external paper (e.g., from arXiv) into the database.

    Also syncs the FTS5 index by inserting into paper_fts table.
    If paper_id already exists, updates the record instead of duplicating.
    """
    if tags is None:
        tags = []

    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if p is None:
        p = PaperORM(
            id=paper_id,
            title=title,
            authors=list(authors),
            abstract=abstract,
            category=category,
            tags=list(tags),
            year=year,
            journal="",
            pdf_url=pdf_url,
            citations=0,
            chunk_count=0,
            index_size=0,
            source=source,
        )
        db.add(p)
    else:
        p.title = title
        p.authors = list(authors)
        p.abstract = abstract
        p.year = year
        p.category = category
        p.tags = list(tags)
        p.pdf_url = pdf_url
        p.source = source
    db.commit()
    db.refresh(p)

    # 同步 FTS5 索引：INSERT OR IGNORE 防止重复 paper_id
    # authors 用空格连接为字符串以支持 MATCH 全文检索
    # B1：full_text 字段同步写入（arXiv 导入通常无全文，存空字符串）
    try:
        authors_str = " ".join(authors or [])
        full_text_value = (p.full_text if p and hasattr(p, "full_text") else None) or ""
        db.execute(
            text(
                "INSERT OR IGNORE INTO paper_fts(paper_id, title, abstract, authors, full_text) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text)"
            ),
            {
                "pid": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors_str,
                "full_text": full_text_value,
            },
        )
        db.commit()
    except Exception:
        # FTS 表可能未就绪，忽略同步失败不影响主流程
        db.rollback()

    return paper_to_schema(p, favorited=False)


# ---------------------------------------------------------------------------
# Semantic Scholar 富化（B2）
# ---------------------------------------------------------------------------
def enrich_paper_from_semantic(db: Session, paper_id: str, force: bool = False) -> bool:
    """从 Semantic Scholar 拉取论文元数据，更新 influential_citations 与 fields_of_study。

    - force=False 时跳过已富化的论文（influential_citations 非 None）。
    - API 失败时静默降级，返回 False，不影响其他流程。
    - 成功更新返回 True；论文不存在返回 False。

    Args:
        force: True 时强制重新拉取（即使已有数据）。

    Returns:
        True 表示成功更新；False 表示跳过/失败/论文不存在。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return False

    # 已富化且非强制刷新 → 跳过
    if not force and p.influential_citations is not None:
        return False

    from .semantic_scholar import fetch_paper_metadata

    meta = fetch_paper_metadata(paper_id)
    if not meta:
        return False

    p.influential_citations = meta["influential_citations"]
    p.fields_of_study = meta["fields_of_study"] or None
    # 同时更新引用数（Semantic Scholar 的 citationCount 更准确）
    if meta["citations"]:
        p.citations = meta["citations"]
    db.commit()
    return True


# ---------------------------------------------------------------------------
# RAG 检索（简易关键词匹配，供 /ask 使用）
# ---------------------------------------------------------------------------
def retrieve_context(db: Session, question: str, paper_ids: list[str], top_k: int = 3) -> list[Paper]:
    """简易 RAG 检索：按问题关键词在标题/摘要中匹配，取前 top_k 篇。

    若指定了 paper_ids 则只在该范围内检索；否则全库检索。
    """
    rows = db.query(PaperORM)
    if paper_ids:
        rows = rows.filter(PaperORM.id.in_(paper_ids))
    rows = rows.all()

    q_lower = (question or "").lower()
    tokens = [t for t in q_lower.split() if len(t) >= 2]

    def score(p: PaperORM) -> int:
        s = 0
        title = (p.title or "").lower()
        abstract = (p.abstract or "").lower()
        for t in tokens:
            if t in title:
                s += 3
            if t in abstract:
                s += 1
        return s

    if tokens:
        rows.sort(key=score, reverse=True)
        rows = [p for p in rows if score(p) > 0][:top_k]
    else:
        rows = rows[:top_k]

    return [paper_to_schema(p) for p in rows]


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def get_stats(db: Session) -> LibraryStats:
    rows = db.query(PaperORM).all()
    total_chunks = sum(p.chunk_count for p in rows)
    total_size = sum(p.index_size for p in rows)
    counter = Counter(p.category for p in rows)
    by_category = [{"category": k, "count": v} for k, v in counter.items()]
    return LibraryStats(
        totalPapers=len(rows),
        totalChunks=total_chunks,
        totalSize=total_size,
        byCategory=by_category,
    )


# ---------------------------------------------------------------------------
# 种子数据
# ---------------------------------------------------------------------------
def seed_if_empty(db: Session) -> None:
    """当 papers 表为空时，从 data.SEED_PAPERS 写入初始数据。"""
    if db.query(PaperORM).count() > 0:
        return

    for d in SEED_PAPERS:
        db.add(
            PaperORM(
                id=d["id"],
                title=d["title"],
                authors=list(d.get("authors", [])),
                abstract=d.get("abstract", ""),
                category=d.get("category", "all"),
                tags=list(d.get("tags", [])),
                year=d.get("year", 0),
                journal=d.get("journal", ""),
                pdf_url=d.get("pdfUrl") or f"https://arxiv.org/pdf/{d['id']}.pdf",
                citations=d.get("citations", 0),
                chunk_count=d.get("chunkCount", 0),
                index_size=d.get("indexSize", 0),
                source=d.get("source", "arxiv"),
            )
        )
    db.commit()


# ===========================================================================
# 论文写作工作台 —— 项目 / 章节 CRUD
# ===========================================================================
def _fmt_dt(dt) -> str:
    """datetime → 'YYYY-MM-DD HH:MM' 字符串，None 返回空串。"""
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _project_to_response(p: WritingProjectORM, chapter_count: int = 0) -> ProjectResponse:
    """ORM 行 → camelCase 响应。chapter_count 由调用方统计后传入（避免 N+1）。"""
    return ProjectResponse(
        id=p.id,
        title=p.title,
        keywords=list(p.keywords or []),
        targetJournal=p.target_journal or "",
        targetWordCount=p.target_word_count or 0,
        chapterCount=chapter_count,
        createdAt=_fmt_dt(p.created_at),
        updatedAt=_fmt_dt(p.updated_at),
    )


def _chapter_to_response(c: ChapterORM) -> ChapterResponse:
    """ORM 行 → camelCase 响应（不递归子节点）。"""
    return ChapterResponse(
        id=c.id,
        projectId=c.project_id,
        parentId=c.parent_id,
        title=c.title,
        content=c.content or "",
        order=c.order,
        createdAt=_fmt_dt(c.created_at),
        updatedAt=_fmt_dt(c.updated_at),
    )


# ---------------------------------------------------------------------------
# 项目 CRUD
# ---------------------------------------------------------------------------
def create_project(db: Session, payload: ProjectCreate) -> ProjectResponse:
    """创建写作项目。

    - 若 payload.templateId 指定模板，按模板结构递归创建章节大纲。
    - 否则创建一个「引言」根章节作为初始内容。
    """
    project = WritingProjectORM(
        title=payload.title.strip(),
        keywords=list(payload.keywords or []),
        target_journal=(payload.targetJournal or "").strip(),
        target_word_count=0,
    )
    db.add(project)
    db.flush()  # 取 project.id

    tmpl = _find_template(payload.templateId) if payload.templateId else None
    if not tmpl and payload.templateId:
        # 系统模板未命中，尝试用户自定义模板
        tmpl = find_user_template(db, payload.templateId)
    if tmpl:
        _create_chapters_recursive(db, project.id, tmpl["chapters"], parent_id=None)
    else:
        # 无模板：自动创建「引言」章节作为初始大纲
        db.add(ChapterORM(
            project_id=project.id,
            parent_id=None,
            title="引言",
            content="",
            order=0,
        ))

    db.commit()
    db.refresh(project)
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project.id).count()
    return _project_to_response(project, chapter_count=cnt)


def _find_template(template_id: str) -> Optional[dict]:
    """按 id 查找模板字典，不存在返回 None。"""
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def _create_chapters_recursive(
    db: Session,
    project_id: int,
    chapters_data: list[dict],
    parent_id: Optional[int],
) -> None:
    """递归创建章节树（从模板数据构建）。

    chapters_data 为模板中的章节列表，每项含 title 与可选 children。
    """
    for i, ch in enumerate(chapters_data):
        chapter = ChapterORM(
            project_id=project_id,
            parent_id=parent_id,
            title=ch.get("title", "未命名章节"),
            content="",
            order=i,
        )
        db.add(chapter)
        db.flush()  # 取 chapter.id
        children = ch.get("children") or []
        if children:
            _create_chapters_recursive(db, project_id, children, parent_id=chapter.id)


def get_templates() -> list[TemplateResponse]:
    """返回所有写作模板（供前端选择）。"""
    return [
        TemplateResponse(
            id=t["id"],
            name=t["name"],
            description=t.get("description", ""),
            chapters=_dict_to_template_chapters(t.get("chapters", [])),
        )
        for t in TEMPLATES
    ]


def _dict_to_template_chapters(items: list[dict]) -> list[TemplateChapter]:
    """递归将模板字典列表转为 TemplateChapter schema 列表。"""
    result: list[TemplateChapter] = []
    for item in items:
        result.append(
            TemplateChapter(
                title=item.get("title", ""),
                children=_dict_to_template_chapters(item.get("children") or []),
            )
        )
    return result


def get_projects(db: Session) -> list[ProjectResponse]:
    """列出所有写作项目（按创建时间倒序，附带章节计数）。"""
    rows = db.query(WritingProjectORM).order_by(WritingProjectORM.id.desc()).all()
    if not rows:
        return []
    # 一次性统计所有项目的章节数，避免 N+1 查询
    counts: dict[int, int] = {
        r[0]: r[1]
        for r in db.query(ChapterORM.project_id, func.count(ChapterORM.id))
        .group_by(ChapterORM.project_id)
        .all()
    }
    return [
        _project_to_response(p, chapter_count=counts.get(p.id, 0)) for p in rows
    ]


def get_project(db: Session, project_id: int) -> Optional[ProjectResponse]:
    """获取单个项目（含章节计数）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project_id).count()
    return _project_to_response(p, chapter_count=cnt)


def update_project(
    db: Session, project_id: int, payload: ProjectUpdate
) -> Optional[ProjectResponse]:
    """更新项目元数据（仅更新非 None 字段）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    if payload.title is not None:
        p.title = payload.title.strip()
    if payload.keywords is not None:
        p.keywords = list(payload.keywords)
    if payload.targetJournal is not None:
        p.target_journal = payload.targetJournal.strip()
    if payload.targetWordCount is not None:
        p.target_word_count = payload.targetWordCount
    db.commit()
    db.refresh(p)
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project_id).count()
    return _project_to_response(p, chapter_count=cnt)


def delete_project(db: Session, project_id: int) -> bool:
    """删除项目及其所有章节（通过 ORM cascade 级联删除）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return False
    db.delete(p)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 章节 CRUD
# ---------------------------------------------------------------------------
def _max_sibling_order(db: Session, project_id: int, parent_id: Optional[int]) -> int:
    """返回当前同级章节中最大的 order 值（无则返回 -1，便于 +1 起步）。"""
    q = db.query(ChapterORM.order).filter(ChapterORM.project_id == project_id)
    if parent_id is None:
        q = q.filter(ChapterORM.parent_id.is_(None))
    else:
        q = q.filter(ChapterORM.parent_id == parent_id)
    row = q.order_by(ChapterORM.order.desc()).first()
    return row[0] if row else -1


def create_chapter(
    db: Session, project_id: int, payload: ChapterCreate
) -> ChapterResponse:
    """在项目下创建章节。

    - parentId 指定时挂到对应父章节下；为 None 时作为根章节。
    - order 未显式指定（保持默认 0）时，自动追加到同级末尾。
    """
    # 校验项目存在
    project = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not project:
        raise ValueError(f"项目 {project_id} 不存在")

    parent_id = payload.parentId
    # 若指定父章节，校验其归属同一项目
    if parent_id is not None:
        parent = db.query(ChapterORM).filter(ChapterORM.id == parent_id).first()
        if not parent or parent.project_id != project_id:
            raise ValueError("父章节不存在或不属于当前项目")

    # order 默认追加到同级末尾
    base_order = _max_sibling_order(db, project_id, parent_id) + 1
    chapter = ChapterORM(
        project_id=project_id,
        parent_id=parent_id,
        title=payload.title.strip(),
        content=payload.content or "",
        order=payload.order if payload.order else base_order,
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return _chapter_to_response(chapter)


def update_chapter(
    db: Session, chapter_id: int, payload: ChapterUpdate
) -> Optional[ChapterResponse]:
    """更新章节（title/content/order，仅更新非 None 字段）。

    当 content 变更时，自动保存一个历史版本快照（最多保留 10 个）。
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    if payload.title is not None:
        c.title = payload.title.strip()
    if payload.content is not None:
        # 内容变更时保存历史版本（仅当内容确实不同）
        if c.content != payload.content and (c.content or payload.content):
            create_chapter_version(db, chapter_id, c.content or "")
        c.content = payload.content
    if payload.order is not None:
        c.order = payload.order
    db.commit()
    db.refresh(c)
    return _chapter_to_response(c)


def get_chapter(db: Session, chapter_id: int) -> Optional[ChapterORM]:
    """获取单个章节 ORM（供 AI 生成等场景使用）。"""
    return db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()


def move_chapter(
    db: Session, chapter_id: int, parent_id: Optional[int], index: int
) -> Optional[ChapterResponse]:
    """移动 / 拖拽重排章节：挂到 parent_id 下并插入到指定 index 位置。

    - 校验目标父章节归属同一项目
    - 禁止将章节移到自身或其子孙节点下（防止环）
    - 插入后对同级章节重新连续编号 order（0,1,2…）
    - index 超出范围时自动 clamp 到 [0, 兄弟数]
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    project_id = c.project_id

    # 校验父章节归属同一项目
    if parent_id is not None:
        parent = db.query(ChapterORM).filter(ChapterORM.id == parent_id).first()
        if not parent or parent.project_id != project_id:
            raise ValueError("目标父章节不存在或不属于当前项目")
        # 禁止移到自身或自身子孙下（防止环）
        if parent_id == chapter_id or _is_descendant(db, chapter_id, parent_id):
            raise ValueError("不能将章节移到自身或其子孙节点下")

    c.parent_id = parent_id
    db.flush()

    # 取新父节点下的兄弟（排除自身），按现有 order 排序
    q = db.query(ChapterORM).filter(
        ChapterORM.project_id == project_id,
        ChapterORM.id != chapter_id,
    )
    if parent_id is None:
        q = q.filter(ChapterORM.parent_id.is_(None))
    else:
        q = q.filter(ChapterORM.parent_id == parent_id)
    siblings = q.order_by(ChapterORM.order.asc(), ChapterORM.id.asc()).all()

    # 插入到目标 index（clamp）
    idx = max(0, min(index, len(siblings)))
    siblings.insert(idx, c)
    # 重新连续编号
    for i, s in enumerate(siblings):
        s.order = i
    db.commit()
    db.refresh(c)
    return _chapter_to_response(c)


def _is_descendant(db: Session, ancestor_id: int, candidate_id: int) -> bool:
    """判断 candidate_id 是否为 ancestor_id 的子孙（递归）。"""
    children = (
        db.query(ChapterORM)
        .filter(ChapterORM.parent_id == ancestor_id)
        .all()
    )
    for ch in children:
        if ch.id == candidate_id:
            return True
        if _is_descendant(db, ch.id, candidate_id):
            return True
    return False


def build_outline_context(db: Session, project_id: int) -> str:
    """构建项目大纲的缩进文本（供 AI 生成时提供结构上下文）。"""
    tree = get_chapter_tree(db, project_id)
    lines: list[str] = []

    def walk(nodes: list[ChapterTreeNode], depth: int) -> None:
        for n in nodes:
            lines.append(f"{'  ' * depth}- {n.title}")
            walk(n.children, depth + 1)

    walk(tree, 0)
    return "\n".join(lines)


def get_chapter_context(db: Session, chapter_id: int) -> dict | None:
    """获取章节上下文：标题、内容、所属项目 ID、完整大纲。

    供「智能续写」和「结构建议」两个 AI 辅助功能复用。
    返回 None 表示章节不存在。
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    outline = build_outline_context(db, c.project_id)
    return {
        "chapter_id": c.id,
        "project_id": c.project_id,
        "title": c.title,
        "content": c.content or "",
        "outline": outline,
    }


def delete_chapter(db: Session, chapter_id: int) -> bool:
    """删除章节及其所有子孙节点（通过 ORM cascade 级联删除 children）。"""
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return False
    db.delete(c)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# C2: 续写历史
# ---------------------------------------------------------------------------
MAX_CONTINUATIONS_PER_CHAPTER = 10


def create_continuation(
    db: Session,
    chapter_id: int,
    content: str,
    direction: str = "",
) -> dict | None:
    """保存一条续写历史记录，并裁剪到最多 MAX 条。

    Returns:
        新建记录的字典 {id, chapter_id, content, direction, created_at}，
        章节不存在时返回 None。
    """
    chapter = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not chapter:
        return None
    rec = ChapterContinuationORM(
        id=str(uuid.uuid4()),
        chapter_id=chapter_id,
        content=content,
        direction=direction or "",
    )
    db.add(rec)
    db.flush()
    # 裁剪：超出 MAX 条时删除最早的
    all_recs = (
        db.query(ChapterContinuationORM)
        .filter(ChapterContinuationORM.chapter_id == chapter_id)
        .order_by(ChapterContinuationORM.created_at.desc())
        .all()
    )
    for old in all_recs[MAX_CONTINUATIONS_PER_CHAPTER:]:
        db.delete(old)
    db.commit()
    db.refresh(rec)
    return _continuation_to_dict(rec)


def list_continuations(db: Session, chapter_id: int) -> list[dict]:
    """获取章节的续写历史（按时间倒序，最多 MAX 条）。"""
    rows = (
        db.query(ChapterContinuationORM)
        .filter(ChapterContinuationORM.chapter_id == chapter_id)
        .order_by(ChapterContinuationORM.created_at.desc())
        .limit(MAX_CONTINUATIONS_PER_CHAPTER)
        .all()
    )
    return [_continuation_to_dict(r) for r in rows]


def _continuation_to_dict(r: ChapterContinuationORM) -> dict:
    """ORM 行转字典（供路由层直接返回）。"""
    return {
        "id": r.id,
        "chapterId": r.chapter_id,
        "content": r.content,
        "direction": r.direction or "",
        "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


def get_chapter_tree(db: Session, project_id: int) -> list[ChapterTreeNode]:
    """获取项目的章节大纲树（按 order 递归构建）。

    一次性拉取项目所有章节，在 Python 侧按 parent_id 分组并递归组装，
    避免 N+1 查询。同级章节按 order 升序排列。
    """
    rows = (
        db.query(ChapterORM)
        .filter(ChapterORM.project_id == project_id)
        .order_by(ChapterORM.order.asc())
        .all()
    )
    if not rows:
        return []

    # 按 parent_id 分组（None → 根）
    by_parent: dict[Optional[int], list[ChapterORM]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r)

    def build(parent_id: Optional[int]) -> list[ChapterTreeNode]:
        children = by_parent.get(parent_id, [])
        nodes: list[ChapterTreeNode] = []
        for c in children:
            node = ChapterTreeNode(
                **_chapter_to_response(c).model_dump(),
                children=build(c.id),
            )
            nodes.append(node)
        return nodes

    return build(None)


# ---------------------------------------------------------------------------
# 字数统计
# ---------------------------------------------------------------------------
# 去除 Markdown 语法标记的正则（顺序敏感：先去代码块再去行内代码）
_MD_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`[^`]+`")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CITE = re.compile(r"\[@[^\]]+\]")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_ENUM = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_QUOTE = re.compile(r"^\s*>\s*", re.MULTILINE)
_MD_HR = re.compile(r"^\s*[-*]{3,}\s*$", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"\*([^*]+)\*")
_MD_UNDERSCORE = re.compile(r"__([^_]+)__")
_MD_WHITESPACE = re.compile(r"\s+")


def _count_words(text: str) -> int:
    """统计纯文本字数（排除 Markdown 标记）。

    规则：依次去除代码块、行内代码、图片/链接语法（保留链接文本）、
    引用标记 [@id]、标题/列表/引用/水平线标记、粗体/斜体标记，
    最后统计非空白字符数（适用于中英文混合文本）。
    """
    if not text:
        return 0
    t = _MD_CODE_BLOCK.sub("", text)
    t = _MD_INLINE_CODE.sub("", t)
    t = _MD_IMAGE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_CITE.sub("", t)
    t = _MD_HEADER.sub("", t)
    t = _MD_LIST.sub("", t)
    t = _MD_ENUM.sub("", t)
    t = _MD_QUOTE.sub("", t)
    t = _MD_HR.sub("", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_ITALIC.sub(r"\1", t)
    t = _MD_UNDERSCORE.sub(r"\1", t)
    return len(_MD_WHITESPACE.sub("", t))


def get_word_count(db: Session, project_id: int) -> Optional[WordCountResponse]:
    """统计项目所有章节的字数。

    返回 {total, chapters: [{id, title, wordCount}]}。
    """
    rows = (
        db.query(ChapterORM)
        .filter(ChapterORM.project_id == project_id)
        .order_by(ChapterORM.order.asc(), ChapterORM.id.asc())
        .all()
    )
    if not rows:
        # 项目可能存在但无章节
        proj = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
        if not proj:
            return None
        return WordCountResponse(total=0, chapters=[])

    items = [
        WordCountItem(id=r.id, title=r.title, wordCount=_count_words(r.content or ""))
        for r in rows
    ]
    total = sum(i.wordCount for i in items)
    return WordCountResponse(total=total, chapters=items)


# ---------------------------------------------------------------------------
# 项目导出（拼接所有章节 Markdown + 解析 [@id] 引用）
# ---------------------------------------------------------------------------
# 匹配章节内容中的 [@paper_id] 引用标记（paper_id 不含 ])
_CITE_RE = re.compile(r"\[@([^\]]+)\]")


def export_project_markdown(
    db: Session, project_id: int,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Optional[tuple[str, str, list[ExportReference]]]:
    """将项目所有章节按大纲顺序拼接为 Markdown，并解析 [@id] 引用。

    返回 (content, filename, references)：
    - content：拼接后的 Markdown 全文，[@id] 已替换为 [N] 编号
    - filename：建议的下载文件名（不含扩展名）
    - references：按首次出现顺序去重的参考文献列表

    P2-2 改进：
    - progress_cb(current, total)：每处理完一批章节后回调，用于精细化进度展示
    - 章节按深度优先展平后分批处理（每批 20 个），大文档导出不再长时间阻塞

    拼接规则：
    - 顶层标题「# 项目标题」
    - 每个章节按层级生成 Markdown 标题（根章节 ##，逐级 +1，封顶 6 级）
    - 章节内容中的 [@paper_id] 替换为 [1][2]… 顺序编号
    - 末尾追加「## 参考文献」列表（仅包含正文中实际引用的论文）
    """
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None

    tree = get_chapter_tree(db, project_id)

    lines: list[str] = [f"# {p.title}", ""]
    if p.keywords:
        lines.append(f"> 关键词：{', '.join(list(p.keywords))}")
        lines.append("")
    if p.target_journal:
        lines.append(f"> 目标期刊：{p.target_journal}")
        lines.append("")

    # P2-2: 将章节树深度优先展平为列表，便于分批处理
    flat: list[tuple[ChapterTreeNode, int]] = []

    def flatten(nodes: list[ChapterTreeNode], depth: int) -> None:
        for node in nodes:
            flat.append((node, depth))
            flatten(node.children, depth + 1)

    flatten(tree, 2)
    total = len(flat)

    # P2-2: 分批渲染（每批 20 章节），每批结束后通过 progress_cb 回调
    BATCH_SIZE = 20
    for i in range(0, total, BATCH_SIZE):
        batch = flat[i:i + BATCH_SIZE]
        for node, depth in batch:
            level = min(depth, 6)
            lines.append("")
            lines.append(f"{'#' * level} {node.title}")
            lines.append("")
            if node.content:
                lines.append(node.content)
        if progress_cb:
            progress_cb(min(i + BATCH_SIZE, total), total)

    raw = "\n".join(lines).strip() + "\n"

    # ---- 解析 [@id] 引用：按首次出现顺序去重 ----
    cited_ids: list[str] = []
    seen: set[str] = set()
    for m in _CITE_RE.finditer(raw):
        pid = m.group(1).strip()
        if pid and pid not in seen:
            seen.add(pid)
            cited_ids.append(pid)

    references: list[ExportReference] = []
    if cited_ids:
        # 一次性从 DB 拉取所有被引论文
        rows = db.query(PaperORM).filter(PaperORM.id.in_(cited_ids)).all()
        paper_map: dict[str, PaperORM] = {r.id: r for r in rows}
        for pid in cited_ids:
            paper = paper_map.get(pid)
            if paper:
                references.append(
                    ExportReference(
                        id=paper.id,
                        title=paper.title,
                        authors=list(paper.authors or []),
                        year=paper.year or 0,
                    )
                )
            else:
                # 论文不在库中，保留占位条目（便于用户发现失效引用）
                references.append(
                    ExportReference(id=pid, title=f"[未找到论文：{pid}]", authors=[], year=0)
                )

        # 构建 id → 编号 映射，替换 [@id] 为 [N]
        id_to_num = {pid: i + 1 for i, pid in enumerate(cited_ids)}

        def _replace(m: re.Match) -> str:
            return f"[{id_to_num.get(m.group(1).strip(), 0)}]"

        raw = _CITE_RE.sub(_replace, raw)

        # 追加参考文献列表
        raw += "\n\n## 参考文献\n\n"
        for i, ref in enumerate(references):
            parts = [f"[{i + 1}]", ref.title]
            if ref.authors:
                parts.append(", ".join(ref.authors))
            if ref.year:
                parts.append(str(ref.year))
            parts.append(f"arXiv:{ref.id}")
            raw += ". ".join(parts) + "\n"

    safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "_", p.title).strip("_") or "untitled"
    filename = safe_title
    return raw, filename, references


def get_project_updated_at(db: Session, project_id: int) -> Optional[str]:
    """P2-2: 获取项目的 updated_at（ISO 字符串），用于导出缓存失效判断。

    项目不存在时返回 None。
    """
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    return p.updated_at.isoformat() if p.updated_at else ""


# ===========================================================================
# 论文笔记 CRUD
# ===========================================================================
def _note_to_response(n: PaperNoteORM) -> NoteResponse:
    """ORM 行 → camelCase 响应。"""
    return NoteResponse(
        id=n.id,
        paperId=n.paper_id,
        projectId=n.project_id,
        content=n.content or "",
        createdAt=_fmt_dt(n.created_at),
        updatedAt=_fmt_dt(n.updated_at),
    )


def get_notes_by_paper(db: Session, paper_id: str) -> list[NoteResponse]:
    """获取某篇论文的所有笔记（按更新时间倒序）。"""
    rows = (
        db.query(PaperNoteORM)
        .filter(PaperNoteORM.paper_id == paper_id)
        .order_by(PaperNoteORM.updated_at.desc())
        .all()
    )
    return [_note_to_response(n) for n in rows]


def get_notes_by_project(db: Session, project_id: int) -> list[NoteResponse]:
    """获取某写作项目关联的所有笔记。"""
    rows = (
        db.query(PaperNoteORM)
        .filter(PaperNoteORM.project_id == project_id)
        .order_by(PaperNoteORM.updated_at.desc())
        .all()
    )
    return [_note_to_response(n) for n in rows]


def create_note(db: Session, payload: NoteCreate) -> Optional[NoteResponse]:
    """新建笔记。论文不存在时返回 None。"""
    paper = db.query(PaperORM).filter(PaperORM.id == payload.paperId).first()
    if not paper:
        return None
    note = PaperNoteORM(
        id=str(uuid.uuid4()),
        paper_id=payload.paperId,
        project_id=payload.projectId,
        content=payload.content or "",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_to_response(note)


def update_note(
    db: Session, note_id: str, payload: NoteUpdate
) -> Optional[NoteResponse]:
    """更新笔记内容。不存在返回 None。"""
    note = db.query(PaperNoteORM).filter(PaperNoteORM.id == note_id).first()
    if not note:
        return None
    note.content = payload.content
    db.commit()
    db.refresh(note)
    return _note_to_response(note)


def delete_note(db: Session, note_id: str) -> bool:
    """删除笔记。不存在返回 False。"""
    note = db.query(PaperNoteORM).filter(PaperNoteORM.id == note_id).first()
    if not note:
        return False
    db.delete(note)
    db.commit()
    return True


# ===========================================================================
# 引用关系可视化（基于现有 citations 字段 + 语义相似度推荐）
# ===========================================================================
def get_citation_relations(db: Session, paper_id: str) -> Optional[dict]:
    """获取论文引用关系。

    返回 {"citations": int, "references": list[Paper]}：
    - citations: 该论文被引用次数（papers.citations 字段）
    - references: 基于标题关键词语义相似度推荐的 5 篇相关论文（mock 引用关系）
    论文不存在时返回 None。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return None
    # 基于标题关键词在库内检索相似论文（取前 5 条，排除自身）
    related = retrieve_context(db, paper.title or paper_id, paper_ids=[], top_k=6)
    references = [p for p in related if p.id != paper_id][:5]
    return {"citations": paper.citations or 0, "references": references}


# ===========================================================================
# 引用智能推荐（基于章节内容检索相关论文）
# ===========================================================================
# 英文停用词表（覆盖最常见的无信息词汇，避免污染 FTS5 检索）
_EN_STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "for",
    "of", "to", "in", "on", "at", "by", "with", "from", "as", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "must", "shall", "can", "need", "this", "that", "these",
    "those", "it", "its", "they", "them", "their", "we", "our", "us",
    "you", "your", "he", "she", "his", "her", "i", "my", "me", "not",
    "no", "nor", "so", "than", "too", "very", "about", "above", "after",
    "again", "all", "any", "each", "few", "more", "most", "other", "some",
    "such", "only", "own", "same", "up", "down", "out", "off", "over",
    "under", "into", "via", "per", "using", "used", "use",
    "based", "shown", "show", "showed", "however", "thus", "hence",
    "therefore", "while", "where", "when", "which", "who", "whom",
    "what", "how", "why", "whether", "during", "between", "through",
    "among", "both", "either", "neither", "also", "such",
    "many", "much", "several", "various", "one", "two", "three",
    "first", "second", "third", "last", "new", "novel", "recent",
    "previous", "prior", "current", "present", "future", "past",
    "et", "al", "fig", "figure", "table", "section", "eq", "equation",
    "ref", "figure", "tab", "abstract", "introduction", "conclusion",
    "result", "results", "method", "methods", "discussion",
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
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(a) for a in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


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
    except Exception:
        # FTS 表不存在 / 查询语法错误等，防御性跳过
        fts_ranked = []

    # --- 2. 向量语义检索 ---
    vec_ranked: list[tuple[str, float]] = []
    from .semantic_search import embed_text, is_available

    if is_available() and count_embeddings(db) > 0:
        qvec = embed_text(embed_text_input)
        if qvec:
            vec_ranked = semantic_search_by_vector(db, qvec, top_k=20)

    # --- 3. 融合结果 ---
    vec_meta: dict[str, dict] = {}
    if vec_ranked:
        missing_ids = [pid for pid, _ in vec_ranked if pid not in fts_meta]
        if missing_ids:
            extra_rows = db.query(PaperORM).filter(PaperORM.id.in_(missing_ids)).all()
            for r in extra_rows:
                vec_meta[r.id] = {
                    "id": r.id,
                    "title": r.title,
                    "authors": list(r.authors or []),
                    "year": r.year or 0,
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
            p = db.query(PaperORM).filter(PaperORM.id == pid).first()
            if not p:
                continue
            meta = {
                "id": p.id,
                "title": p.title,
                "authors": list(p.authors or []),
                "year": p.year or 0,
            }
        result.append({
            "id": meta["id"],
            "title": meta["title"],
            "authors": meta["authors"],
            "year": meta["year"],
            "score": scored[pid],
        })
    return result


# ===========================================================================
# 用户自定义模板 CRUD
# ===========================================================================
def _user_template_to_response(t: UserTemplateORM) -> UserTemplateResponse:
    """ORM 行 → 响应。"""
    return UserTemplateResponse(
        id=t.id,
        name=t.name,
        description=t.description or "",
        chapters=_dict_to_template_chapters(t.chapters or []),
        createdAt=_fmt_dt(t.created_at),
    )


def get_user_templates(db: Session) -> list[UserTemplateResponse]:
    """列出所有用户自定义模板（按创建时间倒序）。"""
    rows = (
        db.query(UserTemplateORM)
        .order_by(UserTemplateORM.created_at.desc())
        .all()
    )
    return [_user_template_to_response(t) for t in rows]


def create_user_template(
    db: Session, payload: UserTemplateCreate
) -> UserTemplateResponse:
    """新建用户自定义模板。"""
    tmpl = UserTemplateORM(
        id=str(uuid.uuid4()),
        name=payload.name.strip(),
        description=(payload.description or "").strip(),
        chapters=[c.model_dump() for c in payload.chapters],
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return _user_template_to_response(tmpl)


def save_project_as_template(
    db: Session, project_id: int, name: str, description: str = ""
) -> Optional[UserTemplateResponse]:
    """将当前项目的大纲结构保存为自定义模板。项目不存在返回 None。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    tree = get_chapter_tree(db, project_id)

    def _tree_to_chapters(nodes: list[ChapterTreeNode]) -> list[TemplateChapter]:
        return [
            TemplateChapter(
                title=n.title,
                children=_tree_to_chapters(n.children),
            )
            for n in nodes
        ]

    chapters_data = [c.model_dump() for c in _tree_to_chapters(tree)]
    tmpl = UserTemplateORM(
        id=str(uuid.uuid4()),
        name=name.strip(),
        description=(description or "").strip(),
        chapters=chapters_data,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return _user_template_to_response(tmpl)


def delete_user_template(db: Session, template_id: str) -> bool:
    """删除用户自定义模板。不存在返回 False。"""
    tmpl = db.query(UserTemplateORM).filter(UserTemplateORM.id == template_id).first()
    if not tmpl:
        return False
    db.delete(tmpl)
    db.commit()
    return True


def find_user_template(db: Session, template_id: str) -> Optional[dict]:
    """按 id 查找用户自定义模板，返回字典（供 create_project 复用）。
    不存在返回 None。
    """
    t = db.query(UserTemplateORM).filter(UserTemplateORM.id == template_id).first()
    if not t:
        return None
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description or "",
        "chapters": t.chapters or [],
    }


# ===========================================================================
# 写作统计 + 每日字数快照
# ===========================================================================
def record_daily_snapshot(db: Session, project_id: int, word_count: int) -> None:
    """记录每日字数快照（每日仅记录一次，已存在则更新）。

    用于趋势图：今日首次打开项目时调用。
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    existing = (
        db.query(WritingSnapshotORM)
        .filter(
            WritingSnapshotORM.project_id == project_id,
            WritingSnapshotORM.date == today,
        )
        .first()
    )
    if existing:
        existing.word_count = word_count
    else:
        db.add(WritingSnapshotORM(
            project_id=project_id,
            date=today,
            word_count=word_count,
        ))
    db.commit()


def get_writing_stats(db: Session) -> WritingStatsResponse:
    """获取写作统计：总项目数、总字数、今日新增、近 7 天趋势。

    - totalWords：所有项目当前字数合计
    - todayWords：今日快照合计 - 昨日快照合计（无昨日快照的项目按 0 计）
    - trend：近 7 天每日所有项目快照字数合计
    """
    projects = db.query(WritingProjectORM).all()
    total_projects = len(projects)

    # 总字数：累加所有项目所有章节字数
    total_words = 0
    for p in projects:
        wc = get_word_count(db, p.id)
        if wc:
            total_words += wc.total

    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 今日新增 = 今日快照合计 - 昨日快照合计
    today_sum = db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0)).filter(
        WritingSnapshotORM.date == today
    ).scalar() or 0
    yesterday_sum = db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0)).filter(
        WritingSnapshotORM.date == yesterday
    ).scalar() or 0
    today_words = max(0, today_sum - yesterday_sum)

    # 近 7 天趋势：每日所有项目快照合计
    trend: list[DailyWordTrend] = []
    for i in range(6, -1, -1):
        d = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_sum = db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0)).filter(
            WritingSnapshotORM.date == d
        ).scalar() or 0
        trend.append(DailyWordTrend(date=d, wordCount=day_sum))

    return WritingStatsResponse(
        totalProjects=total_projects,
        totalWords=total_words,
        todayWords=today_words,
        trend=trend,
    )


# ===========================================================================
# 章节历史版本 CRUD
# ===========================================================================
MAX_VERSIONS_PER_CHAPTER = 10


def _version_to_response(v: ChapterVersionORM) -> ChapterVersionResponse:
    """ORM 行 → 列表项响应（不含 content）。"""
    return ChapterVersionResponse(
        id=v.id,
        chapterId=v.chapter_id,
        wordCount=v.word_count,
        createdAt=_fmt_dt(v.created_at),
    )


def create_chapter_version(
    db: Session, chapter_id: int, content: str
) -> None:
    """保存章节历史版本（超过上限时自动删除最旧的）。

    word_count 由 _count_words 计算。
    """
    version = ChapterVersionORM(
        id=str(uuid.uuid4()),
        chapter_id=chapter_id,
        content=content,
        word_count=_count_words(content),
    )
    db.add(version)
    db.flush()

    # 超过上限时删除最旧的版本
    count = (
        db.query(ChapterVersionORM)
        .filter(ChapterVersionORM.chapter_id == chapter_id)
        .count()
    )
    if count > MAX_VERSIONS_PER_CHAPTER:
        oldest = (
            db.query(ChapterVersionORM)
            .filter(ChapterVersionORM.chapter_id == chapter_id)
            .order_by(ChapterVersionORM.created_at.asc())
            .first()
        )
        if oldest:
            db.delete(oldest)
    db.commit()


def get_chapter_versions(
    db: Session, chapter_id: int
) -> list[ChapterVersionResponse]:
    """获取章节历史版本列表（按时间倒序，不含 content）。"""
    rows = (
        db.query(ChapterVersionORM)
        .filter(ChapterVersionORM.chapter_id == chapter_id)
        .order_by(ChapterVersionORM.created_at.desc())
        .all()
    )
    return [_version_to_response(v) for v in rows]


def get_chapter_version(
    db: Session, version_id: str
) -> Optional[ChapterVersionDetail]:
    """获取某个历史版本详情（含 content）。不存在返回 None。"""
    v = db.query(ChapterVersionORM).filter(ChapterVersionORM.id == version_id).first()
    if not v:
        return None
    return ChapterVersionDetail(
        id=v.id,
        chapterId=v.chapter_id,
        content=v.content or "",
        wordCount=v.word_count,
        createdAt=_fmt_dt(v.created_at),
    )


def restore_chapter_version(
    db: Session, chapter_id: int, version_id: str
) -> Optional[ChapterRestoreResponse]:
    """恢复章节到指定历史版本。

    - 将章节 content 替换为该版本内容
    - 恢复前先保存当前内容为新版本（便于撤销）
    - 返回恢复后的章节信息
    版本不存在或不属于该章节时返回 None。
    """
    v = (
        db.query(ChapterVersionORM)
        .filter(
            ChapterVersionORM.id == version_id,
            ChapterVersionORM.chapter_id == chapter_id,
        )
        .first()
    )
    if not v:
        return None

    chapter = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not chapter:
        return None

    # 恢复前先保存当前内容为新版本（便于撤销）
    if chapter.content and chapter.content != v.content:
        create_chapter_version(db, chapter_id, chapter.content)

    # 恢复章节内容
    chapter.content = v.content
    db.commit()
    db.refresh(chapter)

    return ChapterRestoreResponse(
        chapterId=chapter_id,
        versionId=version_id,
        content=v.content,
        wordCount=v.word_count,
    )


# ===========================================================================
# 语义搜索 —— 向量嵌入 CRUD + RRF 混合检索（fastembed 懒加载，详见 semantic_search.py）
# ===========================================================================
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
    from .semantic_search import embed_text, is_available

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
    rows = db.query(PaperORM).filter(PaperORM.id.in_(ranked_ids)).all()
    paper_map = {r.id: r for r in rows}
    fav_ids = get_favorite_paper_ids(db)
    result = []
    for pid in ranked_ids:
        p = paper_map.get(pid)
        if p:
            result.append(paper_to_schema(p, favorited=pid in fav_ids))
    return result


def upsert_paper_embedding(db: Session, paper_id: str, vec: list[float]) -> None:
    """新增或更新论文向量（upsert）。"""
    from .models import PaperEmbedding as PaperEmbeddingORM
    from .semantic_search import serialize_vector, EMBEDDING_DIM

    row = (
        db.query(PaperEmbeddingORM)
        .filter(PaperEmbeddingORM.paper_id == paper_id)
        .first()
    )
    serialized = serialize_vector(vec)
    if row:
        row.embedding = serialized
        row.dim = len(vec) or EMBEDDING_DIM
    else:
        db.add(PaperEmbeddingORM(
            paper_id=paper_id,
            embedding=serialized,
            dim=len(vec) or EMBEDDING_DIM,
        ))
    db.commit()


def get_all_embeddings(db: Session) -> list[tuple[str, str]]:
    """获取所有论文向量 (paper_id, serialized_embedding)。空表返回 []。"""
    from .models import PaperEmbedding as PaperEmbeddingORM

    rows = db.query(PaperEmbeddingORM.paper_id, PaperEmbeddingORM.embedding).all()
    return [(r[0], r[1]) for r in rows]


def count_embeddings(db: Session) -> int:
    """返回已生成向量的论文数。"""
    from .models import PaperEmbedding as PaperEmbeddingORM

    return db.query(PaperEmbeddingORM).count() or 0


def semantic_search_by_vector(
    db: Session, query_vec: list[float], top_k: int = 10
) -> list[tuple[str, float]]:
    """向量检索：计算查询向量与所有论文向量的余弦相似度，返回 Top K。

    返回 [(paper_id, similarity), ...]。空表返回 []。
    """
    from .semantic_search import cosine_similarity, deserialize_vector

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
