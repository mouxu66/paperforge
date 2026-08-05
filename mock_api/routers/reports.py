"""报告批阅 / 学术诚信视图。

PR13 抽取：从 main.py 搬迁 reports 路由（analyze_report + report_submissions）。
依赖 reflection_pipeline 进行感悟文件分析。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..export_tasks import get_export_task, start_integrity_batch_task
from ..integrity_report import build_integrity_report, render_integrity_docx, render_integrity_html
from ..models import DepthReviewV4
from ..models import Paper as PaperORM
from ..routers.common import _as_dict

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])


# ---------------------------------------------------------------------------
# 感悟报告分析整合端点（parser + 自动绑定 + fidelity + 4维 → 5维）
# 对应 deliverables/reflection_analysis_spec.html
# ---------------------------------------------------------------------------


@router.post("/api/reports/analyze")
async def analyze_report(
    file: UploadFile = File(...),
    sourcePaperId: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    """提交感悟报告 docx → 解析 + 自动绑定原论文 + 忠实度 + 4维 → 5维分析。

    对应「自用写完即测」场景：同步返回完整分析（学号/姓名/5维分/verdict/
    fidelity/anchors/stray_claims）。结果 upsert 到 DepthReviewV4(kind='report')
    的 reflection_result["analysis_v2"]，供 /api/reports/submissions 读取。
    LLM 不可用时 4 维降级为结构启发式，fidelity 仍可用（退化模式）。
    """
    import tempfile

    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式感悟报告")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件为空")
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        try:
            from ..reflection_pipeline import analyze_reflection_file

            result = analyze_reflection_file(tmp.name, db, sourcePaperId)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"分析失败: {e}") from e
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    # upsert 报告 paper + DepthReviewV4（含 analysis_v2），失败不影响返回
    try:
        # B4: 原版用标题哈希生成 paper_id，与 _process_one_reflection_file 用
        # full_text 哈希不一致 → 同内容经 /file 与 /reports/analyze 两条路径
        # 得到两个不同 ID，批阅视图与反思评审视图无法对应。
        # 统一为 full_text 哈希规则，并写入 full_text + source_paper_id。
        _report_full_text = result.get("full_text") or result.get("text") or ""
        if not _report_full_text:
            # 分析结果中可能没有 full_text，用标题兜底（避免空哈希）
            _report_full_text = result.get("paper_title") or "report"
        h = hashlib.sha1(_report_full_text.encode("utf-8")).hexdigest()[:12]
        safe_title = re.sub(r"[^A-Za-z0-9_]", "_", result.get("paper_title") or "report")
        safe_title = re.sub(r"_+", "_", safe_title).strip("_") or f"report_{h}"
        rid = f"report_{h}_{safe_title[:40]}"
        paper = db.query(PaperORM).filter(PaperORM.id == rid).first()
        if paper is None:
            paper = PaperORM(
                id=rid,
                title=result.get("paper_title") or "报告",
                category="report",
                full_text=_report_full_text,
                source="upload",
            )
            db.add(paper)
        # B4: 写入 source_paper_id 绑定关系（原版丢失此字段）
        _src_pid = result.get("bound_paper_id") or sourcePaperId
        if _src_pid:
            paper.source_paper_id = _src_pid
            db.add(paper)
        rev = (
            db.query(DepthReviewV4)
            .filter(DepthReviewV4.paper_id == rid, DepthReviewV4.kind == "report")
            .first()
        )
        if rev is None:
            rev = DepthReviewV4(paper_id=rid, kind="report", status="completed")
            db.add(rev)
        prev = _as_dict(rev.reflection_result)
        prev["analysis_v2"] = result
        rev.reflection_result = prev
        rev.status = "completed"
        rev.completed_at = datetime.now()
        db.commit()
    except Exception as e:
        logger.warning("analyze upsert 失败（不影响返回）: %s", e)

    return result


# ---------------------------------------------------------------------------
# 学生「AI 使用声明 + 真实性报告」一页纸（2026-08-05 新增）
# 数据源：DepthReviewV4.reflection_result["analysis_v2"]（只读，不改评审流水线）
# 设计：确定性拼接（证据性数据不走 LLM）+ docx 主导出 + HTML 打印存 PDF
# ---------------------------------------------------------------------------


@router.get("/api/reports/{paper_id}/integrity")
def get_integrity_report(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """学生「AI 使用声明 + 真实性报告」一页纸（JSON，供前端预览）。

    从报告评审记录（kind='report'）确定性组装；无记录返回 404。
    """
    report = build_integrity_report(db, paper_id)
    if report is None:
        raise HTTPException(status_code=404, detail="未找到该感悟报告的评审记录")
    return report


@router.post("/api/reports/{paper_id}/integrity/export")
def export_integrity_report(
    paper_id: str,
    fmt: str = Query("docx", alias="format", pattern="^(docx|html)$"),
    db: Session = Depends(get_db),
) -> Response:
    """导出诚信报告：format=docx（python-docx 归档）或 html（浏览器打印存 PDF）。"""
    report = build_integrity_report(db, paper_id)
    if report is None:
        raise HTTPException(status_code=404, detail="未找到该感悟报告的评审记录")
    # 学号来自学生 docx 头部（不可信输入），必须先消毒再进 Content-Disposition，
    # 防止引号/换行注入响应头；消毒失败回退 "report"。
    raw_sid = (report.get("student") or {}).get("id") or paper_id
    safe_sid = re.sub(r"[^A-Za-z0-9_-]", "", raw_sid) or "report"
    if fmt == "html":
        return Response(
            content=render_integrity_html(report),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="integrity_{safe_sid}.html"'},
        )
    display = f"{safe_sid}_诚信报告.docx"
    return Response(
        content=render_integrity_docx(report),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"integrity_{safe_sid}.docx\"; filename*=UTF-8''{quote(display)}"
            )
        },
    )


# ---------------------------------------------------------------------------
# 全班诚信报告批量打包（zip，每生一份 docx）—— 复用 export_tasks 异步任务基建
# ---------------------------------------------------------------------------


@router.post("/api/reports/integrity/export-batch", status_code=202)
def start_integrity_batch_export() -> dict:
    """一键导出全班诚信报告：后台打包 zip（每生一份 docx），返回 task_id。

    后台 daemon 线程遍历全部 kind='report' 评审记录，逐篇渲染 docx 后
    压缩为 zip 落盘（DATA_DIR/exports/）；完成后可经 download 端点拉取。
    """
    task_id = start_integrity_batch_task()
    return {"task_id": task_id, "status": "running"}


@router.get("/api/reports/integrity/export-batch/{task_id}/progress")
def get_integrity_batch_progress(task_id: str) -> dict:
    """轮询批量打包进度（响应结构与 writing 导出 progress 兼容）。"""
    task = get_export_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="批量导出任务不存在或已过期")
    return {
        "taskId": task.task_id,
        "progress": task.progress,
        "status": task.status,
        "error": task.error,
        # 兼容 writing 进度结构：批量任务不涉及章节/内容字段，置空
        "currentChapter": 0,
        "totalChapters": 0,
        "content": "",
        "filename": "",
        "references": [],
    }


@router.get("/api/reports/integrity/export-batch/{task_id}/download")
def download_integrity_batch(task_id: str) -> FileResponse:
    """下载打包好的 zip。

    文件下载后**不立即删除**（网络中断可重试、可重复下载）；
    由 render_integrity_batch_zip 的 24h 过期清理统一回收。
    """
    task = get_export_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="批量导出任务不存在或已过期")
    if task.status != "done" or not task.result_path:
        raise HTTPException(status_code=409, detail="批量导出尚未完成")
    path = Path(task.result_path)
    try:
        from ..database import DATA_DIR

        export_root = DATA_DIR / "exports"
        resolved_root = export_root.resolve()
        resolved_path = path.resolve()
        if (
            not resolved_path.is_file()
            or path.is_symlink()
            or resolved_path.parent != resolved_root
            or resolved_path.name != path.name
            or not resolved_path.name.startswith("integrity_reports_")
            or resolved_path.suffix.lower() != ".zip"
        ):
            raise HTTPException(status_code=404, detail="zip 文件不存在或已被清理，请重新导出")
        path = resolved_path
    except OSError as exc:
        raise HTTPException(status_code=404, detail="zip 文件不存在或已被清理，请重新导出") from exc
    display = f"诚信报告_全班_{datetime.now().strftime('%Y%m%d')}.zip"
    return FileResponse(path, media_type="application/zip", filename=display)


@router.get("/api/reports/submissions")
async def report_submissions(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """批阅视图：列出学生感悟报告分析记录（学号/姓名/绑定论文/5维/verdict）。

    从 DepthReviewV4(kind='report') 读取；若 analyze 端点已写入扩展键
    analysis_v2，则展示 5 维与学号/姓名，否则回退现有 4 维。
    """
    records = (
        db.query(DepthReviewV4)
        .filter(DepthReviewV4.kind == "report")
        .order_by(DepthReviewV4.created_at.desc())
        .limit(limit)
        .all()
    )
    paper_ids = [r.paper_id for r in records]
    paper_map: dict = {}
    if paper_ids:
        rows = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
        paper_map = {p.id: p for p in rows}
    items = []
    for r in records:
        rr = _as_dict(r.reflection_result)
        ext = _as_dict(rr.get("analysis_v2"))
        llm_failed = ext.get("llm_failed", rr.get("llm_failed", False))
        llm_failed = r.status in ("failed", "timed_out") or (
            isinstance(llm_failed, (bool, int, float)) and bool(llm_failed)
        )
        paper = paper_map.get(r.paper_id)
        raw_scores = ext.get("scores") or rr.get("scores") or {}
        scores = raw_scores if isinstance(raw_scores, dict) else {}
        if llm_failed:
            scores = None
        average = (
            ext.get("average")
            if ext.get("average") is not None
            else (rr.get("scores") or {}).get("average")
        )
        if llm_failed:
            average = None
        verdict = "llm_failed" if llm_failed else (ext.get("verdict") or rr.get("verdict"))
        items.append(
            {
                "paper_id": r.paper_id,
                "student_id": ext.get("student_id"),
                "student_name": ext.get("student_name"),
                "paper_title": paper.title if paper else (ext.get("paper_title") or ""),
                "bound_paper_id": ext.get("bound_paper_id"),
                "scores": scores,
                "average": average,
                "verdict": verdict,
                "fidelity": None if llm_failed else ext.get("fidelity"),
                "coverage": None if llm_failed else ext.get("coverage"),
                "coverage_uncovered": [] if llm_failed else ext.get("coverage_uncovered"),
                "llm_failed": llm_failed,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
        )
    return {"items": items, "total": len(items)}
