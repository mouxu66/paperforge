"""PDF/DOCX/ZIP 上传入库。

PR6 抽取：从 main.py 搬迁 upload 路由 + _sanitize_filename helper。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..database import get_db
from ..pdf_parser import (
    MAX_BATCH_SIZE,
    process_one_docx,
    process_one_pdf,
    process_zip_upload,
)
from ..schemas import UploadBatchResponse, UploadedPaper, ZipUploadResponse

router = APIRouter(tags=["upload"])


def _sanitize_filename(filename: str) -> str:
    """对用户上传文件名做友好展示用的 sanitize。

    只保留常见字符（字母、数字、中文、下划线、连字符、点、括号），
    去除路径分隔符与空字符，避免日志/JSON 中注入或显示异常。
    paper_id 的严格 sanitize 在 pdf_parser._gen_paper_id 中完成。
    """
    if not filename:
        return "unnamed"
    filename = filename.strip().replace("\\", "_").replace("/", "_").replace("\x00", "")
    return filename or "unnamed"


@router.post("/api/upload-paper", response_model=UploadedPaper)
async def upload_paper(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> UploadedPaper:
    """单个 PDF 解析入库。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    safe_name = _sanitize_filename(file.filename)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext == "docx":
        result = await run_in_threadpool(process_one_docx, file_bytes, safe_name, db)
    else:
        result = await run_in_threadpool(process_one_pdf, file_bytes, safe_name, db)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return UploadedPaper(**result)


@router.post("/api/upload-batch", response_model=UploadBatchResponse)
async def upload_batch(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
) -> UploadBatchResponse:
    """批量 PDF 解析入库。"""
    if not files:
        raise HTTPException(status_code=400, detail="未接收到文件")
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"单次批量最多上传 {MAX_BATCH_SIZE} 个文件")
    results: list[UploadedPaper] = []
    success_count = 0
    fail_count = 0
    unsupported_count = 0
    for f in files:
        filename = f.filename or "report"
        try:
            safe_name = _sanitize_filename(filename)
            file_bytes = await f.read()
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext == "docx":
                result = await run_in_threadpool(process_one_docx, file_bytes, safe_name, db)
            elif ext == "pdf":
                result = await run_in_threadpool(process_one_pdf, file_bytes, safe_name, db)
            else:
                results.append(
                    UploadedPaper(
                        id="",
                        title=filename,
                        authors=[],
                        year=0,
                        abstract="",
                        source="upload",
                        success=False,
                        error=f"不支持的文件格式「{ext or '未知'}」，仅支持 PDF 和 DOCX",
                    )
                )
                unsupported_count += 1
                continue
            if result.get("error"):
                results.append(
                    UploadedPaper(
                        id="",
                        title=filename,
                        authors=[],
                        year=0,
                        abstract="",
                        source="upload",
                        success=False,
                        error=result["error"],
                    )
                )
                fail_count += 1
            else:
                results.append(UploadedPaper(**result))
                success_count += 1
        except Exception as e:
            results.append(
                UploadedPaper(
                    id="",
                    title=filename,
                    authors=[],
                    year=0,
                    abstract="",
                    source="upload",
                    success=False,
                    error=str(e),
                )
            )
            fail_count += 1
    return UploadBatchResponse(
        results=results,
        successCount=success_count,
        failCount=fail_count,
        unsupportedCount=unsupported_count,
    )


@router.post("/api/upload-zip", response_model=ZipUploadResponse)
async def upload_zip(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> ZipUploadResponse:
    """ZIP 打包上传（解压后逐个入库）。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    result = await run_in_threadpool(process_zip_upload, file_bytes, file.filename, db)
    return ZipUploadResponse(**result)
