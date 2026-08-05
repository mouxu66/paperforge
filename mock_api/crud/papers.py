"""论文 CRUD、收藏、导入、富化、标签管理、去重合并、PDF 批注。

待拆分 god-file（~1300 行）：建议按功能域拆为 papers_core / tags / dedup / annotations。
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from pathlib import Path
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session
from sqlalchemy.sql.expression import TextClause

from ..author_utils import parse_authors
from ..data import SEED_PAPERS
from ..models import (
    CitationSentiment as CitationSentimentORM,
)
from ..models import (
    DepthReviewV4 as DepthReviewV4ORM,
)
from ..models import (
    Favorite,
)
from ..models import (
    Paper as PaperORM,
)
from ..models import (
    PaperEmbedding as PaperEmbeddingORM,
)
from ..models import (
    PaperFigure as PaperFigureORM,
)
from ..models import (
    PaperNote as PaperNoteORM,
)
from ..models import (
    PdfAnnotation as PdfAnnotationORM,
)
from ..schemas import (
    BatchDeleteResponse,
    BatchTagResponse,
    Paper,
    TagInfo,
    TagMutationResponse,
)
from ..schemas import (
    Source as SourceLiteral,
)
from ._shared import run_in_bg_session

logger = logging.getLogger(__name__)

# B10/F7 修复：序列化 create_paper_from_upload 的 dedup→commit 关键区，
# 防止同一 paper_id 的并发上传产生 PK 冲突或重复论文。
# FastAPI 上传路由已走 run_in_threadpool，单进程 threading.Lock 足够。
_create_paper_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 分类规范化（统一小写 + 去空格，避免 'Arxiv' / 'arxiv' 分裂为两个分类）
# ---------------------------------------------------------------------------
def normalize_category(cat: str) -> str:
    """Normalize category: lowercase, strip, replace spaces/hyphens with underscore."""
    if not cat:
        return ""
    return cat.strip().lower().replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# 内容类型判别（document_type: 'paper' | 'report'）
# 启发式仅作为前端未传 hint 时的 fallback。
# ---------------------------------------------------------------------------
REFLECTION_KEYWORDS = (
    "读后感",
    "感悟",
    "感想",
    "思考",
    "反思",
    "复现",
    "实验感悟",
    "读后",
    "report",
    "reflection",
    "thoughts",
    "notes",
    "reading",
    "笔记",
    "实验复现",
    "学习笔记",
    "阅读笔记",
    "批判性思考",
    "心得",
    "体会",
    "读后有感",
    "论文笔记",
)
PAPER_STRUCTURAL_KEYWORDS = (
    "Abstract",
    "ABSTRACT",
    "摘要",
    "Introduction",
    "INTRODUCTION",
    "引言",
    "Methodology",
    "METHODOLOGY",
    "Methods",
    "References",
    "参考文献",
    "Experiment",
    "EXPERIMENT",
    "实验",
    "Conclusion",
    "CONCLUSION",
    "结论",
)


def _detect_document_type(
    title: str,
    full_text: str,
    frontend_hint: str | None = None,
) -> str:
    """检测内容类型（'paper' | 'report'）。

    优先级：
    1. 前端明确传 hint（'paper' / 'report'）→ 直接采纳
    2. 标题含报告类关键词 → 'report'（标题是用户意图的最强信号）
    3. 内容含报告类关键词 → 'report'（无长度限制，长报告仍是报告）
    4. 含 ≥2 个论文结构关键词（Abstract/Methods/References 等）→ 'paper'
       （单个匹配不够，避免"作者在 Introduction 中提到"这种随意引用被误判）
    5. 长度 > 10000 字符且无报告信号 → 'paper'（长文默认论文）
    6. 默认 'paper'
    """
    hint = (frontend_hint or "").strip().lower()
    if hint in ("paper", "report"):
        return hint

    text = (full_text or "").strip()

    # 步骤 1: 检查标题（最大信号——用户主动取名"xxx读后感"）
    title_lower = (title or "").lower()
    if any(kw.lower() in title_lower for kw in REFLECTION_KEYWORDS):
        return "report"

    # 步骤 2: 检查内容中的报告信号（无长度限制）
    if text:
        text_lower = text.lower()
        if any(kw.lower() in text_lower for kw in REFLECTION_KEYWORDS):
            return "report"

        # 步骤 3: 论文结构信号 —— 需要 ≥2 个不同的论文结构关键词同时命中
        paper_hits = [kw for kw in PAPER_STRUCTURAL_KEYWORDS if kw in text]
        if len(paper_hits) >= 2:
            return "paper"

        # 步骤 4: 长文（> 10000 字符）且无报告信号 → 默认论文
        if len(text) > 10000:
            return "paper"

    return "paper"


# ---------------------------------------------------------------------------
# ORM -> Pydantic 转换
# ---------------------------------------------------------------------------
def paper_to_schema(p: PaperORM, favorited: bool = False) -> Paper:
    """将 ORM 行转换为 camelCase 的 Pydantic 响应模型。"""
    return Paper(
        id=p.id,
        title=p.title,
        authors=parse_authors(p.authors),
        year=p.year,
        abstract=p.abstract or "",
        category=p.category,
        tags=list(p.tags or []),
        citations=p.citations,
        chunkCount=p.chunk_count,
        indexSize=p.index_size,
        pdfUrl=p.pdf_url or "",
        source=cast(SourceLiteral, p.source),
        journal=p.journal or "",
        favorited=favorited,
        # B2: Semantic Scholar 富化字段（ORM 列可为 None）
        influentialCitations=p.influential_citations,
        fieldsOfStudy=list(p.fields_of_study) if p.fields_of_study else None,
        # WP-5.1: DOI
        doi=p.doi,
        # WP-1.2: OCR 状态与扫描件标识
        ocrStatus=p.ocr_status,
        isScanned=p.is_scanned,
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
    if any(kw in (a or "").lower() for a in parse_authors(p.authors)):
        return True
    if any(kw in (t or "").lower() for t in (p.tags or [])):
        return True
    return False


def _safe_fts_query(keyword: str) -> str:
    """清理用户输入，生成安全的 FTS5 MATCH 查询字符串。

    - 去除控制字符与 FTS5 特殊符号
    - 保留普通字母/数字/中文/空格
    - 多个词用空格连接（AND 语义）
    """
    # 仅保留字母、数字、中文及空白，其余替换为空格
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", keyword)
    # 折叠多余空格
    return " ".join(cleaned.split())


def _like_pattern(term: str) -> str:
    """生成 LIKE 模式并转义 % _ 通配符，避免用户输入被解释为通配符。"""
    escaped = term.replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _fts_match_count(db: Session, safe_kw: str) -> int:
    """返回 FTS5 MATCH 命中数量；返回 -1 表示 FTS5 不可用。"""
    try:
        row = db.execute(
            text("SELECT count(*) FROM paper_fts WHERE paper_fts MATCH :kw"),
            {"kw": safe_kw},
        ).first()
        return row[0] if row else 0
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        logger.debug("FTS5 count 查询失败，将降级到 SQL LIKE: kw=%s", safe_kw)
        return -1


def _fts_match_subquery(safe_kw: str) -> TextClause:
    """生成 FTS5 MATCH 子查询，避免 IN 列表超过 SQLite 999 变量上限。"""
    return text("SELECT paper_id FROM paper_fts WHERE paper_fts MATCH :kw").bindparams(kw=safe_kw)


def _apply_like_filter(query: Query[PaperORM], kw: str) -> Query[PaperORM]:
    """用 SQL LIKE 匹配 title/abstract/id/journal/source，并追加到 query。"""
    pattern = _like_pattern(kw)
    return query.filter(
        (PaperORM.title.ilike(pattern, escape="\\"))
        | (PaperORM.abstract.ilike(pattern, escape="\\"))
        | (PaperORM.id.ilike(pattern, escape="\\"))
        | (PaperORM.journal.ilike(pattern, escape="\\"))
        | (PaperORM.source.ilike(pattern, escape="\\"))
    )


def get_papers(
    db: Session,
    keyword: str | None,
    category: str | None,
    sort: str | None,
    page: int,
    page_size: int,
    source: str | None = None,
) -> tuple[list[Paper], int]:
    """论文分页查询（SQL 侧过滤/排序/分页 + FTS5 关键词检索）。

    实现要点：
    - category / source 走 SQL WHERE
    - keyword 优先走 paper_fts FTS5 全文索引
    - 排序走 SQL ORDER BY
    - 分页走 SQL LIMIT/OFFSET
    - 总数由 SQL COUNT 返回
    """
    kw = (keyword or "").strip().lower()

    query = db.query(PaperORM)

    # 分类过滤（SQL 侧）
    # "all" / 未指定分类 = 普通论文视图，默认排除感悟报告（感悟报告走 category='report' 独立 tab）
    if category and category != "all":
        cat = normalize_category(category)
        query = query.filter(PaperORM.category == cat)
    else:
        query = query.filter(PaperORM.category != "report")

    # 来源过滤（SQL 侧）
    if source and source != "all":
        query = query.filter(PaperORM.source == source)

    # 关键词过滤：FTS5 优先，无命中/不可用时降级为 LIKE
    if kw:
        safe_kw = _safe_fts_query(kw)
        if safe_kw:
            fts_count = _fts_match_count(db, safe_kw)
            if fts_count > 0:
                # 使用子查询回表，规避 SQLite 999 变量上限
                query = query.filter(PaperORM.id.in_(_fts_match_subquery(safe_kw)))
            elif fts_count == 0:
                # FTS5 无命中时降级为 SQL LIKE（保留子串匹配能力）
                query = _apply_like_filter(query, kw)
            else:
                # FTS5 不可用，降级 LIKE
                query = _apply_like_filter(query, kw)
        else:
            # 关键词仅含特殊字符，降级 LIKE
            query = _apply_like_filter(query, kw)

    # 排序（SQL 侧）
    sort = sort or "year_desc"
    sort_map = {
        "year_desc": PaperORM.year.desc(),
        "year_asc": PaperORM.year.asc(),
        "citations_desc": PaperORM.citations.desc(),
        "chunks_desc": PaperORM.chunk_count.desc(),
    }
    if sort in sort_map:
        query = query.order_by(sort_map[sort])

    # 总数（SQL 侧）
    total = query.count()

    # 分页（SQL 侧）
    page_rows = query.offset((page - 1) * page_size).limit(page_size).all()

    fav_ids = get_favorite_paper_ids(db)
    items = [paper_to_schema(p, favorited=p.id in fav_ids) for p in page_rows]
    return items, total


def get_paper(db: Session, paper_id: str) -> Paper | None:
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
        .filter(PaperORM.category != "report")
        .order_by(Favorite.id.desc())
        .all()
    )
    return [paper_to_schema(p, favorited=True) for p in rows]


def add_favorite(db: Session, paper_id: str) -> bool:
    """添加收藏。返回 True 表示论文存在且已收藏；论文不存在返回 False。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return False
    exists = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not exists:
        db.add(Favorite(paper_id=paper_id))
        db.commit()
    return True


def remove_favorite(db: Session, paper_id: str) -> bool:
    """取消收藏。返回 True 表示已删除；未收藏返回 False。"""
    row = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


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
    chunk_count: int = 0,
    index_size: int = 0,
    document_type: str = "paper",
    ocr_status: str | None = None,
    is_scanned: bool | None = None,
) -> Paper:
    """将解析后的 PDF 元数据写入 papers 表（source='upload'）。

    若 paper_id 已存在则更新，否则新增。
    B1：同时写入 full_text 字段并同步 FTS5 索引，确保上传后立即可检索全文。
    去重：按规范化标题+作者交集检测重复，重复时更新已有记录。
    自动审稿：入库后异步触发评审：
    - document_type='paper'  → DEPTH v4.1 九节点 DAG（需 full_text 非空）
    - document_type='report' → reflection 轻量 pipeline（需 full_text 非空）
    启发式：未明确传 document_type 时会按 _detect_document_type() 推断。

    增强（修复 0 chunks / 0 B）：
    - 接收 chunk_count、index_size 参数，非零时写入数据库。

    B10/F7 修复：
    - 用全局锁序列化 dedup→commit 关键区，避免并发上传同一文件时产生重复记录
      或主键冲突。
    - commit 时捕获 IntegrityError 作为兜底：若锁未拦截住（多进程场景），
      回滚后重新查询已有记录并更新。
    """
    with _create_paper_lock:
        return _create_paper_from_upload_locked(
            db,
            paper_id,
            title,
            authors,
            year,
            abstract,
            pdf_url,
            full_text,
            chunk_count,
            index_size,
            document_type,
            ocr_status,
            is_scanned,
        )


def _create_paper_from_upload_locked(
    db: Session,
    paper_id: str,
    title: str,
    authors: list[str],
    year: int,
    abstract: str,
    pdf_url: str,
    full_text: str,
    chunk_count: int,
    index_size: int,
    document_type: str,
    ocr_status: str | None,
    is_scanned: bool | None,
) -> Paper:
    """create_paper_from_upload 的实际实现（必须在 _create_paper_lock 内调用）。"""
    # 去重：按标题+作者检测重复
    dup = _find_duplicate_by_title_authors(db, title, authors)
    if dup and dup.id != paper_id:
        # 更新已有记录（保留原 paper_id 以便引用不失效）
        paper_id = dup.id
        logger.info("检测到重复论文: %s，更新已有记录 %s", title, paper_id)

    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if p is None:
        p = PaperORM(
            id=paper_id,
            title=title,
            authors=list(authors),
            abstract=abstract,
            # 上传论文不再按来源分类；默认归为交叉学科。
            # 后续可基于标题/摘要做 LLM 学科分类。
            category="interdisciplinary",
            tags=[],
            year=year,
            journal="",
            pdf_url=pdf_url,
            citations=0,
            chunk_count=chunk_count or 0,
            index_size=index_size or 0,
            source=cast(SourceLiteral, "upload"),
            full_text=full_text or None,
            ocr_status=ocr_status,
            is_scanned=is_scanned,
        )
        db.add(p)
    else:
        p.title = title
        p.authors = list(authors)
        p.abstract = abstract
        p.year = year
        p.full_text = full_text or None
        if chunk_count:
            p.chunk_count = chunk_count
        if index_size:
            p.index_size = index_size
        if ocr_status is not None:
            p.ocr_status = ocr_status
        if is_scanned is not None:
            p.is_scanned = is_scanned

    # B10/F7 兜底：commit 时若发生主键冲突，说明并发上传突破了锁
    # （多进程场景），回滚后重新查询并更新已有记录。
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning("并发上传导致主键冲突，降级为更新已有记录: %s", paper_id)
        p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if p is None:
            # 理论上不会发生，若发生则重新抛出原始异常
            raise exc from None
        p.title = title
        p.authors = list(authors)
        p.abstract = abstract
        p.year = year
        p.full_text = full_text or None
        if chunk_count:
            p.chunk_count = chunk_count
        if index_size:
            p.index_size = index_size
        if ocr_status is not None:
            p.ocr_status = ocr_status
        if is_scanned is not None:
            p.is_scanned = is_scanned
        db.commit()

    db.refresh(p)

    # B1: 同步 FTS5 索引（INSERT OR REPLACE 覆盖已有记录）
    try:
        authors_str = " ".join(authors or [])
        tags_str = " ".join(p.tags or [])
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
            ),
            {
                "pid": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors_str,
                "full_text": full_text or "",
                "tags": tags_str,
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        # FTS 表可能未就绪，忽略同步失败不影响主流程
        db.rollback()

    # 异步从 Semantic Scholar 补齐引用数/期刊/发表时间
    _enrich_async(paper_id)

    # 入库自动评审：有全文时在后台触发
    # - document_type='paper'  → DEPTH v4.1 九节点 DAG
    # - document_type='report' → reflection 轻量 pipeline
    if full_text:
        if document_type == "report":
            _reflection_review_async(paper_id)
        else:
            _review_async(paper_id)

    return paper_to_schema(p, favorited=False)


def delete_paper(db: Session, paper_id: str) -> bool:
    """删除单篇论文及其所有关联数据（级联删除）。

    删除内容包括：
    - papers 表记录
    - paper_fts 全文索引
    - favorites 收藏
    - paper_notes 笔记
    - pdf_annotations 批注
    - paper_embeddings 向量
    - uploads/{id}.pdf 本地磁盘文件

    Returns:
        True 删除成功，False 论文不存在。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return False

    # 1. 删除关联数据
    try:
        db.query(Favorite).filter(Favorite.paper_id == paper_id).delete()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass
    try:
        db.query(PaperNoteORM).filter(PaperNoteORM.paper_id == paper_id).delete()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass
    try:
        db.query(PdfAnnotationORM).filter(PdfAnnotationORM.paper_id == paper_id).delete()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass
    try:
        db.query(PaperEmbeddingORM).filter(PaperEmbeddingORM.paper_id == paper_id).delete()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass

    # 2. 从 paper_fts 删除
    try:
        db.execute(text("DELETE FROM paper_fts WHERE paper_id = :pid"), {"pid": paper_id})
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass

    # 3. 删除论文本身
    db.delete(paper)
    db.commit()

    # 4. 删除本地 PDF 文件
    try:
        pdf_path = Path(__file__).resolve().parent.parent / "uploads" / f"{paper_id}.pdf"
        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        pass

    return True


def batch_delete_papers(
    db: Session, paper_ids: list[str], max_count: int = 50
) -> BatchDeleteResponse:
    """批量删除论文（最多 max_count 篇）。

    Args:
        paper_ids: 要删除的论文 ID 列表。
        max_count: 单次最大删除数量，默认 50。

    Returns:
        BatchDeleteResponse: 成功/失败统计。
    """
    if len(paper_ids) > max_count:
        paper_ids = paper_ids[:max_count]

    deleted_count = 0
    failed_ids: list[str] = []

    for pid in paper_ids:
        try:
            success = delete_paper(db, pid)
            if success:
                deleted_count += 1
            else:
                failed_ids.append(pid)
        except Exception as e:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
            logger.warning("删除论文 %s 失败: %s", pid, e)
            failed_ids.append(pid)
            db.rollback()

    return BatchDeleteResponse(
        success=len(failed_ids) == 0,
        deleted_count=deleted_count,
        failed_ids=failed_ids,
    )


# ---------------------------------------------------------------------------
# 标签批量管理（WP-2.1）—— tags 为 Paper.tags JSON 列，操作后同步 paper_fts.tags
# ---------------------------------------------------------------------------
def _normalize_tags(raw: list[str]) -> list[str]:
    """strip + 去空 + 去重（保序、保留大小写）。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        name = (t or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _sync_fts_tags(db: Session, paper_id: str, tags: list[str]) -> None:
    """单篇论文 paper_fts.tags 列同步（标签变更后调用）。"""
    try:
        db.execute(
            text("UPDATE paper_fts SET tags = :tags WHERE paper_id = :pid"),
            {"tags": " ".join(tags), "pid": paper_id},
        )
    except Exception:  # noqa: BLE001 - FTS 同步失败不阻断主流程
        db.rollback()
        logger.warning("同步 paper_fts.tags 失败: paper_id=%s", paper_id)


def batch_update_tags(
    db: Session,
    paper_ids: list[str],
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
) -> BatchTagResponse:
    """批量给多篇论文增/删标签。

    - add_tags: 并入每篇 tags（去重）
    - remove_tags: 从每篇 tags 移除（大小写敏感匹配）
    操作后同步 paper_fts.tags。
    """
    add_set = _normalize_tags(add_tags or [])
    rm_set = {t for t in (remove_tags or []) if t.strip()}

    updated = 0
    failed: list[str] = []
    for pid in paper_ids:
        try:
            p = db.query(PaperORM).filter(PaperORM.id == pid).first()
            if p is None:
                failed.append(pid)
                continue
            current = list(p.tags or [])
            # 先删后加，避免同标签先加后删导致空操作
            current = [t for t in current if t not in rm_set]
            for t in add_set:
                if t not in current:
                    current.append(t)
            new_tags = _normalize_tags(current)
            if new_tags == list(p.tags or []):
                # 无变化也计入 updated（幂等），但跳过 FTS 同步
                updated += 1
                continue
            p.tags = new_tags
            db.flush()
            _sync_fts_tags(db, pid, new_tags)
            db.commit()
            updated += 1
        except Exception as e:  # noqa: BLE001 - papers crud - 单篇失败不阻断批量
            logger.warning("批量打标签失败 paper_id=%s: %s", pid, e)
            failed.append(pid)
            db.rollback()

    return BatchTagResponse(
        success=len(failed) == 0,
        updated_count=updated,
        failed_ids=failed,
    )


def list_all_tags(db: Session) -> list[TagInfo]:
    """聚合全库标签及计数（按计数降序、名称升序）。"""
    papers = db.query(PaperORM).filter(PaperORM.tags.isnot(None)).all()
    counter: dict[str, int] = {}
    for p in papers:
        for t in p.tags or []:
            name = (t or "").strip()
            if name:
                counter[name] = counter.get(name, 0) + 1
    return [
        TagInfo(name=name, count=count)
        for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def rename_tag(db: Session, old_name: str, new_name: str) -> TagMutationResponse:
    """全库重命名标签 old_name → new_name（合并到已存在的 new_name）。"""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name or old_name == new_name:
        return TagMutationResponse(success=False, affected_count=0)

    affected = 0
    papers = db.query(PaperORM).filter(PaperORM.tags.isnot(None)).all()
    for p in papers:
        tags = list(p.tags or [])
        if old_name not in tags:
            continue
        new_tags = [new_name if t == old_name else t for t in tags]
        new_tags = _normalize_tags(new_tags)
        p.tags = new_tags
        db.flush()
        _sync_fts_tags(db, p.id, new_tags)
        affected += 1
    db.commit()
    return TagMutationResponse(success=True, affected_count=affected)


def delete_tag(db: Session, name: str) -> TagMutationResponse:
    """全库删除指定标签。"""
    name = name.strip()
    if not name:
        return TagMutationResponse(success=False, affected_count=0)

    affected = 0
    papers = db.query(PaperORM).filter(PaperORM.tags.isnot(None)).all()
    for p in papers:
        tags = list(p.tags or [])
        if name not in tags:
            continue
        new_tags = [t for t in tags if t != name]
        p.tags = new_tags
        db.flush()
        _sync_fts_tags(db, p.id, new_tags)
        affected += 1
    db.commit()
    return TagMutationResponse(success=True, affected_count=affected)


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
    category: str = "interdisciplinary",
    tags: list[str] | None = None,
) -> Paper:
    """Import an external paper (e.g., from arXiv) into the database.

    Also syncs the FTS5 index by inserting into paper_fts table.
    If paper_id already exists, updates the record instead of duplicating.
    去重：按规范化标题+作者交集检测重复，重复时更新已有记录。
    """
    if tags is None:
        tags = []

    # 去重：按标题+作者检测重复
    dup = _find_duplicate_by_title_authors(db, title, authors)
    if dup and dup.id != paper_id:
        paper_id = dup.id
        logger.info("检测到重复论文（导入）: %s，更新已有记录 %s", title, paper_id)

    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if p is None:
        p = PaperORM(
            id=paper_id,
            title=title,
            authors=list(authors),
            abstract=abstract,
            category=normalize_category(category),
            tags=list(tags),
            year=year,
            journal="",
            pdf_url=pdf_url,
            citations=0,
            chunk_count=0,
            index_size=0,
            source=cast(SourceLiteral, source),
        )
        db.add(p)
    else:
        p.title = title
        p.authors = list(authors)
        p.abstract = abstract
        p.year = year
        p.category = normalize_category(category)
        p.tags = list(tags)
        p.pdf_url = pdf_url
        p.source = source
    db.commit()
    db.refresh(p)

    # 同步 FTS5 索引：INSERT OR IGNORE 防止重复 paper_id
    # authors/tags 用空格连接为字符串以支持 MATCH 全文检索
    # B1：full_text 字段同步写入（arXiv 导入通常无全文，存空字符串）
    try:
        authors_str = " ".join(authors or [])
        tags_str = " ".join(tags or [])
        full_text_value = (p.full_text if p and hasattr(p, "full_text") else None) or ""
        db.execute(
            text(
                "INSERT OR IGNORE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
            ),
            {
                "pid": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors_str,
                "full_text": full_text_value,
                "tags": tags_str,
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - papers crud - 退化到默认查询/返回
        # FTS 表可能未就绪，忽略同步失败不影响主流程
        db.rollback()

    # 异步从 Semantic Scholar 补齐引用数/期刊/发表时间
    _enrich_async(paper_id)

    # 入库自动深度审稿：有全文时在后台触发 V4.1 审稿
    if full_text_value:
        _review_async(paper_id)

    return paper_to_schema(p, favorited=False)


def _enrich_async(paper_id: str) -> None:
    """在后台线程中从 Semantic Scholar 富化论文（不阻塞导入响应）。"""

    def _run(db: Session) -> None:
        enrich_paper_from_semantic(db, paper_id, force=False)

    run_in_bg_session(_run)


def _review_async(paper_id: str) -> None:
    """在后台线程中触发 DEPTH v4.1 深度审稿（不阻塞导入响应）。

    仅当论文存在 full_text 时才启动审稿；无全文的论文（如 arXiv 导入）跳过。
    """

    def _run(db: Session) -> None:
        paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if not paper or not paper.full_text:
            return  # 无全文，无法审稿
        from ..depth_tasks import run_depth_review_sync

        run_depth_review_sync(paper_id)

    run_in_bg_session(_run)


def _reflection_review_async(paper_id: str) -> None:
    """在后台线程中触发 DEPTH reflection 轻量评审（不阻塞导入响应）。

    与 _review_async 的差异：
    - 走 depth_tasks.run_depth_reflection_sync
    - 写入 DepthReviewV4.kind='report' 行（独立于论文 v4.1 记录）
    - 仅当论文存在 full_text 时才启动；无全文的论文跳过
    """

    def _run(db: Session) -> None:
        paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if not paper or not paper.full_text:
            return  # 无全文，无法评审
        from ..depth_tasks import run_depth_reflection_sync

        run_depth_reflection_sync(paper_id)

    run_in_bg_session(_run)


def _find_duplicate_by_title_authors(
    db: Session, title: str, authors: list[str]
) -> PaperORM | None:
    """按规范化标题 + 作者交集查重。

    规则：标题规范化后完全相同（折叠多余空格）且 作者列表有交集（至少一个作者相同）
    即视为重复。返回查到的第一条记录的 ORM 对象，无重复返回 None。

    性能优化：先用 SQL LIKE 过滤候选（取标题首词），避免全表扫描。
    """
    if not title or not authors:
        return None
    norm_title = " ".join(title.strip().lower().split())
    if len(norm_title) < 5:
        return None

    author_set = {a.strip().lower() for a in authors if a.strip()}
    if not author_set:
        return None

    # 用标题前几个词做 SQL LIKE 过滤，大幅缩减候选集
    title_words = norm_title.split()
    if title_words:
        first_word = title_words[0]
        # SQLite: LOWER + REPLACE 做规范化（折叠多余空格）
        candidates = db.query(PaperORM).filter(PaperORM.title.ilike(f"%{first_word}%")).all()
    else:
        candidates = db.query(PaperORM).all()

    for c in candidates:
        c_norm = " ".join((c.title or "").strip().lower().split())
        if c_norm != norm_title:
            continue
        c_authors = {a.strip().lower() for a in parse_authors(c.authors)}
        if author_set & c_authors:
            return c
    return None


# ---------------------------------------------------------------------------
# 去重合并（WP-5.2）
# ---------------------------------------------------------------------------
def _papers_match(p1: PaperORM, p2: PaperORM) -> bool:
    """判断两篇论文是否重复：规范化标题相同且作者有交集。"""
    if not p1.title or not p2.title:
        return False
    norm1 = " ".join(p1.title.strip().lower().split())
    norm2 = " ".join(p2.title.strip().lower().split())
    if len(norm1) < 5 or norm1 != norm2:
        return False
    authors1 = {a.strip().lower() for a in parse_authors(p1.authors) if a.strip()}
    authors2 = {a.strip().lower() for a in parse_authors(p2.authors) if a.strip()}
    return bool(authors1 & authors2)


def find_duplicate_groups(db: Session) -> list[list[PaperORM]]:
    """扫描全库，返回所有疑似重复论文组（传递闭包）。

    重复判定：规范化标题完全相同 + 作者列表有交集。
    返回的每组包含 2 篇及以上论文。
    """
    papers = db.query(PaperORM).all()
    n = len(papers)
    parent = {p.id: p.id for p in papers}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # 并查集：两两比较，重复则合并
    for i in range(n):
        for j in range(i + 1, n):
            if _papers_match(papers[i], papers[j]):
                union(papers[i].id, papers[j].id)

    groups: dict[str, list[PaperORM]] = {}
    for p in papers:
        root = find(p.id)
        groups.setdefault(root, []).append(p)

    return [g for g in groups.values() if len(g) >= 2]


def merge_papers(
    db: Session,
    target_id: str,
    source_ids: list[str],
    field_sources: dict[str, str],
) -> Paper | None:
    """将 source_ids 合并到 target_id，按 field_sources 选择字段，并迁移关联数据。

    field_sources: {field_name: paper_id}，field_name 可选值：
        title, authors, year, abstract, journal, pdf_url, tags
    未指定的字段使用 target 原有值。
    """
    target = db.query(PaperORM).filter(PaperORM.id == target_id).first()
    if not target:
        return None

    sources = [db.query(PaperORM).filter(PaperORM.id == pid).first() for pid in source_ids]
    sources = [s for s in sources if s]
    if not sources:
        return get_paper(db, target_id)

    # 构建 id -> paper 映射，包含 target 和 sources
    paper_map = {target.id: target}
    for s in sources:
        paper_map[s.id] = s

    def _pick(field: str) -> PaperORM:
        chosen_id = field_sources.get(field)
        if chosen_id and chosen_id in paper_map:
            return paper_map[chosen_id]
        return target

    # 1. 合并基础字段
    chosen = _pick("title")
    target.title = chosen.title

    chosen = _pick("authors")
    target.authors = parse_authors(chosen.authors)

    chosen = _pick("year")
    target.year = chosen.year

    chosen = _pick("abstract")
    target.abstract = chosen.abstract or ""

    chosen = _pick("journal")
    target.journal = chosen.journal or ""

    chosen = _pick("pdf_url")
    target.pdf_url = chosen.pdf_url or ""

    chosen = _pick("tags")
    target.tags = list(chosen.tags or [])

    # 2. 合并数值/状态字段：取最大值或最完整值
    target.citations = max(target.citations, *[s.citations for s in sources])
    target.chunk_count = max(target.chunk_count, *[s.chunk_count for s in sources])
    target.index_size = max(target.index_size, *[s.index_size for s in sources])

    # 3. 合并 full_text：取最长的
    best_full_text = target.full_text or ""
    for s in sources:
        if (s.full_text or "") and len(s.full_text or "") > len(best_full_text):
            best_full_text = s.full_text or ""
    if best_full_text:
        target.full_text = best_full_text

    # 4. 合并 Semantic Scholar 富化字段
    for s in sources:
        if target.influential_citations is None and s.influential_citations is not None:
            target.influential_citations = s.influential_citations
        if not target.fields_of_study and s.fields_of_study:
            target.fields_of_study = list(s.fields_of_study)

    # 5. 迁移关联数据
    from pathlib import Path

    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    copied_pdf = False

    for s in sources:
        # favorites：target 未收藏时迁移，否则删除 source 的收藏
        for fav in db.query(Favorite).filter(Favorite.paper_id == s.id).all():
            existing = db.query(Favorite).filter(Favorite.paper_id == target_id).first()
            if not existing:
                fav.paper_id = target_id
            else:
                db.delete(fav)

        # notes
        for note in db.query(PaperNoteORM).filter(PaperNoteORM.paper_id == s.id).all():
            note.paper_id = target_id

        # pdf annotations
        for ann in db.query(PdfAnnotationORM).filter(PdfAnnotationORM.paper_id == s.id).all():
            ann.paper_id = target_id

        # embeddings：target 没有时复制第一个 source 的
        target_emb = (
            db.query(PaperEmbeddingORM).filter(PaperEmbeddingORM.paper_id == target_id).first()
        )
        if not target_emb:
            emb = db.query(PaperEmbeddingORM).filter(PaperEmbeddingORM.paper_id == s.id).first()
            if emb:
                emb.paper_id = target_id
                target_emb = emb  # 只复制一次

        # figures
        for fig in db.query(PaperFigureORM).filter(PaperFigureORM.paper_id == s.id).all():
            fig.paper_id = target_id

        # depth reviews
        for review in db.query(DepthReviewV4ORM).filter(DepthReviewV4ORM.paper_id == s.id).all():
            review.paper_id = target_id

        # citation sentiments：source 和 target 都需要更新
        # 注意唯一约束 (source_paper_id, target_paper_id)，冲突时删除重复记录
        for cs in (
            db.query(CitationSentimentORM)
            .filter(CitationSentimentORM.source_paper_id == s.id)
            .all()
        ):
            existing = (
                db.query(CitationSentimentORM)
                .filter(
                    CitationSentimentORM.source_paper_id == target_id,
                    CitationSentimentORM.target_paper_id == cs.target_paper_id,
                    CitationSentimentORM.id != cs.id,
                )
                .first()
            )
            if existing:
                db.delete(cs)
            else:
                cs.source_paper_id = target_id
        for cs in (
            db.query(CitationSentimentORM)
            .filter(CitationSentimentORM.target_paper_id == s.id)
            .all()
        ):
            existing = (
                db.query(CitationSentimentORM)
                .filter(
                    CitationSentimentORM.source_paper_id == cs.source_paper_id,
                    CitationSentimentORM.target_paper_id == target_id,
                    CitationSentimentORM.id != cs.id,
                )
                .first()
            )
            if existing:
                db.delete(cs)
            else:
                cs.target_paper_id = target_id

        # PDF 文件：target 没有本地 PDF 时，复制第一个 source 的 PDF
        if not copied_pdf:
            source_pdf = uploads_dir / f"{s.id}.pdf"
            target_pdf = uploads_dir / f"{target_id}.pdf"
            if source_pdf.exists() and not target_pdf.exists():
                try:
                    target_pdf.write_bytes(source_pdf.read_bytes())
                    if not target.pdf_url:
                        target.pdf_url = str(target_pdf)
                    copied_pdf = True
                except Exception:  # noqa: BLE001
                    pass

    # 6. 更新 papers 表内部自引用（reflection 报告指向的原论文）
    for s in sources:
        db.query(PaperORM).filter(PaperORM.source_paper_id == s.id).update(
            {"source_paper_id": target_id}
        )

    # 7. 删除 source 论文（级联删除剩余关联数据）
    for s in sources:
        db.delete(s)

    # 7. 同步 FTS5 索引（在主事务中提交，失败不阻断主流程）
    try:
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
            ),
            {
                "pid": target_id,
                "title": target.title,
                "abstract": target.abstract or "",
                "authors": " ".join(target.authors or []),
                "full_text": target.full_text or "",
                "tags": " ".join(target.tags or []),
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("合并后 FTS5 索引同步失败: target_id=%s", target_id)

    db.commit()
    db.refresh(target)

    return get_paper(db, target_id)


# ---------------------------------------------------------------------------
# Semantic Scholar 富化（B2）
# ---------------------------------------------------------------------------
def enrich_paper_from_semantic(
    db: Session, paper_id: str, force: bool = False, dry_run: bool = False
) -> bool:
    """从 Semantic Scholar 拉取论文元数据，更新 influential_citations 与 fields_of_study。

    - force=False 时跳过已富化的论文（influential_citations 非 None）。
    - API 失败时静默降级，返回 False，不影响其他流程。
    - 成功更新返回 True；论文不存在返回 False。
    - dry_run=True 时只修改 ORM 对象但不提交数据库，用于前端预览。

    Args:
        force: True 时强制重新拉取（即使已有数据）。
        dry_run: True 时不提交数据库，仅用于预览。

    Returns:
        True 表示成功更新/预览；False 表示跳过/失败/论文不存在。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return False

    # 已富化且非强制刷新 → 跳过（dry_run 也跳过，保持行为一致）
    if not force and p.influential_citations is not None:
        return False

    from ..semantic_scholar import fetch_paper_metadata

    meta = fetch_paper_metadata(paper_id)
    if not meta:
        return False

    p.influential_citations = meta["influential_citations"]
    p.fields_of_study = meta["fields_of_study"] or None
    # 同时更新引用数（Semantic Scholar 的 citationCount 更准确）
    if meta["citations"]:
        p.citations = meta["citations"]
    # 更新期刊名（Semantic Scholar 的 venue）
    if meta.get("venue"):
        p.journal = meta["venue"]
    # 更新年份（Semantic Scholar 的数据可能比 arXiv 更准确）
    if meta.get("year") and meta["year"] > 0:
        p.year = meta["year"]

    if dry_run:
        # 预览模式：让 SQLAlchemy 认为对象已修改，但不提交
        db.flush()
        return True

    db.commit()
    return True


# ---------------------------------------------------------------------------
# WP-5.1: 元数据补全辅助（DOI 提取 / PDF 重命名 / 批注提取）
# ---------------------------------------------------------------------------
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+[A-Z0-9]", re.IGNORECASE)


def _sanitize_filename(title: str) -> str:
    """将论文标题转换为安全的文件名字符串。"""
    sanitized = re.sub(r"[^\w\s-]", "", title).strip()
    return re.sub(r"\s+", " ", sanitized)


def extract_paper_doi(db: Session, paper_id: str) -> str | None:
    """从论文全文、PDF URL 或标题中提取 DOI 并写入数据库。

    返回提取到的 DOI 字符串；若无法提取则返回 None。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return None

    # 优先从 full_text 中提取
    text_sources = [p.full_text or "", p.pdf_url or "", p.title or ""]
    for source in text_sources:
        match = _DOI_RE.search(source)
        if match:
            doi = match.group(0).strip(".,; ")
            p.doi = doi
            db.commit()
            db.refresh(p)
            return doi
    return None


def rename_paper_pdf(db: Session, paper_id: str) -> str | None:
    """将本地 PDF 文件重命名为 '{year} - {title}.pdf' 格式。

    返回新的文件路径字符串；若本地无 PDF 则返回 None。
    """

    from ..pdf_parser import _get_uploads_dir

    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return None

    uploads_dir = _get_uploads_dir()
    old_path = uploads_dir / f"{paper_id}.pdf"
    if not old_path.exists():
        return None

    safe_title = _sanitize_filename(p.title) or paper_id
    new_name = f"{p.year} - {safe_title}.pdf"
    new_path = uploads_dir / new_name

    # 避免覆盖已有文件
    counter = 1
    while new_path.exists() and new_path != old_path:
        new_name = f"{p.year} - {safe_title} ({counter}).pdf"
        new_path = uploads_dir / new_name
        counter += 1

    try:
        old_path.rename(new_path)
        p.pdf_url = str(new_path)
        db.commit()
        db.refresh(p)
        return str(new_path)
    except OSError:
        return None


def _annotation_color_to_hex(color: tuple) -> str:
    """将 PyMuPDF 颜色元组转为 #RRGGBB 十六进制字符串。"""
    if not color:
        return "#FFEB3B"
    # fitz 颜色可能是 (r, g, b) 或 (r, g, b, alpha)，值域 0~1
    rgb = color[:3]
    try:
        return f"#{int(max(0, min(1, rgb[0])) * 255):02x}{int(max(0, min(1, rgb[1])) * 255):02x}{int(max(0, min(1, rgb[2])) * 255):02x}"
    except Exception:  # noqa: BLE001
        return "#FFEB3B"


def _annotation_quadpoints(annot) -> list:
    """从 PyMuPDF 批注中提取四边形坐标数组。"""
    try:
        # 优先使用 vertices（高亮/区域批注）
        vertices = annot.vertices
        if vertices:
            # vertices 是 [(x0, y0), (x1, y1), ...] 的列表
            return [list(pt) for pt in vertices]
    except Exception:  # noqa: BLE001
        pass

    try:
        # 退而求其次使用矩形
        rect = annot.rect
        if rect:
            return [
                [rect.x0, rect.y0],
                [rect.x1, rect.y0],
                [rect.x1, rect.y1],
                [rect.x0, rect.y1],
            ]
    except Exception:  # noqa: BLE001
        pass

    return []


def extract_pdf_annotations(db: Session, paper_id: str) -> list[PdfAnnotationORM]:
    """从本地 PDF 中提取批注/高亮并持久化到 pdf_annotations 表。

    幂等性：每次调用会先删除该论文 source='auto' 的自动提取批注，
    然后重新从 PDF 解析并写入，避免重复。

    返回持久化后的 PdfAnnotationORM 列表；若本地无 PDF 或解析失败则返回空列表。
    """

    from ..pdf_parser import _get_uploads_dir

    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return []

    uploads_dir = _get_uploads_dir()
    pdf_path = uploads_dir / f"{paper_id}.pdf"
    if not pdf_path.exists():
        return []

    try:
        import fitz  # PyMuPDF

        # 幂等：先删除旧的自动提取批注，避免与新批注冲突
        db.query(PdfAnnotationORM).filter(
            PdfAnnotationORM.paper_id == paper_id, PdfAnnotationORM.source == "auto"
        ).delete(synchronize_session=False)
        db.commit()

        doc = fitz.open(pdf_path)
        extracted: list[PdfAnnotationORM] = []
        for page in doc:
            for annot in page.annots() or []:
                note = annot.info.get("content", "") or "" if hasattr(annot, "info") else ""
                color = _annotation_color_to_hex(
                    annot.colors.get("stroke") if annot.colors else None
                )
                quadpoints = _annotation_quadpoints(annot)
                annotation = PdfAnnotationORM(
                    id=str(uuid.uuid4()),
                    paper_id=paper_id,
                    page=page.number + 1,  # 页码从 1 开始
                    quadpoints=quadpoints,
                    color=color,
                    note=note,
                    source="auto",
                )
                db.add(annotation)
                extracted.append(annotation)
        doc.close()

        db.commit()
        for annotation in extracted:
            db.refresh(annotation)
        return extracted
    except Exception as exc:  # noqa: BLE001
        logger.warning("提取 PDF 批注失败: %s", exc)
        db.rollback()
        return []


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
                authors=parse_authors(d.get("authors", [])),
                abstract=d.get("abstract", ""),
                category=normalize_category(d.get("category", "all")),
                tags=list(d.get("tags", [])),
                year=d.get("year", 0),
                journal=d.get("journal", ""),
                pdf_url=d.get("pdfUrl") or f"https://arxiv.org/pdf/{d['id']}.pdf",
                citations=d.get("citations", 0),
                chunk_count=d.get("chunkCount", 0),
                index_size=d.get("indexSize", 0),
                source=str(d.get("source", "arxiv")),
            )
        )
    db.commit()
