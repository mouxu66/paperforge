"""CS Paper Experiment Auditor API 路由。

端点：
- POST /api/experiment-audit/run/{paper_id}    提交异步审计（TaskManager）
- GET  /api/experiment-audit/result/{paper_id} 最新审计结果
- GET  /api/experiment-audit/list              审计历史列表（支持 min_severity/type 过滤）
- GET  /api/experiment-audit/findings/summary  跨论文 Finding 聚合（高危论文榜）
- GET  /api/experiment-audit/report/{audit_id} HTML 报告（浏览器存 PDF）
- POST /api/experiment-audit/leakage           P0-7 数据泄漏初筛（独立端点）
- POST /api/experiment-audit/code-audit        P1-1 代码配置 vs 论文超参比对（独立端点）
- POST /api/experiment-audit/relabeled-reuse   跨论文改标图片复用（NCC 像素 + VLM 语义）
- GET  /api/experiment-audit/finding-types     Finding 类型目录

SSE 进度复用 /api/tasks/{task_id}/events 现有通道。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..experiment_audit import data_leakage, figure_reuse, report
from ..experiment_audit.schemas import (
    AuditRequest,
    CodeAuditRequest,
    LeakageRequest,
    RelabeledReuseRequest,
    assign_finding_ids,
    coerce_findings,
)
from ..experiment_audit.service import AuditService, get_latest_audit
from ..models import AuditFinding, ExperimentAudit
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
        "findings": _inject_evidence_image_urls(audit.paper_id, coerce_findings(audit.findings)),
        "checks_run": audit.checks_run or [],
        "error_message": audit.error_message,
        "created_at": audit.created_at.strftime("%Y-%m-%d %H:%M:%S") if audit.created_at else None,
        "completed_at": audit.completed_at.strftime("%Y-%m-%d %H:%M:%S")
        if audit.completed_at
        else None,
    }


def _inject_evidence_image_urls(paper_id: str, findings: list[dict]) -> list[dict]:
    """读路径注入证据标注图 URL（表现层职责，检测器零感知）。

    P0-4 检测器把标注图本地路径写进 evidence_sources[].snippet
    （uploads/figures/<paper_id>/_audit/*.png）。DB 存的是生成机器的本地
    路径，前端无法直接消费——此处识别该形态并补 image_url 指向
    /api/experiment-audit/evidence-image 端点；原 snippet 保留供追查。
    路径形态不匹配时不动，纯文本证据不受影响。
    """
    for f in findings:
        for ev in f.get("evidence_sources") or []:
            snippet = ev.get("snippet")
            if not isinstance(snippet, str) or not snippet:
                continue
            parts = Path(snippet.replace("\\", "/")).parts
            if len(parts) >= 3 and parts[-2] == "_audit" and parts[-1].lower().endswith(".png"):
                ev["image_url"] = f"/api/experiment-audit/evidence-image/{paper_id}/{parts[-1]}"
    return findings


@router.get("/api/experiment-audit/evidence-image/{paper_id}/{filename}")
def serve_audit_evidence_image(
    paper_id: str,
    filename: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """提供 _audit/ 下的证据标注图（P0-4 红框/黄框叠加图），供前端 <img> 展示。

    安全：校验论文存在 + 文件名防目录穿越；不裸挂整个 uploads 目录
    （与 /api/figures/{paper_id}/image 同模式）。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    try:
        from ..utils.paths import _get_uploads_dir
    except ImportError:
        from ..pdf_parser import _get_uploads_dir

    img_path = _get_uploads_dir() / "figures" / paper_id / "_audit" / filename
    if not img_path.exists() or not img_path.is_file():
        raise HTTPException(status_code=404, detail="证据图不存在")
    return FileResponse(str(img_path))


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
    min_severity: str | None = Query(
        None, description="最低严重度过滤：low/medium/high（走 audit_findings 索引表）"
    ),
    finding_type: str | None = Query(
        None, description="Finding 类型过滤（如 SUSPICIOUS_DATA_PATTERN）"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """审计历史列表（含论文标题与发现计数）。

    min_severity / finding_type 过滤走 audit_findings 索引表（SQL 索引），
    未加过滤时行为与旧版完全一致（不触索引表，旧库无 backfill 也不受影响）。
    """
    query = db.query(ExperimentAudit)
    if min_severity or finding_type:
        sub = db.query(AuditFinding.audit_id).filter(AuditFinding.audit_id == ExperimentAudit.id)
        if min_severity:
            ranks = {"low": 1, "medium": 2, "high": 3}
            if min_severity not in ranks:
                raise HTTPException(status_code=400, detail="min_severity 须为 low/medium/high")
            sub = sub.filter(
                AuditFinding.severity.in_([s for s, r in ranks.items() if r >= ranks[min_severity]])
            )
        if finding_type:
            sub = sub.filter(AuditFinding.type == finding_type)
        query = query.filter(sub.exists())
    total = query.count()
    audits = query.order_by(ExperimentAudit.created_at.desc()).offset(offset).limit(limit).all()
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


@router.get("/api/experiment-audit/findings/summary")
def audit_findings_summary(
    min_severity: str = Query("high", description="聚合门槛：low/medium/high"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """跨论文 Finding 聚合：按论文聚合达标 Finding 计数（走索引表，替代全表 JSON 遍历）。

    用途：快速回答「库里哪些论文有高危发现、各多少条」。口径说明：
    - 只统计 audit_findings 表（写路径同步镜像）；2026-08 前的旧审计需先跑
      scripts/backfill_audit_findings.py 补齐，否则不计入。
    - 同一论文多轮审计的 Finding 全部计入（保留历史语义，与 /list 一致）。
    """
    ranks = {"low": 1, "medium": 2, "high": 3}
    if min_severity not in ranks:
        raise HTTPException(status_code=400, detail="min_severity 须为 low/medium/high")
    ok_severities = [s for s, r in ranks.items() if r >= ranks[min_severity]]

    rows = (
        db.query(
            AuditFinding.paper_id,
            AuditFinding.type,
            AuditFinding.severity,
        )
        .filter(AuditFinding.severity.in_(ok_severities))
        .all()
    )
    by_paper: dict[str, dict] = {}
    type_counter: dict[str, int] = {}
    for paper_id, ftype, sev in rows:
        entry = by_paper.setdefault(
            paper_id, {"paper_id": paper_id, "findings_count": 0, "types": {}}
        )
        entry["findings_count"] += 1
        entry["types"][ftype] = entry["types"].get(ftype, 0) + 1
        type_counter[ftype] = type_counter.get(ftype, 0) + 1

    paper_ids = list(by_paper.keys())
    titles = (
        {p.id: p.title for p in db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()}
        if paper_ids
        else {}
    )
    papers = [
        {**by_paper[pid], "paper_title": titles.get(pid, "")}
        for pid in sorted(by_paper, key=lambda p: by_paper[p]["findings_count"], reverse=True)[
            :limit
        ]
    ]
    return {
        "min_severity": min_severity,
        "papers_with_findings": len(by_paper),
        "total_findings": len(rows),
        "by_type": dict(sorted(type_counter.items(), key=lambda kv: -kv[1])),
        "papers": papers,
    }


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


@router.post("/api/experiment-audit/relabeled-reuse")
def run_relabeled_reuse_check(
    payload: RelabeledReuseRequest,
    db: Session = Depends(get_db),
) -> dict:
    """跨论文改标图片复用检测：NCC 条带像素复用 + VLM 目标蛋白语义比对。

    两篇论文需已上传且抽好 figure。同步执行（含 VLM 调用，耗时会随 max_pairs
    增大到几十秒），前端应显示加载态。
    """
    if payload.paper_a_id == payload.paper_b_id:
        raise HTTPException(status_code=400, detail="两篇论文不能相同")
    paper_a = db.query(PaperORM).filter(PaperORM.id == payload.paper_a_id).first()
    if paper_a is None:
        raise HTTPException(status_code=404, detail=f"论文不存在: {payload.paper_a_id}")
    paper_b = db.query(PaperORM).filter(PaperORM.id == payload.paper_b_id).first()
    if paper_b is None:
        raise HTTPException(status_code=404, detail=f"论文不存在: {payload.paper_b_id}")

    figs_a = figure_reuse.resolve_paper_figure_paths(db, payload.paper_a_id)
    figs_b = figure_reuse.resolve_paper_figure_paths(db, payload.paper_b_id)
    if not figs_a or not figs_b:
        raise HTTPException(
            status_code=400,
            detail=(
                f"图不足：{payload.paper_a_id}={len(figs_a)} 张、"
                f"{payload.paper_b_id}={len(figs_b)} 张，请先上传并抽取两篇论文的图"
            ),
        )

    findings = figure_reuse.detect_relabeled_image_reuse(
        {payload.paper_a_id: figs_a, payload.paper_b_id: figs_b},
        ncc_threshold=payload.ncc_threshold,
        max_pairs=payload.max_pairs,
    )
    findings = assign_finding_ids(findings)
    return {
        "paper_a_id": payload.paper_a_id,
        "paper_b_id": payload.paper_b_id,
        "ncc_threshold": payload.ncc_threshold,
        "findings": coerce_findings(findings),
        "findings_count": len(findings),
    }


@router.get("/api/experiment-audit/finding-types")
def get_finding_types() -> list[dict]:
    """Finding 类型目录（前端说明/筛选器用）。"""
    return report.finding_type_catalog()
