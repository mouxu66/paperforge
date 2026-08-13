"""CS Paper Experiment Auditor API 路由。

端点：
- POST /api/experiment-audit/run/{paper_id}    提交异步审计（TaskManager）
- GET  /api/experiment-audit/result/{paper_id} 最新审计结果
- GET  /api/experiment-audit/list              审计历史列表
- GET  /api/experiment-audit/report/{audit_id} HTML 报告（浏览器存 PDF）
- POST /api/experiment-audit/leakage           P0-7 数据泄漏初筛（独立端点）
- POST /api/experiment-audit/code-audit        P1-1 代码配置 vs 论文超参比对（独立端点）
- GET  /api/experiment-audit/finding-types     Finding 类型目录

SSE 进度复用 /api/tasks/{task_id}/events 现有通道。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..experiment_audit import data_leakage, report
from ..experiment_audit.schemas import (
    AuditRequest,
    CodeAuditRequest,
    LeakageRequest,
    coerce_findings,
)
from ..experiment_audit.service import AuditService, get_latest_audit
from ..models import ExperimentAudit
from ..models import Paper as PaperORM
from ..tasks import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["experiment-audit"])


def _audit_worker(task_id: str, params: dict) -> None:
    """TaskManager worker：独立会话执行审计并回写任务状态。"""
    paper_id = params.get("paper_id", "")
    checks = params.get("checks")
    db = SessionLocal()
    try:
        TaskManager.update_progress(task_id, 5, "开始论文实验审计")
        audit = AuditService().run_paper_audit(db, paper_id, checks=checks)
        if audit.status == "failed":
            TaskManager.fail(task_id, audit.error_message or "审计失败")
            return
        TaskManager.complete(
            task_id,
            {
                "audit_id": audit.id,
                "paper_id": paper_id,
                "findings_count": len(audit.findings or []),
            },
        )
    except Exception as e:  # noqa: BLE001 - worker 顶层兜底
        logger.exception("实验审计 worker 异常")
        TaskManager.fail(task_id, str(e)[:2000])
    finally:
        db.close()


@router.post("/api/experiment-audit/run/{paper_id}")
def start_audit(
    paper_id: str,
    payload: AuditRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """提交异步审计任务，返回 task_id（进度走 /api/tasks SSE）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文不存在: {paper_id}")
    task_id = TaskManager.submit(
        "experiment_audit",
        params={"paper_id": paper_id, "checks": (payload.checks if payload else None)},
        worker_fn=_audit_worker,
    )
    return {"task_id": task_id, "paper_id": paper_id}


def _audit_to_dict(audit: ExperimentAudit, title: str = "") -> dict:
    return {
        "audit_id": audit.id,
        "paper_id": audit.paper_id,
        "paper_title": title,
        "status": audit.status,
        "source_pdf_hash": audit.source_pdf_hash,
        # 读路径校验：DB 脏数据降级为占位 Finding，不击穿前端（见 coerce_findings）
        "findings": coerce_findings(audit.findings),
        "checks_run": audit.checks_run or [],
        "error_message": audit.error_message,
        "created_at": audit.created_at.strftime("%Y-%m-%d %H:%M:%S") if audit.created_at else None,
        "completed_at": audit.completed_at.strftime("%Y-%m-%d %H:%M:%S")
        if audit.completed_at
        else None,
    }


@router.get("/api/experiment-audit/result/{paper_id}")
def get_audit_result(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """查询论文最新一次审计结果。"""
    audit = get_latest_audit(db, paper_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="该论文尚无审计记录")
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    return _audit_to_dict(audit, title=paper.title if paper else "")


@router.get("/api/experiment-audit/list")
def list_audits(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """审计历史列表（含论文标题与发现计数）。"""
    total = db.query(ExperimentAudit).count()
    audits = (
        db.query(ExperimentAudit)
        .order_by(ExperimentAudit.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    ids = [a.paper_id for a in audits]
    titles = (
        {p.id: p.title for p in db.query(PaperORM).filter(PaperORM.id.in_(ids)).all()}
        if ids
        else {}
    )
    items = [
        {
            "audit_id": a.id,
            "paper_id": a.paper_id,
            "paper_title": titles.get(a.paper_id, ""),
            "status": a.status,
            # 列表也走读路径容错：脏 findings 不产生误导性计数
            "findings_count": len(coerce_findings(a.findings)),
            "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else None,
        }
        for a in audits
    ]
    return {"total": total, "items": items}


@router.get("/api/experiment-audit/report/{audit_id}", response_class=HTMLResponse)
def get_audit_report(audit_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """HTML 审计报告（浏览器可直接打印存 PDF）。"""
    audit = db.query(ExperimentAudit).filter(ExperimentAudit.id == audit_id).first()
    if audit is None:
        raise HTTPException(status_code=404, detail="审计记录不存在")
    paper = db.query(PaperORM).filter(PaperORM.id == audit.paper_id).first()
    html_content = report.render_audit_html(audit, paper_title=paper.title if paper else "")
    return HTMLResponse(content=html_content)


@router.post("/api/experiment-audit/leakage")
def run_leakage_check(payload: LeakageRequest) -> dict:
    """P0-7 数据泄漏初筛（同步执行，建议小样本集使用）。"""
    try:
        findings = data_leakage.check_data_leakage(
            payload.train_dir,
            payload.test_dir,
            exact_hash=payload.exact_hash,
            phash_threshold=payload.phash_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"findings": findings, "findings_count": len(findings)}


@router.post("/api/experiment-audit/code-audit")
def run_code_audit_check(payload: CodeAuditRequest, db: Session = Depends(get_db)) -> dict:
    """P1-1 代码配置 vs 论文超参比对（独立端点，需提供代码仓库目录）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == payload.paper_id).first()
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    full_text = paper.full_text or ""
    if not full_text:
        raise HTTPException(status_code=400, detail="论文无全文，无法比对超参")
    try:
        from ..experiment_audit import code_audit

        findings = code_audit.check_config_mismatch(full_text, payload.repo_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"findings": findings, "findings_count": len(findings)}


@router.get("/api/experiment-audit/finding-types")
def get_finding_types() -> list[dict]:
    """10 种 Finding 类型目录（前端说明/筛选器用）。"""
    return report.finding_type_catalog()
