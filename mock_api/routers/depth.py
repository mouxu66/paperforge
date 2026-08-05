"""DEPTH 论文多维评审（v1 + v4.2 + 统一列表）。

PR11 抽取：从 main.py 搬迁 depth 路由 + 2 helpers。
共享锁 depth_review_lock 从 concurrency.py 引入。
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import depth_eval, depth_tasks
from .. import tasks as task_manager
from ..concurrency import depth_review_lock as _depth_review_lock
from ..database import get_db
from ..models import DepthReviewV4
from ..models import Paper as PaperORM
from ..routers.common import _safe_dict
from ..schemas import (
    BatchDeleteResponse,
    BatchDeleteReviewRequest,
    DepthBatchRequest,
    DepthBatchResponse,
    DepthScoreRequest,
    DepthScoreResponse,
    DepthV4ReviewSelectedRequest,
    UnifiedReviewListResponse,
)
from ..settings import get_settings
from ..workers import get_worker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["depth"])


@router.post("/api/depth/score")
async def depth_score_single(
    payload: DepthScoreRequest,
    db: Session = Depends(get_db),
) -> DepthScoreResponse:
    """单篇 DEPTH 评分（同步，适用于快速测试单篇论文）。

    请求体：{"paper_id": "1706.03762"}
    优先返回缓存；无缓存则现场评估。
    """
    paper_id = payload.paper_id

    cached = depth_eval.get_cached_score(db, paper_id)
    if cached:
        return DepthScoreResponse(**cached)

    # [P2-3 MAIN_PASSTHROUGH] forward compute_mode from request body if present
    _cm = (
        payload.get("compute_mode")
        if isinstance(payload, dict)
        else getattr(payload, "compute_mode", None)
    )
    if _cm:
        result = depth_eval.evaluate_paper(paper_id, db=db, compute_mode=_cm)
    else:
        result = depth_eval.evaluate_paper(paper_id, db=db)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return DepthScoreResponse(**result)


@router.post("/api/depth/batch", response_model=DepthBatchResponse)
async def depth_batch_start(
    request: DepthBatchRequest,
    db: Session = Depends(get_db),
) -> DepthBatchResponse:
    """批量 DEPTH 评估（异步）：提交论文列表，返回 task_id。"""
    if not request.paper_ids:
        raise HTTPException(status_code=400, detail="paper_ids 不能为空")

    papers = db.query(PaperORM).filter(PaperORM.id.in_(request.paper_ids)).all()
    if not papers:
        raise HTTPException(status_code=404, detail="未找到论文")

    task_id = depth_tasks.submit_depth_job([p.id for p in papers])
    return DepthBatchResponse(task_id=task_id, total_papers=len(papers))


@router.get("/api/depth/status/{task_id}")
async def depth_batch_status(task_id: str) -> dict:
    """查询批量 DEPTH 评估任务状态。"""
    task = depth_tasks.get_job_status(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("/api/depth/score/{paper_id}", response_model=DepthScoreResponse)
async def depth_get_score(paper_id: str, db: Session = Depends(get_db)) -> DepthScoreResponse:
    """查询单篇论文已缓存的 DEPTH 评分。"""
    cached = depth_eval.get_cached_score(db, paper_id)
    if not cached:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 暂无 DEPTH 评分")
    return DepthScoreResponse(**cached)


# ---------------------------------------------------------------------------
# DEPTH v4.2 审稿（九节点串行流水线）
# ---------------------------------------------------------------------------


def _has_active_depth_review_task(paper_id: str) -> bool:
    """判断 tasks 表里是否存在针对该 paper 的真实活跃 depth_review 任务。

    这是 dedup 自愈判定的关键依据：
    - 若返回 True：旧 DepthReviewV4 锁对应的 Task 仍在跑，必须 409。
    - 若返回 False：DepthReviewV4 是孤儿（其 Task 已终态或被清理），可自愈放行。

    ⚠️ SQLite json1 扩展在某些 Windows wheels 上可能缺失（无法调用 json_extract）。
    采用双路径：先 try json_extract 句型，仅当 OperationalError 提示 json1 缺失
    才回退到 Python 侧 json.loads(params).get("paperId") 过滤；其它 DB 错误
    仍正常向上抛（由 FastAPI 渲染 500），不掩盖真实故障。
    """

    from ..database import SessionLocal

    db = SessionLocal()
    try:
        # 路径 A：使用 json_extract（更快，索引可优化）
        try:
            row = db.execute(
                text(
                    "SELECT id FROM tasks "
                    "WHERE type = 'depth_review' "
                    "  AND status IN ('pending', 'running') "
                    "  AND json_extract(params, '$.paperId') = :paper_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"paper_id": paper_id},
            ).first()
            return row is not None
        except OperationalError as exc:
            # 仅当错误源于 json1 缺失才回退；其它 OperationalError 立即重抛
            msg = str(exc).lower()
            if "json1" not in msg and "json_extract" not in msg and "no such function" not in msg:
                raise
            # json1 不可用：走路径 B
            db.rollback()
            rows = db.execute(
                text(
                    "SELECT id, params FROM tasks "
                    "WHERE type = 'depth_review' "
                    "  AND status IN ('pending', 'running') "
                    "ORDER BY created_at DESC LIMIT 50"
                )
            ).all()
            for _id, raw in rows:
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict) and parsed.get("paperId") == paper_id:
                    return True
            return False
    finally:
        db.close()


def _submit_single_v4_review(paper_id: str, db: Session) -> tuple[str | None, str | None]:
    """提交单篇论文的 V4.2 深度审稿（带 dedup + 自愈）。

    Returns:
        (task_id, None) 如果成功提交
        (None, skip_reason) 如果跳过（已有活跃任务 / 无全文等）
    Raises:
        HTTPException 如果任务系统未初始化
    """
    with _depth_review_lock:
        existing = (
            db.query(DepthReviewV4)
            .filter(
                DepthReviewV4.paper_id == paper_id,
                DepthReviewV4.status.in_(["pending", "running"]),
            )
            .first()
        )
        if existing:
            if _has_active_depth_review_task(paper_id):
                return None, "已有进行中的审稿任务"
            # 自愈：清理幽灵锁
            now = datetime.now()
            existing.status = "failed"
            existing.completed_at = now
            existing.error_message = (
                f"[auto-recovered @ {now.isoformat(timespec='seconds')}] "
                "深度任务锁已失效：底层 Task 不在 active 状态，自动放行。"
            )
            db.commit()
            logger.info(
                "DEPTH v4.2 dedup 自愈：清理幽灵 DepthReviewV4[%s] for paper=%s",
                existing.id,
                paper_id,
            )

        worker = get_worker("depth_review")
        if worker is None:
            raise HTTPException(status_code=500, detail="任务系统未初始化")
        task_id = task_manager.TaskManager.submit(
            "depth_review",
            params={"paperId": paper_id},
            worker_fn=worker,
        )
        return task_id, None


@router.post("/api/depth/v4/review/{paper_id}")
async def depth_v4_start_review(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """提交 DEPTH v4.2 深度审稿任务（异步，通过 TaskManager 执行）。

    检查论文是否存在、检查是否有 pending/running 任务，
    提交到 TaskManager 后台执行，立即返回 task_id。
    客户端轮询 GET /api/tasks/{task_id} 或 GET /api/tasks/{task_id}/stream (SSE)。

    🛡 Dedup 自愈：旧的 DepthReviewV4 锁对应的 Task 已终态或不存在时，
    自动把锁标记为 failed（log: '深度任务锁已失效，自动放行'），允许重新提交。
    """
    # 检查论文存在
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 不存在")

    task_id, skip_reason = _submit_single_v4_review(paper_id, db)
    if skip_reason:
        raise HTTPException(
            status_code=409,
            detail=(
                f"论文 {paper_id} {skip_reason}，请等待完成后再提交。\n"
                f"提示：可在「深度审稿记录」列表页查看实时进度。"
            ),
        )

    return {
        "task_id": task_id,
        "status": "pending",
        "paper_id": paper_id,
        "recoveredFromGhost": False,
        "previousLockId": None,
        "message": (
            f"审稿任务已提交（task_id={task_id}）；通过 GET /api/tasks/{task_id} 跟踪进度。"
        ),
    }


@router.get("/api/depth/v4/result/{paper_id}")
async def depth_v4_get_result(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """查询论文最新的 DEPTH v4.2 审稿结果。

    返回最新一条记录的完整审稿结果，不存在时返回 404。
    """
    record = (
        db.query(DepthReviewV4)
        .filter(DepthReviewV4.paper_id == paper_id)
        .order_by(DepthReviewV4.created_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"论文 {paper_id} 暂无 DEPTH v4.2 审稿结果")

    fv = record.final_verdict or {}
    calibrated = fv.get("calibrated_score") if isinstance(fv, dict) else None
    final_score = (
        round(calibrated * 100, 1)
        if get_settings().v4_final_score_convert and calibrated is not None
        else None
    )
    # M0 矢量渲染开关状态：让前端在「深度评审」区域知道图是否会被分析
    m0_active = get_settings().depth_vector_render_enabled
    # 把 m0_active 注入 final_verdict，这样 FigureConsistencyCard 能直接读取到
    final_verdict_for_response = (
        {**fv, "m0_active": m0_active} if isinstance(fv, dict) else {"m0_active": m0_active}
    )
    return {
        "id": record.id,
        "paper_id": record.paper_id,
        "status": record.status,
        "q0_result": record.q0_result,
        "q1_result": record.q1_result,
        "evidence_pool": record.evidence_pool,
        "q2_result": record.q2_result,
        "q3_result": record.q3_result,
        "q4_result": record.q4_result,
        "q5a_result": record.q5a_result,
        "q5b_result": record.q5b_result,
        "q5c_result": record.q5c_result,
        "final_verdict": final_verdict_for_response,
        "final_score": final_score,
        "node_score_stds": fv.get("node_score_stds", {}) if isinstance(fv, dict) else {},
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


@router.get("/api/depth/v4/list")
async def depth_v4_list(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """分页列出 DEPTH v4.2 审稿记录（仅 v4.2，按 novelty_score 降序）。

    排序：优先使用 SQLite json_extract 按 q2_result.novelty_score 降序；
    若 json1 扩展不可用，回退到 Python 侧排序。

    注意：本接口返回 **每次审稿尝试一行**（同一论文多次重审会显示多条）。
    若想按论文聚合查看，请改用 /api/depth/v4/papers。
    """
    base_query = db.query(DepthReviewV4).filter(DepthReviewV4.version.in_(["v4.1", "v4.2"]))
    total = base_query.count()

    # 尝试 SQL 层排序（需要 json1 扩展），失败则 Python 排序
    try:
        records = (
            base_query.order_by(
                func.json_extract(DepthReviewV4.q2_result, "$.novelty_score").desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
    except Exception:
        # json_extract 不可用（如 SQLite 未编译 json1）→ 全量取出，Python 排序后分页
        all_records = base_query.all()

        def _get_novelty(r):
            q2 = r.q2_result or {}
            score = q2.get("novelty_score")
            return score if score is not None else 0.0

        all_records.sort(key=_get_novelty, reverse=True)
        records = all_records[offset : offset + limit]

    items = []
    for r in records:
        # 关联论文标题
        paper = db.query(PaperORM).filter(PaperORM.id == r.paper_id).first()
        q2 = r.q2_result or {}
        items.append(
            {
                "id": r.id,
                "paper_id": r.paper_id,
                "paper_title": paper.title if paper else "",
                "status": r.status,
                "novelty_score": q2.get("novelty_score"),
                "hotspot_alignment_score": q2.get("hotspot_alignment_score"),
                "core_contribution": q2.get("core_contribution"),
                "final_verdict": (r.final_verdict or {}).get("final_verdict")
                if r.final_verdict
                else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/depth/v4/papers")
async def depth_v4_papers(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """分页列出 DEPTH v4.2 审稿过的论文（**按论文聚合**，去重重复重审记录）。

    与 /api/depth/v4/list 的区别：
    - /list：每次审稿尝试返回一行（同一论文多次重审显示多条 → 列表页 11 条重复）
    - /papers：每篇论文仅返回一行聚合数据，适合「xx 深度审稿记录」概览表

    每行的语义：
    - paper_id / paper_title：论文基本信息
    - totalReviews：所有重审尝试总数（含失败）
    - completedCount / failedCount / pendingCount：按状态拆分的尝试次数
    - latestStatus / latestRecordId / lastAttemptedAt：最新一次审稿的状态/记录 ID/时间
    - latestNoveltyScore / latestVerdict：最新一次**已完成**审稿的创新分和裁决
      （若最新一次是 failed，则取最近一次 completed 的分数；都没有即 null）
    - lastCompletedAt：最近一次成功审稿完成时间
    - hasUnresolvedFailure：是否有失败但尚未成功的审稿（即「重审一下试试」信号）

    排序：先按 latestNoveltyScore desc（空值排在后），再按 lastAttemptedAt desc。
    实现：Python 侧聚合（SQLite 无窗口函数；数据规模 ~百级，性能完全够用）。
    """
    # 只取 v4.1 / v4.2 版本记录（向后兼容旧数据）
    all_records = (
        db.query(DepthReviewV4)
        .filter(DepthReviewV4.version.in_(["v4.1", "v4.2"]))
        .order_by(DepthReviewV4.created_at.desc())
        .all()
    )

    # Python 侧按 paper_id 聚合

    by_paper: dict[str, list] = defaultdict(list)
    for r in all_records:
        by_paper[r.paper_id].append(r)

    paper_ids = list(by_paper.keys())
    paper_map: dict[str, PaperORM] = {}
    if paper_ids:
        rows = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
        paper_map = {p.id: p for p in rows}

    aggregated: list[dict] = []
    for pid, reviews in by_paper.items():
        # reviews 已是按 created_at desc 排序
        latest = reviews[0]
        completed_reviews = [r for r in reviews if r.status == "completed"]
        latest_completed = completed_reviews[0] if completed_reviews else None

        novelty_score = None
        hotspot_score = None
        latest_verdict = None
        if latest_completed is not None:
            q2 = latest_completed.q2_result or {}
            fv = latest_completed.final_verdict or {}
            novelty_score = q2.get("novelty_score")
            hotspot_score = q2.get("hotspot_alignment_score")
            latest_verdict = fv.get("final_verdict") if isinstance(fv, dict) else None

        counts = {"completed": 0, "failed": 0, "pending": 0, "running": 0, "timed_out": 0}
        for r in reviews:
            if r.status in counts:
                counts[r.status] += 1

        paper = paper_map.get(pid)
        aggregated.append(
            {
                "paper_id": pid,
                "paper_title": paper.title if paper else "",
                "total_reviews": len(reviews),
                "completed_count": counts["completed"],
                "failed_count": counts["failed"],
                "pending_count": counts["pending"],
                "timed_out_count": counts["timed_out"],
                "latest_status": latest.status,
                "latest_record_id": latest.id,
                "latest_novelty_score": novelty_score,
                "latest_hotspot_score": hotspot_score,
                "latest_verdict": latest_verdict,
                "has_unresolved_failure": (counts["failed"] > 0 and counts["completed"] == 0),
                "last_attempted_at": latest.created_at.isoformat() if latest.created_at else None,
                "last_completed_at": (
                    latest_completed.completed_at.isoformat()
                    if (latest_completed and latest_completed.completed_at)
                    else None
                ),
            }
        )

    # 排序：有两段独立排序 —— 有最新创新分的论文按 novelty desc，
    # 没有的论文按 last_attempted_at desc 排在末尾。
    with_score = [x for x in aggregated if x["latest_novelty_score"] is not None]
    without_score = [x for x in aggregated if x["latest_novelty_score"] is None]
    with_score.sort(key=lambda x: (-(x["latest_novelty_score"] or 0), x["last_attempted_at"] or ""))
    without_score.sort(key=lambda x: x["last_attempted_at"] or "", reverse=True)
    aggregated = with_score + without_score

    total = len(aggregated)
    page_slice = aggregated[offset : offset + limit]
    return {"items": page_slice, "total": total, "limit": limit, "offset": offset}


@router.get("/api/depth/v4/scores")
async def depth_v4_scores_by_paper_ids(
    paper_ids: str = Query(..., description="逗号分隔的论文ID列表"),
    db: Session = Depends(get_db),
) -> dict:
    """按论文ID批量查询深度评审分数。

    返回 {paper_id: {verdict, novelty_score}} 映射，未评审的论文不在返回中。
    每篇论文只取最新一次已完成的评审记录。
    """
    ids = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
    if not ids:
        return {"scores": {}}

    # 一次性查询所有指定论文的 v4 评审记录
    records = (
        db.query(DepthReviewV4)
        .filter(
            DepthReviewV4.paper_id.in_(ids),
            DepthReviewV4.version.in_(["v4.1", "v4.2"]),
            DepthReviewV4.status == "completed",
        )
        .order_by(DepthReviewV4.created_at.desc())
        .all()
    )

    # 每篇论文只取最新一条已完成记录
    seen = set()
    scores: dict[str, dict] = {}
    for r in records:
        if r.paper_id in seen:
            continue
        seen.add(r.paper_id)
        q2 = r.q2_result or {}
        fv = r.final_verdict or {}
        scores[r.paper_id] = {
            "verdict": fv.get("final_verdict") if isinstance(fv, dict) else None,
            "novelty_score": q2.get("novelty_score"),
        }

    return {"scores": scores}


@router.delete("/api/depth/review/{review_id}")
async def delete_depth_review(review_id: str, db: Session = Depends(get_db)) -> dict:
    """Delete a single depth review record (v4.2 / reflection).

    Deletes one row from depth_reviews_v4 by its id.
    Returns 404 if not found.
    """
    record = db.query(DepthReviewV4).filter(DepthReviewV4.id == review_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")
    db.delete(record)
    db.commit()
    return {"success": True, "deleted_id": review_id}


@router.post("/api/depth/reviews/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_depth_reviews(
    req: BatchDeleteReviewRequest, db: Session = Depends(get_db)
) -> BatchDeleteResponse:
    """批量删除深度审稿记录（v4.2 / reflection 通用）。

    请求体：
    - review_ids: DepthReviewV4 主键列表（1~50 条）

    处理逻辑：
    1. 一次 SQL 查询定位所有目标行（含 kind 字段，便于前端区分 v4.2 vs reflection）
    2. 单次 commit 删除全部命中行（避免长事务）
    3. 不存在的 ID 归入 failed_ids，不抛异常
    4. 全部成功 → HTTP 200；部分失败 → HTTP 200（success=False 字段标识）

    限制：
    - 单次最多 50 条
    - 不允许删除 task 表的运行时锁（status='pending'/'running' 由前端锁定不允许勾选）
    """
    # 合并：review_ids（直接） + paper_ids（解析为每篇论文最新一条审稿记录）
    target_review_ids: set[str] = set(req.review_ids or [])
    if req.paper_ids:
        # 一次 SQL 查询定位 paper_ids 对应的所有记录，按 created_at 解析「最新一条」
        paper_records = (
            db.query(DepthReviewV4)
            .filter(DepthReviewV4.paper_id.in_(req.paper_ids))
            .order_by(DepthReviewV4.created_at.desc())
            .all()
        )
        latest_per_paper: dict[str, str] = {}
        for r in paper_records:
            # paper_records 已按 created_at desc 排序，遇到新 paper_id 即记录首个
            if r.paper_id not in latest_per_paper:
                latest_per_paper[r.paper_id] = r.id
        target_review_ids.update(latest_per_paper.values())

    if not target_review_ids:
        raise HTTPException(
            status_code=400, detail="未解析到任何审稿记录（paper_ids 可能无对应记录）"
        )
    if len(target_review_ids) > 50:
        raise HTTPException(status_code=400, detail="单次最多删除 50 条审稿记录")

    # 用 SQL IN 查询定位所有目标行（避免 N 次往返）
    records = db.query(DepthReviewV4).filter(DepthReviewV4.id.in_(list(target_review_ids))).all()
    found_ids = {r.id for r in records}
    missing_ids = [rid for rid in target_review_ids if rid not in found_ids]

    # 服务端拒绝删除进行中的审稿（active 锁），保留 dedup 自愈不变量。
    # 即使前端已 disable 勾选，直接调用 API 也应被服务端兜底拒绝。
    active_records = [r for r in records if r.status in ("pending", "running")]
    if active_records:
        active_ids = [r.id for r in active_records]
        raise HTTPException(
            status_code=409,
            detail=(
                f"以下 {len(active_ids)} 条审稿记录正在执行中，无法删除："
                f"{', '.join(active_ids[:5])}"
                f"{'...' if len(active_ids) > 5 else ''}。"
                "请等待审稿完成或失败后再试。"
            ),
        )

    deleted_count = 0
    failed_ids = list(missing_ids)  # 先把缺失 ID 加入失败列表
    for r in records:
        try:
            with db.begin_nested():
                db.delete(r)
                # 必须显式 flush 才能让 DELETE 真正被执行在 SAVEPOINT 之中
                # （SQLAlchemy 默认 lazy flush，会在最后 commit 时才发 SQL，
                # 届时异常会跳出 SAVEPOINT，整批回滚 —— 与初版的 bug 等价）
                db.flush()
            deleted_count += 1
        except Exception as e:
            logger.warning("batch delete depth review %s failed: %s", r.id, e)
            failed_ids.append(r.id)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"批量删除失败：{e}") from e

    return BatchDeleteResponse(
        success=len(failed_ids) == 0,
        deleted_count=deleted_count,
        failed_ids=failed_ids,
    )


@router.get("/api/depth/unified/list", response_model=UnifiedReviewListResponse)
async def unified_review_list(
    kind: str = Query("all", description="all | paper | report"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> UnifiedReviewListResponse:
    """统一列出 DEPTH 评审记录（按 kind 过滤）。

    - kind='paper'  → DEPTH v4.2 九节点 DAG 评审（v4.2 版本）
    - kind='report' → reflection 轻量 pipeline 评审
    - kind='all'    → 全部（按 created_at 倒序）
    """
    kind = (kind or "all").strip().lower()
    if kind not in ("all", "paper", "report"):
        raise HTTPException(
            status_code=400,
            detail=f"无效的 kind '{kind}'，可选: all | paper | report",
        )

    base_query = db.query(DepthReviewV4)
    if kind in ("paper", "report"):
        base_query = base_query.filter(DepthReviewV4.kind == kind)
    total = base_query.count()

    records = base_query.order_by(DepthReviewV4.created_at.desc()).offset(offset).limit(limit).all()

    paper_ids = [r.paper_id for r in records]
    paper_map: dict[str, PaperORM] = {}
    if paper_ids:
        rows = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
        paper_map = {p.id: p for p in rows}

    items: list[dict] = []

    for r in records:
        paper = paper_map.get(r.paper_id)
        # 通用字段
        item: dict = {
            "id": r.id,
            "paper_id": r.paper_id,
            "paper_title": paper.title if paper else "",
            "kind": r.kind,
            "status": r.status,
            "version": r.version,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        if r.kind == "report":
            rr = _safe_dict(r.reflection_result)
            scores = _safe_dict(rr.get("scores"))
            item.update(
                {
                    "primary_score": scores.get("average"),
                    "verdict": rr.get("verdict"),
                    "summary_short": (rr.get("summary", "") or "")[:200],
                }
            )
        else:  # paper
            q2 = _safe_dict(r.q2_result)
            fv = _safe_dict(r.final_verdict)
            item.update(
                {
                    "primary_score": q2.get("novelty_score"),
                    "verdict": fv.get("final_verdict"),
                    "summary_short": (q2.get("core_contribution", "") or "")[:200],
                }
            )
        items.append(item)

    return UnifiedReviewListResponse(
        items=items,
        total=total,
        kind=kind,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# 批量审完论文库（V4.2）—— 带 TaskManager 进度跟踪
# ---------------------------------------------------------------------------
@router.post("/api/depth/v4/review-selected")
async def depth_v4_review_selected(
    payload: DepthV4ReviewSelectedRequest, db: Session = Depends(get_db)
) -> dict:
    """提交指定论文列表进行 V4.2 深度审稿（通过 TaskManager 异步执行）。

    请求体：{"paper_ids": ["paper_id_1", "paper_id_2", ...]}
    批量提交：单次查询所有活跃锁 + 单次加锁 + 批量写入 TaskManager，避免逐篇串行。

    与 /review-batch 的区别：
    - review-batch：自动找出所有未审稿论文并批量审稿
    - review-selected：用户从主页手动选择论文提交审稿
    """
    paper_ids = payload.paper_ids

    # 1) 批量查询论文（一次 DB 查询）
    papers = db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()
    paper_map = {p.id: p for p in papers}

    # 2) 批量查询已有活跃审稿锁（一次 DB 查询替代 N 次）
    active_reviews = (
        db.query(DepthReviewV4)
        .filter(
            DepthReviewV4.paper_id.in_(paper_ids),
            DepthReviewV4.status.in_(["pending", "running"]),
        )
        .all()
    )
    active_review_map: dict[str, DepthReviewV4] = {r.paper_id: r for r in active_reviews}

    # 3) 获取 worker（一次）
    worker = get_worker("depth_review")
    if worker is None:
        raise HTTPException(status_code=500, detail="任务系统未初始化")

    task_ids: list[str] = []
    skipped: list[dict] = []
    submitted = 0

    # 4) 单次加锁，批量处理所有论文
    with _depth_review_lock:
        for pid in paper_ids:
            paper = paper_map.get(pid)
            if paper is None:
                skipped.append({"paper_id": pid, "reason": "论文不存在"})
                continue
            if not paper.full_text:
                skipped.append({"paper_id": pid, "reason": "论文尚无全文，无法审稿"})
                continue

            # 去重检查（使用批量查询结果）
            existing = active_review_map.get(pid)
            if existing:
                if _has_active_depth_review_task(pid):
                    skipped.append({"paper_id": pid, "reason": "已有进行中的审稿任务"})
                    continue
                # 自愈：清理幽灵锁（立即提交，避免被后续异常中断导致自愈丢失）
                now = datetime.now()
                existing.status = "failed"
                existing.completed_at = now
                existing.error_message = (
                    f"[auto-recovered @ {now.isoformat(timespec='seconds')}] "
                    "深度任务锁已失效：底层 Task 不在 active 状态，自动放行。"
                )
                db.commit()
                logger.info(
                    "DEPTH v4.2 dedup 自愈（批量）：清理幽灵 DepthReviewV4[%s] for paper=%s",
                    existing.id,
                    pid,
                )

            try:
                task_id = task_manager.TaskManager.submit(
                    "depth_review",
                    # B2: 异步审稿提交原未转发 compute_mode，worker 恒退化 "deep"，
                    # 用户对 speed/fast 的选择在异步批量审稿中被完全忽略。
                    params={"paperId": pid, "computeMode": payload.compute_mode},
                    worker_fn=worker,
                )
                task_ids.append(task_id)
                submitted += 1
            except Exception as e:
                skipped.append({"paper_id": pid, "reason": f"提交失败: {e}"})

        # 批量提交自愈修改
        db.commit()

    return {
        "task_ids": task_ids,
        "submitted": submitted,
        "skipped": skipped,
        "total": len(paper_ids),
        "message": f"已提交 {submitted} 篇审稿任务"
        + (f"，{len(skipped)} 篇跳过" if skipped else ""),
    }


@router.post("/api/depth/v4/review-batch")
async def depth_v4_review_all_unreviewed(db: Session = Depends(get_db)) -> dict:
    """批量审稿：找出所有有全文但未完成 V4.2 审稿的论文，通过 TaskManager 异步执行。

    筛选条件：
    - papers.full_text 非空（有全文才能审稿）
    - 没有 status IN ('pending','running','completed') 的 depth_reviews_v4 记录

    使用 TaskManager.submit() 提交后台任务，返回 task_id。
    客户端通过 GET /api/tasks/{task_id} 轮询进度，
    或 GET /api/tasks/{task_id}/stream (SSE) 实时接收进度事件。

    进度语义：
    - 0~100: 已审稿数量 / 总数量 * 100
    - progressMessage: "审稿中: 3/15 (1706.03762)"
    - result: {"total": 15, "succeeded": 12, "failed": 3, "failedPapers": [...]}
    """
    # 子查询：已有 active v4.2 审核的 paper_id
    reviewed_subq = (
        db.query(DepthReviewV4.paper_id)
        .filter(DepthReviewV4.status.in_(["pending", "running", "completed"]))
        .subquery()
    )
    unreviewed = (
        db.query(PaperORM)
        .filter(
            PaperORM.full_text.isnot(None),
            PaperORM.full_text != "",
            PaperORM.id.notin_(reviewed_subq),
        )
        .all()
    )

    if not unreviewed:
        return {
            "task_id": None,
            "message": "所有论文已完成 V4.2 审稿",
            "total": 0,
        }

    paper_ids = [p.id for p in unreviewed]
    total = len(paper_ids)

    # ── Worker：逐篇审稿 + 进度更新 ──
    def _batch_review_worker(task_id: str, params: dict):
        import time

        from ..database import SessionLocal
        from ..depth_tasks import run_depth_review_sync

        TM = task_manager.TaskManager

        paper_ids_ = params.get("paperIds", [])
        if not paper_ids_:
            TM.fail(task_id, "没有待审论文")
            return

        total_ = len(paper_ids_)
        succeeded = 0
        failed = 0
        failed_papers: list[dict] = []

        TM.update_progress(task_id, 0, f"审稿中: 0/{total_}")

        for i, pid in enumerate(paper_ids_):
            db2 = SessionLocal()
            try:
                run_depth_review_sync(pid)
                succeeded += 1
            except Exception as e:
                failed += 1
                failed_papers.append({"paper_id": pid, "error": str(e)[:200]})
                logger.warning("批量审稿: 论文 %s 审稿失败: %s", pid, e)
            finally:
                db2.close()

            # P0-C：批量端点触发 figure 抽取（M0 矢量渲染 + 真图 QF 路径）。
            # 门控 PAPERFORGE_BATCH_TRIGGER_FIGURES=1 才启用；fire-and-forget +
            # 非致命：不阻塞审稿，PDF 缺失会自动按需下载。默认关闭，避免 456 篇
            # 批量把 VLM/OCR 与 DEPTH 的 Qwen 在 VRAM 调度下互相打满。
            if os.environ.get("PAPERFORGE_BATCH_TRIGGER_FIGURES") == "1":
                try:
                    from ..workers.figures import submit_extract_figures_task

                    submit_extract_figures_task(pid)
                except Exception as e:  # noqa: BLE001
                    logger.warning("批量审稿：提交 figure 抽取任务失败（非致命）: %s", e)

            progress = int((i + 1) / total_ * 100)
            TM.update_progress(
                task_id,
                progress,
                f"审稿中: {i + 1}/{total_} (成功 {succeeded}, 失败 {failed})",
            )

            # 避免 LLM 限流（最后一条无需等待）
            if i < total_ - 1:
                time.sleep(1)

        TM.complete(
            task_id,
            {
                "total": total_,
                "succeeded": succeeded,
                "failed": failed,
                "failedPapers": failed_papers,
            },
        )

    # 提交到 TaskManager
    task_id = task_manager.TaskManager.submit(
        "v4_batch_review",
        params={"paperIds": paper_ids},
        worker_fn=_batch_review_worker,
    )

    logger.info(
        "批量 V4.2 审稿已提交 TaskManager: task_id=%s, %d 篇待审",
        task_id,
        total,
    )
    return {
        "task_id": task_id,
        "message": f"已提交 {total} 篇论文的 V4.2 审稿任务，通过 GET /api/tasks/{task_id} 跟踪进度",
        "total": total,
    }
