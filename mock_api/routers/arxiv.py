"""arXiv 检索与导入。

PR7 抽取：从 main.py 搬迁 arxiv 路由（依赖 arxiv_crawler）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud
from ..arxiv_crawler import search_arxiv
from ..database import get_db
from ..schemas import (
    ArxivImportRequest,
    ArxivImportResponse,
    ArxivSearchRequest,
    ArxivSearchResponse,
)

router = APIRouter(tags=["arxiv"])


@router.post("/api/arxiv/search", response_model=ArxivSearchResponse)
def arxiv_search(req: ArxivSearchRequest) -> ArxivSearchResponse:
    """arXiv 检索。"""
    if not req.keyword.strip():
        raise HTTPException(status_code=400, detail="检索词不能为空")
    try:
        papers = search_arxiv(req.keyword, max_results=req.maxResults or 20)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return ArxivSearchResponse(items=papers, total=len(papers))


@router.post("/api/arxiv/import", response_model=ArxivImportResponse)
def arxiv_import(req: ArxivImportRequest, db: Session = Depends(get_db)) -> ArxivImportResponse:
    """从 arXiv 导入选中论文。"""
    if not req.papers:
        raise HTTPException(status_code=400, detail="未选择要导入的论文")
    results = []
    success_count = 0
    fail_count = 0
    for preview in req.papers:
        try:
            # 优先使用 arXiv 预览项里的学科门类；后端爬虫已将其从 arXiv 子类映射到大学科。
            paper = crud.import_external_paper(
                db,
                paper_id=preview.id,
                title=preview.title,
                authors=preview.authors,
                year=preview.year,
                abstract=preview.abstract,
                pdf_url=f"https://arxiv.org/pdf/{preview.id}.pdf",
                source="arxiv",
                category=preview.category or "interdisciplinary",
            )
            results.append({"id": paper.id, "title": paper.title, "success": True})
            success_count += 1
        except Exception as e:
            results.append(
                {"id": preview.id, "title": preview.title, "success": False, "error": str(e)}
            )
            fail_count += 1
    return ArxivImportResponse(results=results, successCount=success_count, failCount=fail_count)
