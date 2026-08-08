"""论文相关路由：论文 CRUD / 笔记 / 引用 / 排名 / 分析 / 收藏 / 统计 / 搜索建议 / PDF 批注 / PDF 代理。

批次 1 拆分：从 main.py 搬迁 papers 簇路由（仅依赖 crud + get_db + schemas）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import crud
from ..author_utils import parse_authors
from ..database import get_db
from ..llm.base import ChatMessage
from ..llm.factory import get_factory
from ..models import Paper as PaperORM
from ..models import PdfAnnotation as PdfAnnotationORM
from ..models import TranslationHistory as TranslationHistoryORM
from ..pdf_parser import process_one_pdf
from ..schemas import (
    AnalysisReport,
    AnalyzeRequest,
    AnalyzeResponse,
    BatchDeleteRequest,
    BatchDeleteResponse,
    BatchTagRequest,
    BatchTagResponse,
    ComparePapersRequest,
    ComparePapersResponse,
    DuplicateGroupPaper,
    DuplicateGroupsResponse,
    FavoriteCreate,
    FavoriteToggle,
    LibraryStats,
    MergePapersRequest,
    MergePapersResponse,
    NoteCreate,
    NoteResponse,
    NoteUpdate,
    PageResult,
    Paper,
    PaperFigureResponse,
    PdfAnnotationCreate,
    PdfAnnotationResponse,
    PdfAnnotationUpdate,
    QFHealthResponse,
    QFTimelineResponse,
    RankRequest,
    RankResponse,
    RenameTagRequest,
    TagInfo,
    TagMutationResponse,
    TranslationHistoryCreate,
    TranslationHistoryCreateResponse,
    TranslationHistoryItem,
    TranslationHistoryList,
)
from ..services.pdf_proxy_service import (
    fetch_pdf_stream,
    resolve_pdf_url,
    validate_pdf_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["papers"])


# ---------------------------------------------------------------------------
# PDF 代理（已有路由，保留）
# ---------------------------------------------------------------------------
@router.get("/api/papers/{paper_id}/pdf-proxy")
def pdf_proxy(paper_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """PDF 代理：绕过 CORS 限制，为前端 pdfjs-dist 提供可读取的 PDF 流。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    url = resolve_pdf_url(paper_id, paper.pdf_url)
    validate_pdf_url(url)
    iter_chunks, headers = fetch_pdf_stream(url)
    return StreamingResponse(iter_chunks, media_type="application/pdf", headers=headers)


# ---------------------------------------------------------------------------
# 基础论文接口
# ---------------------------------------------------------------------------
@router.get("/api/papers", response_model=PageResult)
def list_papers(
    keyword: str | None = Query(None),
    category: str | None = Query(None),
    sort: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
) -> PageResult:
    items, total = crud.get_papers(db, keyword, category, sort, page, page_size, source)
    return PageResult(items=items, total=total, page=page, pageSize=page_size)


# ---------------------------------------------------------------------------
# 标签批量管理（WP-2.1）
# 注意：必须放在 /api/papers/{paper_id} 之前，避免动态路由 shadow 静态路由。
# ---------------------------------------------------------------------------
@router.post("/api/papers/batch-tag", response_model=BatchTagResponse)
def batch_tag_papers_route(req: BatchTagRequest, db: Session = Depends(get_db)) -> BatchTagResponse:
    """批量给多篇论文增/删标签。"""
    if not req.paper_ids:
        raise HTTPException(status_code=400, detail="paper_ids 不能为空")
    if not req.add_tags and not req.remove_tags:
        raise HTTPException(status_code=400, detail="add_tags 和 remove_tags 不能同时为空")
    return crud.batch_update_tags(
        db,
        req.paper_ids,
        add_tags=req.add_tags,
        remove_tags=req.remove_tags,
    )


@router.get("/api/papers/tags", response_model=list[TagInfo])
def list_tags_route(db: Session = Depends(get_db)) -> list[TagInfo]:
    """聚合全库标签及计数（按计数降序）。"""
    return crud.list_all_tags(db)


@router.post("/api/papers/tags/rename", response_model=TagMutationResponse)
def rename_tag_route(req: RenameTagRequest, db: Session = Depends(get_db)) -> TagMutationResponse:
    """全库重命名标签。"""
    return crud.rename_tag(db, req.old_name, req.new_name)


@router.delete("/api/papers/tags/{tag}", response_model=TagMutationResponse)
def delete_tag_route(tag: str, db: Session = Depends(get_db)) -> TagMutationResponse:
    """全库删除指定标签。"""
    return crud.delete_tag(db, tag)


# ---------------------------------------------------------------------------
# WP-5.2: 去重合并 UI
# 注意：必须放在 /api/papers/{paper_id} 之前，避免动态路由 shadow 静态路由。
# ---------------------------------------------------------------------------
@router.get("/api/papers/duplicates", response_model=DuplicateGroupsResponse)
def list_duplicate_groups(db: Session = Depends(get_db)) -> DuplicateGroupsResponse:
    """扫描全库，返回所有疑似重复论文组。"""
    groups = crud.find_duplicate_groups(db)
    return DuplicateGroupsResponse(
        groups=[
            {
                "papers": [
                    DuplicateGroupPaper(
                        id=p.id,
                        title=p.title,
                        authors=parse_authors(p.authors),
                        year=p.year,
                        abstract=p.abstract or "",
                        journal=p.journal or "",
                        pdfUrl=p.pdf_url or "",
                        source=p.source or "",
                        citations=p.citations,
                        tags=list(p.tags or []),
                    )
                    for p in group
                ]
            }
            for group in groups
        ]
    )


ALLOWED_MERGE_FIELDS = {"title", "authors", "year", "abstract", "journal", "pdf_url", "tags"}


@router.post("/api/papers/merge", response_model=MergePapersResponse)
def merge_papers_route(
    req: MergePapersRequest, db: Session = Depends(get_db)
) -> MergePapersResponse:
    """合并重复论文：将 source_ids 合并到 target_id，按 field_sources 选择字段，并删除 source。"""
    target = db.query(PaperORM).filter(PaperORM.id == req.target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail=f"目标论文 {req.target_id} 不存在")

    if not req.source_ids:
        raise HTTPException(status_code=400, detail="source_ids 不能为空")

    allowed_ids = {req.target_id}
    for sid in req.source_ids:
        if sid == req.target_id:
            raise HTTPException(status_code=400, detail="source_ids 不能包含 target_id")
        source = db.query(PaperORM).filter(PaperORM.id == sid).first()
        if not source:
            raise HTTPException(status_code=404, detail=f"源论文 {sid} 不存在")
        allowed_ids.add(sid)

    for field, pid in (req.field_sources or {}).items():
        if field not in ALLOWED_MERGE_FIELDS:
            raise HTTPException(status_code=400, detail=f"非法字段: {field}")
        if pid not in allowed_ids:
            raise HTTPException(
                status_code=400, detail=f"field_sources 中的 paper_id 不在合并组内: {pid}"
            )

    # 校验 source_ids 确实与 target_id 属于同一重复组
    duplicate_groups = crud.find_duplicate_groups(db)
    target_group: set[str] | None = None
    for group in duplicate_groups:
        ids = {p.id for p in group}
        if req.target_id in ids:
            target_group = ids
            break

    if not target_group:
        raise HTTPException(status_code=400, detail="目标论文不在任何重复组中")

    for sid in req.source_ids:
        if sid not in target_group:
            raise HTTPException(status_code=400, detail=f"源论文 {sid} 与目标论文不在同一重复组中")

    merged = crud.merge_papers(db, req.target_id, req.source_ids, req.field_sources or {})
    if not merged:
        raise HTTPException(status_code=500, detail="合并失败")
    return MergePapersResponse(
        success=True,
        target_id=req.target_id,
        deleted_ids=req.source_ids,
        paper=merged,
    )


@router.get("/api/papers/{paper_id}", response_model=Paper)
def get_paper(paper_id: str, db: Session = Depends(get_db)) -> Paper:
    paper = crud.get_paper(db, paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return paper


@router.get("/api/search/suggest")
def search_suggest(q: str = Query(""), db: Session = Depends(get_db)) -> list[dict]:
    """搜索建议：FTS5 全文检索，返回前 5 条 {id, title, highlight}。"""
    return crud.suggest_papers(db, q)


# ---------------------------------------------------------------------------
# 实验图（figure）抽取 + OCR 检索
# ---------------------------------------------------------------------------
class FigureSearchRequest(BaseModel):
    """figure 级语义检索请求体。"""

    q: str
    top_k: int = 10


class HybridSearchRequest(BaseModel):
    """论文 + figure 混合检索请求体。"""

    q: str
    top_k: int = 10


@router.post("/api/figures/search")
def search_figures_endpoint(req: FigureSearchRequest, db: Session = Depends(get_db)) -> list[dict]:
    """figure 级语义检索：把查询编码为向量，在 paper_figures 表里做余弦检索。

    MVP 闭环：「搜 accuracy curve」→ 定位到具体论文的具体那张图。
    返回 [{paper_id, title, page, figure_index, ocr_text, score, image_url}, ...]，
    仅返回 OCR 文字向量化成功（有 embedding）的图；score 降序。

    诚实上限：命中来自 OCR 抽到的「图里的字」，不是 VLM 对图语义的理解。
    """
    q = (req.q or "").strip()
    if not q:
        return []
    # 延迟导入：避免在无 OCR/向量依赖的测试环境触发重导入
    from ..crud.figures import search_figures as _search_figures
    from ..semantic_search import embed_text as _embed_text

    vec = _embed_text(q)
    if not vec:
        # 向量不可用（fastembed 未装/未联网）：返回空，前端提示降级
        return []
    hits = _search_figures(db, vec, top_k=req.top_k or 10)

    _title_cache: dict[str, str] = {}
    out: list[dict] = []
    for h in hits:
        pid = h["paper_id"]
        if pid not in _title_cache:
            p = crud.get_paper(db, pid)
            _title_cache[pid] = p.title if p else ""
        fname = os.path.basename(h["figure_path"])
        out.append(
            {
                "paper_id": pid,
                "title": _title_cache[pid],
                "page": h["page"],
                "figure_index": h["figure_index"],
                "ocr_text": h["ocr_text"],
                "score": h["score"],
                "image_url": f"/api/figures/{pid}/image/{fname}",
            }
        )
    return out


@router.get("/api/figures/{paper_id}/image/{filename}")
def serve_figure_image(paper_id: str, filename: str, db: Session = Depends(get_db)) -> FileResponse:
    """按 paper_id 隔离地提供 figure PNG，供前端 <img> 展示。

    安全：校验论文存在 + 文件名防目录穿越；不裸挂整个 uploads 目录，
    避免把 PDF 等其他文件也暴露成静态资源。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    from ..pdf_parser import _get_uploads_dir

    img_path = _get_uploads_dir() / "figures" / paper_id / filename
    if not img_path.exists() or not img_path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(str(img_path))


@router.get("/api/papers/{paper_id}/figures", response_model=list[PaperFigureResponse])
def list_paper_figures(paper_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """获取某篇论文的所有 figure 详情（含 axis_info / source_text_span / claim_validation）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    from ..crud.figures import get_figures_by_paper

    rows = get_figures_by_paper(db, paper_id)
    out: list[dict] = []
    for fig in rows:
        fname = os.path.basename(fig.figure_path)
        out.append(
            {
                "id": fig.id,
                "paper_id": fig.paper_id,
                "page": fig.page,
                "figure_index": fig.figure_index,
                "figure_number": fig.figure_number,
                "figure_path": fig.figure_path,
                "ocr_text": fig.ocr_text or "",
                "caption_text": fig.caption_text,
                "qwen_summary": fig.qwen_summary,
                "source": fig.source,
                "source_text_span": fig.source_text_span,
                "axis_info": fig.axis_info,
                "claim_validation": fig.claim_validation,
                "curve_points": fig.curve_points,
                "image_url": f"/api/figures/{paper_id}/image/{fname}",
            }
        )
    return out


@router.post("/api/search/hybrid")
def search_hybrid(req: HybridSearchRequest, db: Session = Depends(get_db)) -> dict:
    """论文 + figure 混合检索：论文级 RRF 与 figure 级向量相似度融合重排。

    返回 {"papers": [...], "figures": [...], "fused_figures": [...]}。
    - papers：论文级混合检索结果（FTS5 + 向量 RRF）。
    - figures：figure 级语义检索结果（按 score 降序）。
    - fused_figures：融合结果，按 fused_score = figure_score + 12/(60+paper_rank) 排序。

    当向量不可用时，figures 为空，papers 仍正常返回 FTS5 结果。
    """
    q = (req.q or "").strip()
    top_k = req.top_k or 10

    # 论文级检索：RRF 召回 + MMR 多样性重排（复用 recommend_ranker 的 MMR）
    mmr_lambda = None
    try:
        from ..recommend_ranker import RecommendConfig

        mmr_lambda = RecommendConfig().diversity_mmr_lambda  # 默认 0.7
    except Exception:  # noqa: BLE001 - 路由增强 - 极端降级回退旧行为
        mmr_lambda = None
    papers = crud.hybrid_search_papers(db, q, top_k=top_k, mmr_lambda=mmr_lambda) if q else []
    paper_rank = {p.id: i + 1 for i, p in enumerate(papers)}

    # figure 级检索
    figures: list[dict] = []
    if q:
        from ..semantic_search import embed_text as _embed_text

        vec = _embed_text(q)
        if vec:
            from ..crud.figures import search_figures as _search_figures

            figures = _search_figures(db, vec, top_k=top_k)

    # 融合：fused_score = figure_score + 12/(60+paper_rank)
    fused = []
    for fig in figures:
        pid = fig["paper_id"]
        rank = paper_rank.get(pid, 9999)
        fused.append({**fig, "fused_score": fig["score"] + 12.0 / (60 + rank)})
    fused.sort(key=lambda x: x["fused_score"], reverse=True)

    return {"papers": papers, "figures": figures, "fused_figures": fused}


# ---------------------------------------------------------------------------
# 收藏
# ---------------------------------------------------------------------------
@router.get("/api/favorites", response_model=list[Paper])
def list_favorites(db: Session = Depends(get_db)) -> list[Paper]:
    return crud.get_favorite_papers(db)


@router.post("/api/favorites", response_model=FavoriteToggle, status_code=201)
def add_favorite(payload: FavoriteCreate, db: Session = Depends(get_db)) -> FavoriteToggle:
    ok = crud.add_favorite(db, payload.paper_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Paper {payload.paper_id} not found")
    return FavoriteToggle(favorited=True)


@router.delete("/api/favorites/{paper_id}", response_model=FavoriteToggle)
def remove_favorite(paper_id: str, db: Session = Depends(get_db)) -> FavoriteToggle:
    crud.remove_favorite(db, paper_id)
    return FavoriteToggle(favorited=False)


@router.get("/api/stats", response_model=LibraryStats)
def stats(db: Session = Depends(get_db)) -> LibraryStats:
    return crud.get_stats(db)


@router.get("/api/stats/qf-health", response_model=QFHealthResponse)
def qf_health(db: Session = Depends(get_db)) -> QFHealthResponse:
    """DEPTH 图链路(QF)健康面板：按需聚合 coverage / qf_mean / qf_std / histogram。"""
    return crud.get_qf_health(db)


@router.get("/api/stats/qf-timeline", response_model=QFTimelineResponse)
def qf_timeline(db: Session = Depends(get_db)) -> QFTimelineResponse:
    """Rec2: 图注关联修复前后对比（时间轴）统计。"""
    return crud.get_qf_timeline(db)


# ---------------------------------------------------------------------------
# 论文笔记
# ---------------------------------------------------------------------------
@router.get("/api/papers/{paper_id}/notes", response_model=list[NoteResponse])
def list_paper_notes(paper_id: str, db: Session = Depends(get_db)) -> list[NoteResponse]:
    """获取某篇论文的所有笔记。"""
    return crud.get_notes_by_paper(db, paper_id)


@router.post("/api/papers/notes", response_model=NoteResponse, status_code=201)
def create_paper_note(payload: NoteCreate, db: Session = Depends(get_db)) -> NoteResponse:
    """新建论文笔记（可关联写作项目）。"""
    note = crud.create_note(db, payload)
    if note is None:
        raise HTTPException(status_code=404, detail=f"论文 {payload.paperId} 不存在")
    return note


@router.put("/api/papers/notes/{note_id}", response_model=NoteResponse)
def update_paper_note(
    note_id: str, payload: NoteUpdate, db: Session = Depends(get_db)
) -> NoteResponse:
    """更新笔记内容。"""
    note = crud.update_note(db, note_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="笔记不存在")
    return note


@router.delete("/api/papers/notes/{note_id}")
def delete_paper_note(note_id: str, db: Session = Depends(get_db)) -> dict:
    """删除笔记。"""
    if not crud.delete_note(db, note_id):
        raise HTTPException(status_code=404, detail="笔记不存在")
    return {"success": True}


@router.get("/api/projects/{project_id}/notes", response_model=list[NoteResponse])
def list_project_notes(project_id: int, db: Session = Depends(get_db)) -> list[NoteResponse]:
    """获取某写作项目关联的所有笔记。"""
    return crud.get_notes_by_project(db, project_id)


# ---------------------------------------------------------------------------
# 引用关系可视化
# ---------------------------------------------------------------------------
@router.get("/api/papers/{paper_id}/citations")
def get_citation_relations_api(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """获取论文引用关系：引用次数 + 基于语义相似度推荐的相关论文列表。"""
    result = crud.get_citation_relations(db, paper_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    return {
        "citations": result["citations"],
        "references": result["references"],
    }


# ---------------------------------------------------------------------------
# WP-4.1: Web Clipper — 通过 URL 下载 PDF 并入库
# ---------------------------------------------------------------------------
class IngestUrlRequest(BaseModel):
    url: str
    title: str | None = None
    authors: list[str] | None = None
    abstract: str | None = None
    year: int | None = None
    journal: str | None = None
    pdf_url: str | None = None
    source: str | None = None
    tags: list[str] | None = None


class IngestUrlResponse(BaseModel):
    status: str
    paperId: str
    title: str
    mode: str = "pdf"
    message: str = ""


ALLOWED_SOURCES = {"arxiv", "cnki", "google_scholar", "web_clipper"}


def _detect_source(url: str) -> str:
    """根据 URL 识别论文来源站点。"""
    if "arxiv.org" in url:
        return "arxiv"
    if "cnki.net" in url or "cnki.com.cn" in url:
        return "cnki"
    if "scholar.google" in url:
        return "google_scholar"
    return "web_clipper"


def _resolve_pdf_url(url: str, source: str) -> str:
    """将来源页面 URL 解析为可直接下载的 PDF URL。"""
    if source == "arxiv" and "/abs/" in url:
        return url.replace("/abs/", "/pdf/") + ".pdf"
    return url


def _is_pdf_content(content: bytes) -> bool:
    """判断二进制内容是否为 PDF 文件。"""
    return content.startswith(b"%PDF-")


def _is_allowed_pdf_host(url: str) -> bool:
    """校验 PDF URL 是否来自受信任的论文站点，防止 SSRF。"""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return False
    allowed_hosts = {
        "arxiv.org",
        "cnki.net",
        "cnki.com.cn",
        "scholar.google.com",
        "scholar.google.com.hk",
        "scholar.google.co.jp",
    }
    return any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts)


def _is_private_or_internal_host(hostname: str) -> bool:
    """判断主机名是否指向私有/链路本地/回环地址（SSRF 防护）。"""
    import ipaddress
    import socket

    if not hostname:
        return True

    # 纯 IP 地址：直接解析并判断
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass

    # 域名：解析 DNS 后判断返回的 IP
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return True
            except ValueError:
                continue
    except socket.gaierror:
        # 无法解析的域名视为不安全
        return True

    return False


# 合规的下载 User-Agent（含联系方式，满足 arXiv 等政策对自动化下载的明文要求）
PAPERFORGE_UA = "PaperForge/1.0 (+https://github.com/mouxu66/paperforge; contact:2877693740@qq.com)"

# 下载节流：防止连续快速触发多篇下载触发 arXiv 等站点的速率限制 / 临时封禁。
# 单篇手动点击通常间隔已 > 该值，无感；仅对"快速连点多篇"生效。
_PDF_FETCH_MIN_INTERVAL = 2.0  # 秒
_pdf_fetch_lock = threading.Lock()
_pdf_last_fetch_ts = 0.0


def _throttled_fetch_pdf(pdf_url: str, headers: dict, timeout: int = 30) -> requests.Response:
    """带全局节流的 PDF 下载包装：保证相邻两次下载至少间隔 _PDF_FETCH_MIN_INTERVAL 秒。

    线程安全（FastAPI 默认多线程）；节流仅串行化下载节奏，不阻塞其他请求类型。
    """
    global _pdf_last_fetch_ts
    with _pdf_fetch_lock:
        now = time.monotonic()
        wait = _PDF_FETCH_MIN_INTERVAL - (now - _pdf_last_fetch_ts)
        if wait > 0:
            time.sleep(wait)
        _pdf_last_fetch_ts = time.monotonic()
    return _fetch_pdf_with_redirects(pdf_url, headers=headers, timeout=timeout)


def _fetch_pdf_with_redirects(
    pdf_url: str,
    headers: dict,
    timeout: int = 30,
    max_redirects: int = 5,
) -> requests.Response:
    """手动跟随重定向下载 PDF，逐跳校验 host，防止 SSRF 绕过。

    - 初始 URL 必须由调用方提前通过 _is_allowed_pdf_host 校验。
    - 每一跳 3xx 响应的 Location 头都会再次校验：
      1) 仍在允许列表内；
      2) 不指向私有/链路本地/回环地址。
    - 超过 max_redirects 次重定向直接抛异常。
    """
    current_url = pdf_url
    redirect_count = 0

    while redirect_count < max_redirects:
        if not _is_allowed_pdf_host(current_url):
            raise requests.exceptions.RequestException("重定向目标不在受信任来源列表中")
        if _is_private_or_internal_host(urlparse(current_url).hostname or ""):
            raise requests.exceptions.RequestException("重定向目标为私有/内部地址")

        resp = requests.get(current_url, headers=headers, timeout=timeout, allow_redirects=False)

        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_count += 1
            if redirect_count > max_redirects:
                raise requests.exceptions.RequestException(f"重定向次数超过上限 {max_redirects}")

            location = resp.headers.get("Location")
            if not location:
                raise requests.exceptions.RequestException("响应 3xx 但缺少 Location 头")

            current_url = urljoin(current_url, location)
            continue

        return resp

    raise requests.exceptions.RequestException(f"重定向次数超过上限 {max_redirects}")


def _create_metadata_only_paper(
    db: Session,
    *,
    url: str,
    title: str | None,
    authors: list[str] | None,
    abstract: str | None,
    year: int | None,
    journal: str | None,
    source: str,
    tags: list[str] | None = None,
) -> tuple[str, str]:
    """当 PDF 无法下载时，仅根据元数据创建论文记录。

    Returns:
        (paper_id, title)
    """
    import hashlib

    from ..crud.papers import import_external_paper

    safe_title = (title or url).strip()
    paper_id = f"clip_{source}_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}"
    import_external_paper(
        db,
        paper_id=paper_id,
        title=safe_title,
        authors=list(authors or []),
        year=year or 0,
        abstract=abstract or "",
        pdf_url=url,
        source=source,
        # 元数据-only 导入不再按来源分类，统一归为交叉学科。
        category="interdisciplinary",
        tags=list(tags) if tags else [source],
    )
    return paper_id, safe_title


@router.post("/api/ingest/url", response_model=IngestUrlResponse)
def ingest_url_api(req: IngestUrlRequest, db: Session = Depends(get_db)) -> IngestUrlResponse:
    """Web Clipper 入口：下载 URL 指向的 PDF 并走 process_one_pdf 入库。

    支持 arXiv / CNKI / Google Scholar 等来源。前端可附带元数据；
    当 PDF 下载失败或被付费墙拦截时，降级为仅保存元数据，
    避免用户丢失文献条目。
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")

    source = req.source or _detect_source(url)
    if source not in ALLOWED_SOURCES:
        source = "web_clipper"
    pdf_url = req.pdf_url or _resolve_pdf_url(url, source)

    # 尝试下载 PDF（仅允许受信任来源，防止 SSRF）
    content: bytes | None = None
    download_error = ""
    if pdf_url and _is_allowed_pdf_host(pdf_url):
        try:
            headers = {
                "User-Agent": PAPERFORGE_UA,
                "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = _throttled_fetch_pdf(pdf_url, headers=headers, timeout=30)
            resp.raise_for_status()
            content = resp.content
        except requests.exceptions.RequestException as exc:
            download_error = str(exc)
            content = None
    elif pdf_url:
        download_error = "PDF URL 不在受信任来源列表中"
        content = None

    # 如果下载成功且是有效 PDF，走正常 PDF 解析入库
    if content and _is_pdf_content(content):
        filename = pdf_url.split("/")[-1] or "download.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        result = process_one_pdf(content, filename, db)
        if result.success:
            # WP-4.1b 对齐：用扩展提供的元数据覆盖 PDF 提取值（source/tags/标题等），
            # 避免 arXiv 论文经 clipper 保存后 source 仍为 'upload'、tags 丢失。
            _apply_clipper_overrides(
                db,
                result.id,
                req.title,
                req.authors or [],
                req.year,
                req.journal,
                req.abstract,
                source,
                req.tags or [],
            )
            return IngestUrlResponse(
                status="success",
                paperId=result.id,
                title=req.title or result.title,
                mode="pdf",
                message="PDF 解析入库成功",
            )
        # PDF 解析失败，降级保存元数据
        download_error = result.error or "PDF 解析失败"

    # 降级：保存元数据（含前端传来的或从 URL 推断的）
    # 若没有任何可用元数据，则不再创建空记录，直接返回错误
    if not req.title and not req.authors and not req.abstract:
        raise HTTPException(
            status_code=400,
            detail=f"PDF 下载/解析失败且未提供元数据: {download_error or '无法获取 PDF'}",
        )

    paper_id, safe_title = _create_metadata_only_paper(
        db,
        url=url,
        title=req.title,
        authors=req.authors,
        abstract=req.abstract,
        year=req.year,
        journal=req.journal,
        source=source,
        tags=req.tags or None,
    )

    return IngestUrlResponse(
        status="success",
        paperId=paper_id,
        title=safe_title,
        mode="metadata",
        message=(
            "PDF 暂不可下载，已保存元数据"
            if not download_error
            else f"PDF 下载/解析失败，已保存元数据：{download_error}"
        ),
    )


# ---------------------------------------------------------------------------
# WP-4.1b: Web Clipper — 浏览器内已下载 PDF 直传入库（知网等需登录态站点）
# ---------------------------------------------------------------------------
MAX_RAW_PDF_SIZE = 50 * 1024 * 1024  # 50 MB


def _apply_clipper_overrides(
    db: Session,
    paper_id: str,
    title: str | None,
    authors: list[str],
    year: int | None,
    journal: str | None,
    abstract: str | None,
    source: str,
    tags: list[str],
) -> None:
    """将调用方（浏览器扩展）提供的元数据覆盖到 process_one_pdf 创建的论文记录上。

    process_one_pdf 从 PDF 内部提取元数据（可能不准或为空），而扩展从知网页面
    DOM/meta 提取的元数据通常更准确。此函数用扩展提供的值覆盖 PDF 提取值。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return

    changed = False
    if title:
        p.title = title
        changed = True
    if authors:
        p.authors = list(authors)
        changed = True
    if year:
        p.year = year
        changed = True
    if journal:
        p.journal = journal
        changed = True
    if abstract:
        p.abstract = abstract
        changed = True
    if source and source != "upload":
        p.source = source  # type: ignore[assignment]
        # 不再按来源给 category 赋值，统一归为交叉学科；后续可基于标题/摘要 LLM 分类。
        p.category = "interdisciplinary"
        changed = True
    if tags:
        existing = list(p.tags or [])
        for t in tags:
            if t not in existing:
                existing.append(t)
        p.tags = existing
        changed = True

    if not changed:
        return

    db.commit()
    # 重新同步 FTS5 索引以反映覆盖后的元数据
    try:
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
            ),
            {
                "pid": paper_id,
                "title": p.title,
                "abstract": p.abstract or "",
                "authors": " ".join(p.authors or []),
                "full_text": p.full_text or "",
                "tags": " ".join(p.tags or []),
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - FTS5 同步失败不阻塞主流程
        db.rollback()


@router.post("/api/ingest/raw", response_model=IngestUrlResponse)
def ingest_raw_api(
    file: UploadFile = File(...),
    url: str = Form(default=""),
    title: str | None = Form(default=None),
    authors: str = Form(default="[]"),
    abstract: str | None = Form(default=None),
    year: int | None = Form(default=None),
    journal: str | None = Form(default=None),
    source: str | None = Form(default=None),
    tags: str = Form(default="[]"),
    db: Session = Depends(get_db),
) -> IngestUrlResponse:
    """Web Clipper 原始 PDF 上传入口。

    用于知网等需登录态的站点：浏览器扩展在页面上下文内用
    ``fetch(pdfUrl, {credentials:'include'})`` 下载 PDF（借用户登录态），
    再以 multipart 方式上传 PDF 字节 + 页面提取的元数据到此端点。

    与 ``/api/ingest/url`` 的区别：后者由后端下载 PDF（无法携带知网登录态），
    本端点由浏览器下载后直传字节，绕过付费墙。

    流程：
    1. 校验文件非空 + 大小限制 + PDF 魔数
    2. 有效 PDF → ``process_one_pdf`` 解析入库 → 覆盖元数据
    3. 非有效 PDF / 解析失败 → 降级保存元数据
    """
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="PDF 文件为空")
    if len(content) > MAX_RAW_PDF_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"PDF 文件过大（{len(content) / 1024 / 1024:.1f} MB），"
            f"最大支持 {MAX_RAW_PDF_SIZE / 1024 / 1024:.0f} MB",
        )

    # 解析 JSON 数组字段（authors / tags）
    try:
        authors_list = json.loads(authors) if authors else []
    except (ValueError, TypeError):
        authors_list = []
    if not isinstance(authors_list, list):
        authors_list = []
    try:
        tags_list = json.loads(tags) if tags else []
    except (ValueError, TypeError):
        tags_list = []
    if not isinstance(tags_list, list):
        tags_list = []

    # 来源归一化
    src = source or (_detect_source(url) if url else "web_clipper")
    if src not in ALLOWED_SOURCES:
        src = "web_clipper"

    filename = file.filename or "clipper.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    # 校验是否为有效 PDF（魔数检查）
    if not _is_pdf_content(content):
        if not title and not authors_list and not abstract:
            raise HTTPException(status_code=400, detail="上传内容不是有效 PDF 且未提供元数据")
        paper_id, safe_title = _create_metadata_only_paper(
            db,
            url=url,
            title=title,
            authors=authors_list,
            abstract=abstract,
            year=year,
            journal=journal,
            source=src,
            tags=tags_list or None,
        )
        return IngestUrlResponse(
            status="success",
            paperId=paper_id,
            title=safe_title,
            mode="metadata",
            message="上传内容非有效 PDF，已保存元数据",
        )

    # 有效 PDF → 走正常 PDF 解析入库
    result = process_one_pdf(content, filename, db)

    if result.success:
        # 用扩展提供的元数据覆盖 PDF 提取值
        _apply_clipper_overrides(
            db,
            result.id,
            title,
            authors_list,
            year,
            journal,
            abstract,
            src,
            tags_list,
        )
        return IngestUrlResponse(
            status="success",
            paperId=result.id,
            title=title or result.title,
            mode="pdf",
            message="PDF 解析入库成功",
        )

    # PDF 解析失败 → 降级保存元数据
    if not title and not authors_list and not abstract:
        raise HTTPException(
            status_code=400,
            detail=f"PDF 解析失败且未提供元数据: {result.error or '解析失败'}",
        )

    paper_id, safe_title = _create_metadata_only_paper(
        db,
        url=url,
        title=title,
        authors=authors_list,
        abstract=abstract,
        year=year,
        journal=journal,
        source=src,
        tags=tags_list or None,
    )
    return IngestUrlResponse(
        status="success",
        paperId=paper_id,
        title=safe_title,
        mode="metadata",
        message=f"PDF 解析失败，已保存元数据：{result.error or ''}",
    )


# ---------------------------------------------------------------------------
# WP-5.1: 元数据补全（触发 Semantic Scholar 富化）
# ---------------------------------------------------------------------------
class EnrichMetadataPreviewResponse(BaseModel):
    """元数据补全预览响应：包含原始论文和预览补全后的论文。"""

    original: Paper
    enriched: Paper


@router.post("/api/papers/{paper_id}/enrich-metadata", response_model=Paper)
@router.post(
    "/api/papers/{paper_id}/enrich-metadata/preview", response_model=EnrichMetadataPreviewResponse
)
def enrich_paper_metadata_api(
    paper_id: str,
    dry_run: bool = Query(False, description="预览模式：只返回预览数据，不写入数据库"),
    db: Session = Depends(get_db),
) -> Paper | EnrichMetadataPreviewResponse:
    """手动触发单篇论文的元数据补全（Semantic Scholar 富化）。

    - dry_run=true 时仅返回预览数据，不写入数据库，用于前端对比展示。
    - dry_run=false（默认）时直接更新数据库并返回更新后的论文信息。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    # 预览模式：在独立 session 中计算富化结果，不污染当前事务
    if dry_run:
        original = crud.get_paper(db, paper_id)
        if original is None:
            raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

        # 使用临时 session 计算富化结果，dry_run=True 不提交数据库
        from ..database import SessionLocal

        preview_db = SessionLocal()
        try:
            preview_paper = preview_db.query(PaperORM).filter(PaperORM.id == paper_id).first()
            if preview_paper is None:
                raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
            crud.enrich_paper_from_semantic(preview_db, paper_id, force=True, dry_run=True)
            enriched = crud.get_paper(preview_db, paper_id)
        finally:
            preview_db.close()
        return EnrichMetadataPreviewResponse(original=original, enriched=enriched)

    updated = crud.enrich_paper_from_semantic(db, paper_id, force=True)
    if not updated:
        raise HTTPException(
            status_code=502, detail="元数据补全失败（Semantic Scholar 不可用或无结果）"
        )
    return crud.get_paper(db, paper_id)


# ---------------------------------------------------------------------------
# WP-5.1: 元数据补全辅助接口（DOI 提取 / PDF 重命名 / 批注提取）
# ---------------------------------------------------------------------------
@router.post("/api/papers/{paper_id}/extract-doi", response_model=Paper)
def extract_doi_api(paper_id: str, db: Session = Depends(get_db)) -> Paper:
    """从论文全文、PDF URL 或标题中提取 DOI 并保存。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    doi = crud.extract_paper_doi(db, paper_id)
    if doi is None:
        raise HTTPException(status_code=404, detail="未能从论文中提取到 DOI")
    result = crud.get_paper(db, paper_id)
    if not result:
        raise HTTPException(status_code=500, detail="DOI 提取后无法读取论文")
    return result


@router.post("/api/papers/{paper_id}/rename-pdf", response_model=Paper)
def rename_pdf_api(paper_id: str, db: Session = Depends(get_db)) -> Paper:
    """将本地 PDF 文件重命名为 '{year} - {title}.pdf' 格式。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    new_path = crud.rename_paper_pdf(db, paper_id)
    if new_path is None:
        raise HTTPException(status_code=404, detail="论文没有本地 PDF 文件")
    result = crud.get_paper(db, paper_id)
    if not result:
        raise HTTPException(status_code=500, detail="PDF 重命名后无法读取论文")
    return result


class ExtractAnnotationsResponse(BaseModel):
    count: int
    annotations: list[PdfAnnotationResponse]


@router.post(
    "/api/papers/{paper_id}/extract-annotations", response_model=ExtractAnnotationsResponse
)
def extract_annotations_api(
    paper_id: str, db: Session = Depends(get_db)
) -> ExtractAnnotationsResponse:
    """从本地 PDF 中提取批注/高亮，持久化后返回数量和列表。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    annotations = crud.extract_pdf_annotations(db, paper_id)
    return ExtractAnnotationsResponse(
        count=len(annotations),
        annotations=[_pdf_annotation_to_response(a) for a in annotations],
    )


# ---------------------------------------------------------------------------
# WP-2.7: PDF 实时翻译（选中段落 → LLM 学术翻译）
# ---------------------------------------------------------------------------
class TranslateRequest(BaseModel):
    text: str
    target_language: str = "zh-CN"


class TranslateResponse(BaseModel):
    translation: str


@router.post("/api/papers/{paper_id}/translate", response_model=TranslateResponse)
def translate_paper_text(
    paper_id: str, req: TranslateRequest, db: Session = Depends(get_db)
) -> TranslateResponse:
    """对选中的 PDF 文本进行学术翻译。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    text = req.text.strip()[:4000]
    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")

    try:
        factory = get_factory()
        provider = factory.get_provider()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 翻译：LLM 未配置: %s", exc)
        raise HTTPException(
            status_code=503, detail="LLM 未配置，请先在「模型管理」中添加并启用模型"
        ) from exc

    try:
        messages = [
            ChatMessage(
                role="system",
                content=f"You are a professional academic translator. Translate the following text to {req.target_language}. "
                "Preserve technical terms and LaTeX/math symbols. Output only the translated text, no explanations.",
            ),
            ChatMessage(role="user", content=text),
        ]
        resp = provider.chat(messages, temperature=0.1, max_tokens=1024)
        return TranslateResponse(translation=resp.content or "")
    except Exception as exc:
        logger.warning("PDF 翻译失败: %s", exc)
        raise HTTPException(status_code=500, detail="翻译失败，请检查 LLM 配置") from exc


# ---------------------------------------------------------------------------
# WP-2.7: PDF 翻译历史
# ---------------------------------------------------------------------------
MAX_TRANSLATION_HISTORY_PER_PAPER = 50


@router.get("/api/papers/{paper_id}/translation-history", response_model=TranslationHistoryList)
def list_translation_history(
    paper_id: str, db: Session = Depends(get_db)
) -> TranslationHistoryList:
    """获取某篇论文的最近翻译历史（按 created_at 倒序）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    rows = (
        db.query(TranslationHistoryORM)
        .filter(TranslationHistoryORM.paper_id == paper_id)
        .order_by(TranslationHistoryORM.created_at.desc())
        .limit(MAX_TRANSLATION_HISTORY_PER_PAPER)
        .all()
    )
    items: list[TranslationHistoryItem] = []
    for row in rows:
        items.append(
            TranslationHistoryItem(
                id=row.id,
                paperId=row.paper_id,
                originalText=row.original_text,
                translatedText=row.translated_text,
                targetLanguage=row.target_language,
                createdAt=row.created_at.isoformat() if row.created_at else "",
            )
        )
    return TranslationHistoryList(items=items)


@router.post(
    "/api/papers/{paper_id}/translation-history", response_model=TranslationHistoryCreateResponse
)
def create_translation_history(
    paper_id: str, req: TranslationHistoryCreate, db: Session = Depends(get_db)
) -> TranslationHistoryCreateResponse:
    """保存一条翻译记录；超过上限时删除最旧的记录。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    item = TranslationHistoryORM(
        paper_id=paper_id,
        original_text=req.original_text.strip()[:4000],
        translated_text=req.translated_text.strip()[:4000],
        target_language=req.target_language,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    # 保持每篇论文最多 N 条
    _trim_translation_history(db, paper_id)

    return TranslationHistoryCreateResponse(id=item.id, success=True)


@router.delete("/api/papers/{paper_id}/translation-history/{history_id}")
def delete_translation_history(
    paper_id: str, history_id: str, db: Session = Depends(get_db)
) -> dict:
    """删除单条翻译历史。"""
    item = (
        db.query(TranslationHistoryORM)
        .filter(TranslationHistoryORM.id == history_id, TranslationHistoryORM.paper_id == paper_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="翻译记录不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


def _trim_translation_history(db: Session, paper_id: str) -> None:
    """删除超出上限的最旧记录。"""
    count = (
        db.query(TranslationHistoryORM).filter(TranslationHistoryORM.paper_id == paper_id).count()
    )
    if count <= MAX_TRANSLATION_HISTORY_PER_PAPER:
        return
    overflow = count - MAX_TRANSLATION_HISTORY_PER_PAPER
    old_ids = (
        db.query(TranslationHistoryORM.id)
        .filter(TranslationHistoryORM.paper_id == paper_id)
        .order_by(TranslationHistoryORM.created_at.asc())
        .limit(overflow)
        .all()
    )
    if old_ids:
        old_id_list = [oid for (oid,) in old_ids]
        db.query(TranslationHistoryORM).filter(TranslationHistoryORM.id.in_(old_id_list)).delete(
            synchronize_session=False
        )


# ---------------------------------------------------------------------------
# WP-2.2: 被引情感（单篇论文 citation sentiment 缓存 + 查询）
# ---------------------------------------------------------------------------
@router.get("/api/papers/{paper_id}/sentiment")
def get_paper_sentiment_api(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """获取单篇论文的被引情感统计（基于 citation_sentiments 表）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    sentiments = crud.get_citation_sentiments_for_target(db, paper_id)
    return {
        "paperId": paper.id,
        "sentiments": sentiments,
        "counts": _aggregate_sentiment_counts(sentiments),
    }


# ---------------------------------------------------------------------------
# WP-2.2: 触发被引情感抽取（后台任务）
# ---------------------------------------------------------------------------
@router.post("/api/papers/{paper_id}/extract-citation-sentiment")
def extract_citation_sentiment_api(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """提交后台任务，抽取目标论文的被引情感。

    同一篇论文的 pending/running 任务未结束时，返回已有任务 ID，避免重复 LLM 调用。
    """
    from ..tasks import TaskManager
    from ..workers.reviews import citation_sentiment_worker

    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    # 检查是否已有进行中的被引情感任务（遍历所有 pending/running 任务，避免 limit=1 漏检）
    existing = TaskManager.list(task_type="citation_sentiment", status="pending") or []
    existing += TaskManager.list(task_type="citation_sentiment", status="running") or []
    for task in existing:
        if task.get("params", {}).get("paperId") == paper_id:
            return {"taskId": task["id"], "paperId": paper_id, "status": task["status"]}

    task_id = TaskManager.submit(
        "citation_sentiment",
        {"paperId": paper_id},
        worker_fn=citation_sentiment_worker,
    )
    return {"taskId": task_id, "paperId": paper_id, "status": "pending"}


# ---------------------------------------------------------------------------
# WP-2.2: 关系图（中心论文 + 引用它的论文 + 被引情感边）
# ---------------------------------------------------------------------------
@router.get("/api/papers/{paper_id}/relation-graph")
def get_relation_graph_api(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """获取以指定论文为中心的关系图（节点 + 边）。"""
    result = crud.get_relation_graph(db, paper_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    db.commit()
    return result


def _aggregate_sentiment_counts(sentiments: list[dict]) -> dict[str, int]:
    """统计被引情感标签数量。"""
    counts: dict[str, int] = {"support": 0, "criticize": 0, "background": 0}
    for s in sentiments:
        label = s.get("sentimentLabel", "background")
        if label in counts:
            counts[label] += 1
    return counts


# ---------------------------------------------------------------------------
# 论文综合评分与批量对比
# ---------------------------------------------------------------------------
@router.post("/api/papers/rank", response_model=RankResponse)
def rank_papers(req: RankRequest, db: Session = Depends(get_db)) -> RankResponse:
    """论文综合评分与排序。"""
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="主题不能为空")
    return crud.rank_papers(db, req.topic, req.weights, req.paperIds, req.topK)


@router.post("/api/papers/analysis", response_model=AnalysisReport)
def get_analysis_report(req: RankRequest, db: Session = Depends(get_db)) -> AnalysisReport:
    """生成综合分析报告。"""
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="主题不能为空")
    return crud.get_analysis_report(db, req.topic, req.weights, req.paperIds, req.topK)


@router.post("/api/papers/analyze", response_model=AnalyzeResponse)
def analyze_papers_route(req: AnalyzeRequest, db: Session = Depends(get_db)) -> AnalyzeResponse:
    """指定论文对比分析。"""
    if len(req.paper_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="请至少选择 2 篇论文进行分析",
        )
    return crud.analyze_papers(
        db,
        paper_ids=req.paper_ids,
        topic=req.topic,
        weights=req.weights,
    )


@router.post("/api/papers/compare", response_model=ComparePapersResponse)
def compare_papers_route(
    req: ComparePapersRequest, db: Session = Depends(get_db)
) -> ComparePapersResponse:
    """跨文档对比表：结构化抽取方法/样本量/主要结果等字段。"""
    # 去重并保持顺序
    seen: set[str] = set()
    unique_ids: list[str] = []
    for pid in req.paper_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)
    if len(unique_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择 2 篇论文进行对比")
    if len(unique_ids) > 20:
        raise HTTPException(status_code=400, detail="单次最多对比 20 篇论文")
    # 校验所有论文存在
    existing_ids = {p.id for p in db.query(PaperORM.id).filter(PaperORM.id.in_(unique_ids)).all()}
    missing = [pid for pid in unique_ids if pid not in existing_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"论文不存在: {', '.join(missing)}")
    try:
        result = crud.compare_papers(db, paper_ids=unique_ids, question=req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ComparePapersResponse(**result)


@router.post("/api/compare", response_model=ComparePapersResponse, include_in_schema=False)
def compare_papers_alias(
    req: ComparePapersRequest, db: Session = Depends(get_db)
) -> ComparePapersResponse:
    """旧路径 `/api/compare` 兼容别名，内部委托给 `/api/papers/compare` 处理。"""
    return compare_papers_route(req, db)


@router.post("/api/papers/batch-delete", response_model=BatchDeleteResponse)
def batch_delete_papers_route(
    req: BatchDeleteRequest, db: Session = Depends(get_db)
) -> BatchDeleteResponse:
    """批量删除论文（级联删除关联数据 + 本地 PDF 文件）。"""
    if not req.paper_ids:
        raise HTTPException(status_code=400, detail="paper_ids 不能为空")
    if len(req.paper_ids) > 50:
        raise HTTPException(status_code=400, detail="单次最多删除 50 篇论文")
    result = crud.batch_delete_papers(db, req.paper_ids)
    return result


# ---------------------------------------------------------------------------
# PDF 批注/高亮（CRUD，前端 localStorage 为主，后端为可选同步目标）
# ---------------------------------------------------------------------------
def _pdf_annotation_to_response(a: PdfAnnotationORM) -> PdfAnnotationResponse:
    return PdfAnnotationResponse(
        id=a.id,
        paperId=a.paper_id,
        page=a.page,
        quadpoints=a.quadpoints,
        color=a.color,
        note=a.note,
        createdAt=a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
    )


@router.get(
    "/api/papers/{paper_id}/pdf-annotations",
    response_model=list[PdfAnnotationResponse],
)
def list_pdf_annotations(
    paper_id: str, db: Session = Depends(get_db)
) -> list[PdfAnnotationResponse]:
    """列出某篇论文的所有 PDF 批注（按创建时间倒序）。"""
    if not db.query(PaperORM).filter(PaperORM.id == paper_id).first():
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    rows = (
        db.query(PdfAnnotationORM)
        .filter(PdfAnnotationORM.paper_id == paper_id)
        .order_by(PdfAnnotationORM.created_at.desc())
        .all()
    )
    return [_pdf_annotation_to_response(r) for r in rows]


@router.post(
    "/api/papers/{paper_id}/pdf-annotations",
    response_model=PdfAnnotationResponse,
    status_code=201,
)
def create_pdf_annotation(
    paper_id: str,
    payload: PdfAnnotationCreate,
    db: Session = Depends(get_db),
) -> PdfAnnotationResponse:
    """新建 PDF 批注（前端高亮后可选同步到后端）。"""
    if not db.query(PaperORM).filter(PaperORM.id == paper_id).first():
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    annotation = PdfAnnotationORM(
        id=str(uuid.uuid4()),
        paper_id=paper_id,
        page=payload.page,
        quadpoints=payload.quadpoints,
        color=payload.color,
        note=payload.note,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return _pdf_annotation_to_response(annotation)


@router.patch(
    "/api/pdf-annotations/{annotation_id}",
    response_model=PdfAnnotationResponse,
)
def update_pdf_annotation(
    annotation_id: str,
    payload: PdfAnnotationUpdate,
    db: Session = Depends(get_db),
) -> PdfAnnotationResponse:
    """更新批注（颜色 / 批注文字）。"""
    annotation = db.query(PdfAnnotationORM).filter(PdfAnnotationORM.id == annotation_id).first()
    if not annotation:
        raise HTTPException(status_code=404, detail="批注不存在")
    if payload.color is not None:
        annotation.color = payload.color
    if payload.note is not None:
        annotation.note = payload.note
    db.commit()
    db.refresh(annotation)
    return _pdf_annotation_to_response(annotation)


@router.delete("/api/pdf-annotations/{annotation_id}")
def delete_pdf_annotation(annotation_id: str, db: Session = Depends(get_db)) -> dict:
    """删除批注。"""
    annotation = db.query(PdfAnnotationORM).filter(PdfAnnotationORM.id == annotation_id).first()
    if not annotation:
        raise HTTPException(status_code=404, detail="批注不存在")
    db.delete(annotation)
    db.commit()
    return {"success": True}
