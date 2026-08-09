"""DEPTH reflection 感悟/读后/复现报告评审。

PR12 抽取：从 main.py 搬迁 reflection 路由 + 2 helpers。
共享锁从 concurrency.py 引入；视觉理解经外部 Qwen3-VL HTTP 调用。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func as _func
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import crud, database
from .. import tasks as task_manager
from ..concurrency import depth_review_lock as _depth_review_lock
from ..concurrency import reflection_file_lock as _reflection_file_lock
from ..database import get_db
from ..models import DepthReviewV4
from ..models import Paper as PaperORM
from ..pdf_parser import (
    MAX_BATCH_SIZE,
    _extract_docx_full_text,
    _extract_docx_metadata,
    _extract_pdf_metadata,
    extract_full_text,
    split_into_chunks,
)
from ..reflection_docx_parser import parse_docx_from_bytes
from ..report_paper_resolver import resolve_and_ingest
from ..routers.common import _as_dict
from ..schemas import (
    ReflectionCreateResponse,
    ReflectionFileBatchItem,
    ReflectionFileBatchResponse,
    ReflectionListResponse,
    ReflectionUploadRequest,
    SourcePaperInfo,
)
from ..workers import get_worker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reflection"])


@router.post("/api/depth/reflection/text", response_model=ReflectionCreateResponse, status_code=202)
async def create_reflection_report(
    payload: ReflectionUploadRequest, db: Session = Depends(get_db)
) -> ReflectionCreateResponse:
    """创建感悟/读后/复现报告并触发轻量 reflection 评审。

    流程：
    1. 复用 _find_duplicate_by_title_authors 去重（保留已有 paper_id）
    2. 写入 papers 表（source='upload', category='report'）
    3. 写 FTS5 索引
    4. 通过 TaskManager 提交 reflection_review 后台任务（统一 SSE/轮询路径）
    5. 立即返回 paper_id + taskId（HTTP 202，告知前端需轮询）

    ⚠️ 返回 202 Accepted 而非 201 Created：LLM 调用耗时不确定，
    同步响应容易触发网关超时（504）。前端通过 taskId 走
    GET /api/tasks/{taskId} 轮询，或 /api/tasks/{taskId}/stream 订阅 SSE。
    """
    import hashlib

    title = payload.title.strip()
    content = payload.content.strip()
    if not title:
        raise HTTPException(status_code=400, detail="报告标题不能为空")
    if len(content) < 10:
        raise HTTPException(status_code=400, detail="报告内容太短（至少 10 字符）")

    # 生成 paper_id（与 PDF 上传同源使用 content hash + title sanitize）
    h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    safe_title = re.sub(r"[^A-Za-z0-9_]", "_", title)
    safe_title = re.sub(r"_+", "_", safe_title).strip("_") or f"report_{h}"
    paper_id = f"report_{h}_{safe_title[:40]}"

    # 去重检查（可能已存在同标题同作者的报告）
    dup = crud._find_duplicate_by_title_authors(db, title, payload.authors or [])
    if dup and dup.id != paper_id:
        paper_id = dup.id

    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if paper is None:
        paper = PaperORM(
            id=paper_id,
            title=title,
            authors=list(payload.authors or []),
            abstract=content[:500],
            category="report",
            tags=[],
            year=payload.year or datetime.now().year,
            journal="",
            pdf_url="",
            citations=0,
            chunk_count=0,
            index_size=0,
            source="upload",
            full_text=content,
        )
        db.add(paper)
    else:
        # 更新已存在记录
        paper.title = title
        paper.authors = list(payload.authors or [])
        paper.abstract = content[:500]
        paper.year = payload.year or paper.year
        paper.full_text = content
        paper.category = "report"
    # 若前端显式指定了源论文，写入 source_paper_id
    if payload.sourcePaperId:
        paper.source_paper_id = payload.sourcePaperId
    db.commit()
    db.refresh(paper)

    # FTS5 索引
    try:
        authors_str = " ".join(payload.authors or [])
        # B8: 统一为 6 列（含 tags），与 crud/papers.py 的 FTS 写入一致。
        # 原 5 列 INSERT 在无 tags 列的早期库上能跑，但与 6 列版本不一致。
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
            ),
            {
                "pid": paper_id,
                "title": title,
                "abstract": content[:500],
                "authors": authors_str,
                "full_text": content,
                "tags": "",
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    # 提交到 TaskManager（统一走 reflection_review worker，与 /reflection/run 一致）。
    # 复用与 reflection/run 一样的 dedup + 自愈逻辑：避免同一 paper_id 上
    # 出现并发运行的 reflection 任务（两个 worker 同时写 reflection_result 会
    # 触发 last-writer-wins + 浪费 LLM 配额）。
    with _depth_review_lock:
        existing = (
            db.query(DepthReviewV4)
            .filter(
                DepthReviewV4.paper_id == paper_id,
                DepthReviewV4.kind == "report",
                DepthReviewV4.status.in_(["pending", "running"]),
            )
            .first()
        )
        if existing:
            now = datetime.now()
            existing.status = "failed"
            existing.completed_at = now
            existing.error_message = (
                f"[auto-recovered @ {now.isoformat(timespec='seconds')}] "
                "report 锁已释放（reflection/text 重复提交）"
            )
            db.commit()

    worker = get_worker("reflection_review")
    if worker is None:
        raise HTTPException(status_code=500, detail="任务系统未初始化")
    task_id = task_manager.TaskManager.submit(
        "reflection_review",
        params={"paperId": paper_id},
        worker_fn=worker,
    )

    source_paper_resp: SourcePaperInfo | None = None
    if payload.sourcePaperId:
        source_paper_resp = SourcePaperInfo(
            status="manual",
            paperId=payload.sourcePaperId,
            title=None,
        )
    return ReflectionCreateResponse(
        paperId=paper_id,
        taskId=task_id,
        documentType="report",
        message=(
            f"报告已入库，reflection 评审已提交（task_id={task_id}）；"
            f"通过 GET /api/tasks/{task_id} 跟踪进度。"
        ),
        sourcePaper=source_paper_resp,
    )


def _resolve_source_paper_for_reflection(
    db: Session,
    filename: str,
    file_bytes: bytes,
    source_paper_id: str | None = None,
) -> dict | None:
    """解析/导入报告引用的原论文，返回 source_paper 信息字典。

    该函数不修改 paper 对象，仅做查询/解析，因此可以在 reflection
    文件上传锁外执行，避免阻塞其他上传。
    """
    source_paper: dict | None = None
    if source_paper_id:
        try:
            sp = db.query(PaperORM).filter(PaperORM.id == source_paper_id).first()
            if sp:
                source_paper = {
                    "status": "manual",
                    "paperId": source_paper_id,
                    "title": sp.title,
                }
            else:
                source_paper = {
                    "status": "no_match",
                    "paperId": source_paper_id,
                    "title": None,
                }
        except Exception as exc:
            logger.warning("sourcePaperId 校验失败: %s", exc)
            source_paper = {
                "status": "import_failed",
                "paperId": source_paper_id,
                "title": None,
            }
    elif filename.lower().endswith(".docx"):
        try:
            # parse_docx_from_bytes imported at module level
            # resolve_and_ingest imported at module level

            doc = parse_docx_from_bytes(file_bytes, filename=filename)
            doc_title = (doc.paper_title or "").strip()
            if doc_title:
                resolved = resolve_and_ingest(doc_title, doc.paper_author, db=db)
                status = resolved.get("status", "no_match")
                if status == "download_failed":
                    status = "import_failed"
                source_paper = {
                    "status": status,
                    "paperId": resolved.get("paper_id"),
                    "title": resolved.get("title") or doc_title,
                    "source": resolved.get("source"),
                }
            else:
                source_paper = {"status": "no_title", "paperId": None, "title": None}
        except Exception as exc:
            logger.warning("reflection 原论文识别失败: %s", exc)
            source_paper = {"status": "import_failed", "paperId": None, "title": None}
    return source_paper


def _process_one_reflection_file_in_session(
    file_bytes: bytes,
    filename: str,
    db: Session,
    title: str,
    authors_csv: str,
    year: int | None,
    source_paper_id: str | None = None,
) -> dict:
    """处理单个感悟文件：抽取 → 去重 → 入库 → 提交 reflection 任务。

    抽出此函数以复用：单文件版 /file 和批量版 /files 都走它。
    返回 dict 与 ReflectionFileBatchItem 字段一一对应。

    错误处理：抛 HTTPException 让上层捕获，转化为 batch item 的 failed 状态。
    """
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（超过 10MB）")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")

    filename_stem = Path(filename).stem or "report"

    # 1. 按扩展名分发抽取
    full_text: str = ""
    extracted_title = ""
    extracted_authors: list[str] = []
    extracted_year = 0

    if filename.lower().endswith(".pdf"):
        full_text = extract_full_text(file_bytes)
        # 扫描 PDF 仅保留 pypdf 文本抽取；退役 OCR 不再静默补全文本。
        try:
            meta = _extract_pdf_metadata(file_bytes, filename or "report.pdf")
            extracted_title = meta.get("title") or ""
            extracted_authors = meta.get("authors") or []
            extracted_year = meta.get("year") or 0
        except Exception:
            pass
    elif filename.lower().endswith(".docx"):
        full_text = _extract_docx_full_text(file_bytes)
        try:
            meta = _extract_docx_metadata(file_bytes, filename or "report.docx")
            extracted_title = meta.get("title") or ""
            extracted_authors = meta.get("authors") or []
            extracted_year = meta.get("year") or 0
        except Exception:
            pass
    elif filename.lower().endswith((".txt", ".md")):
        full_text = file_bytes.decode("utf-8", errors="replace")
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不支持的文件格式「{Path(filename).suffix or '未知'}」，"
                "仅支持 .pdf / .docx / .txt / .md"
            ),
        )

    if not full_text or len(full_text.strip()) < 10:
        raise HTTPException(
            status_code=422,
            detail=(
                "未能从文件中提取文本（可能是扫描版 PDF / DOCX 损坏 / 纯图片）。"
                "请检查文件后再试，或使用「粘贴文本」路径。"
            ),
        )

    # 2. merge metadata
    final_title = (
        title.strip()
        if title and title.strip()
        else (extracted_title.strip() if extracted_title else filename_stem)
    )
    if not final_title:
        raise HTTPException(status_code=400, detail="报告标题不能为空")
    csv_authors = [a.strip() for a in authors_csv.split(",") if a.strip()] if authors_csv else []
    final_authors = csv_authors or extracted_authors
    final_year = year if year else extracted_year

    # 3. 计算 chunk_count / index_size（修复报告卡片显示 0 chunks / 0 B）
    chunks = split_into_chunks(full_text) if full_text else [""]
    chunk_count = len(chunks)
    index_size = sum(len(c.encode("utf-8")) for c in chunks)

    # 4. paper_id process（在加锁前计算候选 id；锁内会重新去重）
    h = hashlib.sha1(full_text.encode("utf-8")).hexdigest()[:12]
    safe_title = re.sub(r"[^A-Za-z0-9_]", "_", final_title)
    safe_title = re.sub(r"_+", "_", safe_title).strip("_") or f"report_{h}"
    candidate_paper_id = f"report_{h}_{safe_title[:40]}"

    # N1: 把源论文识别（resolve_and_ingest，含网络 IO + 3s sleep）移出锁外执行，
    # 避免持有 _reflection_file_lock 期间做 arXiv/S2 HTTP 请求导致并发上传串行化
    # 拉长到数十秒。使用已定义的 _resolve_source_paper_for_reflection 助手函数
    # （专为锁外执行设计，不修改 paper 对象，仅返回 source_paper 信息字典）。
    source_paper: dict | None = None
    try:
        source_paper = _resolve_source_paper_for_reflection(
            db, filename, file_bytes, source_paper_id=source_paper_id
        )
    except Exception as exc:
        logger.warning("源论文识别失败（锁外预解析）: %s", exc)
        source_paper = {"status": "import_failed", "paperId": None, "title": None}

    # 🛡️ reflection 文件上传关键区：串行化同一 paper_id 的并发上传，
    # 防止重复创建 paper / 重复提交 reflection 任务 / StaleDataError。
    with _reflection_file_lock:
        # 重新去重：文本抽取期间可能有其他请求已写入同一篇论文
        dup = crud._find_duplicate_by_title_authors(db, final_title, final_authors)
        if dup and dup.id != candidate_paper_id:
            paper_id = dup.id
        else:
            paper_id = candidate_paper_id

        # 5. Save to DB（整体在锁内：dedup→add→commit→FTS→任务提交 原子，
        # 修复 F1/F7 并发同文件上传撞 PK 导致的 500 / StaleDataError 与重复计费任务）
        paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if paper is None:
            paper = PaperORM(
                id=paper_id,
                title=final_title,
                authors=final_authors,
                abstract=full_text[:500],
                category="report",
                tags=[],
                year=final_year or datetime.now().year,
                source="upload",
                full_text=full_text,
                chunk_count=chunk_count,
                index_size=index_size,
            )
            db.add(paper)
        else:
            paper.title = final_title
            paper.authors = final_authors
            paper.abstract = full_text[:500]
            paper.year = final_year or paper.year
            paper.full_text = full_text
            paper.chunk_count = chunk_count
            paper.index_size = index_size

        # 6. 应用锁外预解析的源论文结果到 paper 对象（N1：网络 IO 已在锁外完成）
        if source_paper:
            _sp_status = source_paper.get("status", "no_match")
            paper.source_paper_status = _sp_status
            _sp_pid = source_paper.get("paperId")
            if _sp_status in ("exists", "imported", "manual") and _sp_pid:
                paper.source_paper_id = _sp_pid
            elif _sp_status in ("import_failed", "no_match", "no_title"):
                # 关键：同一 paper_id 重用时若本次解析失败，不能清空既有的
                # source_paper_id；但新建记录必须明确写入失败状态，避免后续
                # 任务误以为“尚未解析”而重复触发导入。
                if paper.source_paper_id is None:
                    paper.source_paper_status = _sp_status

        db.commit()
        db.refresh(paper)
        # 完整修复：保存 docx 原始字节到 uploads/，供 analyze_reflection_file 使用
        if filename.lower().endswith(".docx"):
            try:
                from ..database import DATA_DIR

                uploads_dir = DATA_DIR / "uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                docx_path = uploads_dir / f"{paper_id}.docx"
                docx_path.write_bytes(file_bytes)
                paper.reflection_docx_path = str(docx_path)
                db.commit()
            except Exception:
                logger.warning("保存 docx 到 uploads 失败: paper=%s", paper_id, exc_info=True)

        # 5. FTS5 sync
        try:
            authors_str = " ".join(final_authors)
            # B8: 统一为 6 列（含 tags），与 crud/papers.py 的 FTS 写入一致。
            db.execute(
                text(
                    "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                    "VALUES (:pid, :title, :abstract, :authors, :full_text, :tags)"
                ),
                {
                    "pid": paper_id,
                    "title": final_title,
                    "abstract": full_text[:500],
                    "authors": authors_str,
                    "full_text": full_text,
                    "tags": "",
                },
            )
            db.commit()
        except Exception:
            db.rollback()

        # 6. dedup 自愈 + TaskManager.submit（与单文件版 /file 保持行为一致）
        #    不加这个块会导致同一 paper_id 上同时跑多个 reflection 任务（race on
        #    reflection_result JSON writes + LLM 重复计费）。单文件版 /file 中已有
        #    相同逻辑，这里是补齐。
        #    使用 bulk update 替代 ORM 对象修改，避免 worker 线程并发修改同一行时
        #    触发 StaleDataError。
        now = datetime.now()
        db.query(DepthReviewV4).filter(
            DepthReviewV4.paper_id == paper_id,
            DepthReviewV4.kind == "report",
            DepthReviewV4.status.in_(["pending", "running"]),
        ).update(
            {
                "status": "failed",
                "completed_at": now,
                "error_message": (
                    f"[auto-recovered @ {now.isoformat(timespec='seconds')}] "
                    "report 锁已释放（reflection/file 重复提交）"
                ),
            },
            synchronize_session=False,
        )
        db.commit()

        worker = get_worker("reflection_review")
        if worker is None:
            raise HTTPException(status_code=500, detail="任务系统未初始化")
        task_id = task_manager.TaskManager.submit(
            "reflection_review",
            params={"paperId": paper_id},
            worker_fn=worker,
        )
        return {
            "filename": filename,
            "paperId": paper_id,
            "taskId": task_id,
            "status": "accepted",
            "error": "",
            "bytes": len(file_bytes),
            "sourcePaper": source_paper,
        }


def _process_one_reflection_file(
    file_bytes: bytes,
    filename: str,
    title: str,
    authors_csv: str,
    year: int | None,
    source_paper_id: str | None = None,
) -> dict:
    """在线程池中处理一个文件，并为该线程创建独立的数据库 Session。

    FastAPI 请求依赖注入的 Session 只属于请求线程，不能传入
    ``run_in_threadpool``；这里显式创建/关闭 Session，避免并发上传时
    SQLAlchemy 状态污染和 SQLite 锁竞争。
    """
    db = database.SessionLocal()
    try:
        return _process_one_reflection_file_in_session(
            file_bytes=file_bytes,
            filename=filename,
            db=db,
            title=title,
            authors_csv=authors_csv,
            year=year,
            source_paper_id=source_paper_id,
        )
    finally:
        db.close()


@router.post(
    "/api/depth/reflection/files",
    response_model=ReflectionFileBatchResponse,
    status_code=202,
)
async def create_reflection_reports_from_files(
    files: list[UploadFile] = File(
        ..., description="感悟文件列表（1-20 个，支持 PDF/DOCX/TXT/MD）"
    ),
    title: str = Form("", description="报告标题（可选）"),
    authors_csv: str = Form("", description="作者列表（逗号分隔）"),
    year: int | None = Form(None, description="年份（可选）"),
    sourcePaperId: str | None = Form(None, description="关联源论文 ID（可选，所有文件共用）"),
) -> ReflectionFileBatchResponse:
    """批量上传感悟/读后/复现报告。"""
    if not files:
        raise HTTPException(status_code=400, detail="未接收到文件")
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"单次批量最多上传 {MAX_BATCH_SIZE} 个文件")

    items: list[ReflectionFileBatchItem] = []
    submitted = 0
    failed = 0
    for f in files:
        filename = f.filename or "report"
        try:
            file_bytes = await f.read()
            result = await run_in_threadpool(
                _process_one_reflection_file,
                file_bytes=file_bytes,
                filename=filename,
                title=title,
                authors_csv=authors_csv,
                year=year,
                source_paper_id=sourcePaperId,
            )
            items.append(ReflectionFileBatchItem(**result))
            submitted += 1
        except Exception as e:
            items.append(
                ReflectionFileBatchItem(filename=filename, status="failed", error=str(e), bytes=0)
            )
            failed += 1
    return ReflectionFileBatchResponse(
        items=items, submitted=submitted, failed=failed, total=len(files), message="批量处理完成"
    )


@router.post("/api/depth/reflection/file", response_model=ReflectionCreateResponse, status_code=202)
async def create_reflection_report_from_file(
    file: UploadFile = File(...),
    title: str = Form(""),
    authors_csv: str = Form("", description="作者列表（逗号分隔）"),
    year: int | None = Form(None),
    sourcePaperId: str | None = Form(None),
) -> ReflectionCreateResponse:
    """从上传文件创建感悟/读后/复现报告，走与 /text 路一样的 reflection pipeline。

    支持格式：
    - .pdf   → pypdf 全文提取（+OCR 扫描版降级）
    - .docx  → python-docx 全文提取
    - .txt / .md → 原文本（UTF-8 解码，遇到坏字节降级 replace）

    与 /text 的区别是主体的来源（file vs text），后者子流程复用：
    1. 抽取完整文本 → 轻量纯文本抽奖器，不调用 process_one_pdf / _docx
       （这两个会插入论文进 source='upload' 表，反面起赋）
    2. 按⽂件 metadata 加 title/authors/year（允许调用者覆盖）
    3. 去重 + 写 papers + 写 FTS5 + TaskManager.submit（与 /text 一致）
    4. 返回 202 + paperId + taskId

    错误码：
    - 400 不支持的格式或表单校验失败
    - 422 提取后文本不足 10 字符（无法评审）
    - 413 上传过大（> 10MB）
    """
    # 1. 预读检查：用 Content-Length 头部拒绝超大上传，避免 OOM
    content_length_header = file.size if hasattr(file, "size") else None
    if content_length_header is not None and content_length_header > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（超过 10MB）")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(file_bytes) > 10 * 1024 * 1024:  # 二次保险：Content-Length 缺失或被伪造
        raise HTTPException(status_code=413, detail="文件过大（超过 10MB）")

    # 2. 复用统一 helper：抽取 → 入库 → 识别原论文 → 提交 reflection 任务
    result = await run_in_threadpool(
        _process_one_reflection_file,
        file_bytes=file_bytes,
        filename=file.filename or "report",
        title=title,
        authors_csv=authors_csv,
        year=year,
        source_paper_id=sourcePaperId,
    )
    suffix = Path(file.filename or "").suffix or "txt"
    return ReflectionCreateResponse(
        paperId=result["paperId"],
        taskId=result["taskId"],
        documentType="report",
        message=(
            f"报告(.{suffix})已解析入库，reflection 评审已提交"
            f"（task_id={result['taskId']}）；通过 GET /api/tasks/{result['taskId']} 跟踪进度。"
        ),
        sourcePaper=result.get("sourcePaper"),
    )


@router.post("/api/depth/reflection/run/{paper_id}")
async def reflection_run(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """手动重跑 reflection 评审（适用于 review_selected 场景）。

    行为：清空该论文所有 kind='report' 的 running 锁，重新提交后台评审。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")
    if not paper.full_text:
        raise HTTPException(
            status_code=400,
            detail="报告无全文（full_text 为空），无法进行 reflection 评审",
        )

    with _depth_review_lock:
        # 自愈：清理已存在的 kind='report' running 锁
        existing = (
            db.query(DepthReviewV4)
            .filter(
                DepthReviewV4.paper_id == paper_id,
                DepthReviewV4.kind == "report",
                DepthReviewV4.status.in_(["pending", "running"]),
            )
            .first()
        )
        if existing:
            now = datetime.now()
            existing.status = "failed"
            existing.completed_at = now
            existing.error_message = (
                f"[auto-recovered @ {now.isoformat(timespec='seconds')}] "
                "reflection 锁已释放，重新提交"
            )
            db.commit()

    # 提交后台任务
    worker = get_worker("reflection_review")
    if worker is None:
        raise HTTPException(status_code=500, detail="任务系统未初始化")
    task_id = task_manager.TaskManager.submit(
        "reflection_review",
        params={"paperId": paper_id},
        worker_fn=worker,
    )
    return {
        "task_id": task_id,
        "status": "pending",
        "paper_id": paper_id,
        "kind": "report",
        "message": (
            f"reflection 评审已提交（task_id={task_id}）；通过 GET /api/tasks/{task_id} 跟踪进度。"
        ),
    }


@router.get("/api/depth/reflection/list", response_model=ReflectionListResponse)
async def reflection_list(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ReflectionListResponse:
    """分页列出 reflection 报告评审记录（按平均分降序）。

    返回 items: [{id, paper_id, paper_title, status, scores, verdict, summary_short, ...}]
    """

    base_query = db.query(DepthReviewV4).filter(
        DepthReviewV4.kind == "report",
    )
    total = base_query.count()

    # 按 reflection_result.scores.average 降序（json_extract 不可用时 Python 侧）
    try:
        records = (
            base_query.order_by(
                _func.json_extract(DepthReviewV4.reflection_result, "$.scores.average").desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
    except Exception:
        all_records = base_query.all()

        def _get_avg(r):
            rr: dict = _as_dict(r.reflection_result)
            sc = rr.get("scores", {}) or {}
            return sc.get("average") or 0.0

        all_records.sort(key=_get_avg, reverse=True)
        records = all_records[offset : offset + limit]

    items: list[dict] = []
    paper_ids = [r.paper_id for r in records]
    paper_map: dict[str, PaperORM] = {}
    if paper_ids:
        rows = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
        paper_map = {p.id: p for p in rows}

    for r in records:
        rr: dict = _as_dict(r.reflection_result)
        analysis = _as_dict(rr.get("analysis_v2"))
        # analysis_v2 是完整分析的优先口径；LLM 失败时严禁回退到旧的默认分数。
        llm_failed = analysis.get("llm_failed", rr.get("llm_failed", False))
        llm_failed = r.status in ("failed", "timed_out") or (
            isinstance(llm_failed, (bool, int, float)) and bool(llm_failed)
        )
        scores = analysis.get("scores") or rr.get("scores", {}) or {}
        if not isinstance(scores, dict):
            scores = {}
        if llm_failed:
            scores = {}
        verdict = "llm_failed" if llm_failed else (analysis.get("verdict") or rr.get("verdict"))
        paper = paper_map.get(r.paper_id)
        items.append(
            {
                "id": r.id,
                "paper_id": r.paper_id,
                "paper_title": paper.title if paper else "",
                "status": r.status,
                "document_type": "report",
                "scores": {
                    "understanding_accuracy": None
                    if llm_failed
                    else scores.get("understanding_accuracy"),
                    "analysis_depth": None if llm_failed else scores.get("analysis_depth"),
                    "innovative_insights": None
                    if llm_failed
                    else scores.get("innovative_insights"),
                    "evidence_support": None if llm_failed else scores.get("evidence_support"),
                    # 修复：列表雷达按 6 维渲染，旧记录 scores 里自带 fidelity/coverage，
                    # 新记录在 analysis_v2（展开 scores）里；两处都要兜底，否则前端两轴恒为 0。
                    "fidelity": None
                    if llm_failed
                    else (
                        analysis.get("fidelity")
                        if analysis.get("fidelity") is not None
                        else scores.get("fidelity")
                    ),
                    "coverage": None
                    if llm_failed
                    else (
                        analysis.get("coverage")
                        if analysis.get("coverage") is not None
                        else scores.get("coverage")
                    ),
                    "average": None
                    if llm_failed
                    else (
                        analysis.get("average")
                        if analysis.get("average") is not None
                        else scores.get("average")
                    ),
                },
                "verdict": verdict,
                "verdict_reason": analysis.get("verdict_reason") or rr.get("verdict_reason", ""),
                "summary_short": (analysis.get("summary") or rr.get("summary", ""))[:200],
                "effective_evidence_count": analysis.get(
                    "effective_evidence_count", rr.get("effective_evidence_count", 0)
                ),
                "claims_count": len(analysis.get("claims", rr.get("claims", [])) or []),
                "fidelity": None
                if llm_failed
                else (
                    analysis.get("fidelity")
                    if analysis.get("fidelity") is not None
                    else rr.get("fidelity")
                ),
                "coverage": None
                if llm_failed
                else (
                    analysis.get("coverage")
                    if analysis.get("coverage") is not None
                    else rr.get("coverage")
                ),
                "llm_failed": llm_failed,
                "error_message": r.error_message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
        )

    return ReflectionListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/api/depth/reflection/result/{paper_id}")
async def reflection_get_result(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """查询论文最新一条 reflection 评审完整结果。"""
    record = (
        db.query(DepthReviewV4)
        .filter(
            DepthReviewV4.paper_id == paper_id,
            DepthReviewV4.kind == "report",
        )
        .order_by(DepthReviewV4.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 暂无 reflection 评审结果")
    result = _as_dict(record.reflection_result)
    failed = record.status in ("failed", "timed_out")
    failed_flag = result.get("llm_failed", False)
    analysis = _as_dict(result.get("analysis_v2"))
    nested_failed = analysis.get("llm_failed", False)
    failed = (
        failed
        or (isinstance(failed_flag, (bool, int, float)) and bool(failed_flag))
        or (isinstance(nested_failed, (bool, int, float)) and bool(nested_failed))
    )
    if failed:
        # 原始结果端点也必须遵守失败口径，不能把历史/部分写入的分数泄露给前端。
        result["scores"] = {}
        result["verdict"] = "llm_failed"
        result["llm_failed"] = True
        result["fidelity"] = None
        result["coverage"] = None
        result["analysis_v2"] = {}
    return {
        "id": record.id,
        "paper_id": record.paper_id,
        "kind": "report",
        "status": record.status,
        "version": record.version,
        "result": result,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }
