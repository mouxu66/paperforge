"""Admin 端 figure 理解批量触发路由。

M0 消费层「独立小批量 pass」的显式入口：与 bulk DEPTH 解耦，由调用方在
PAPERFORGE_DISABLE_FIGURE_TRIGGER=1 的 bulk DEPTH 之外，主动、小规模地触发
figure 理解（Qwen3-VL 视觉摘要 + 入库），避免 OCR 与 Qwen 在 8GB 显存上
互斥导致 segfault。

所有端点挂 /api/admin 前缀，并依赖 require_admin_auth（仅显式开发态 + 鉴权通过）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import require_admin_auth
from ..crud.figures import get_figures_by_paper
from ..database import SessionLocal
from ..models import Paper
from ..workers.figure_understanding import submit_figure_understanding_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_auth)])


class FigureUnderstandBatchRequest(BaseModel):
    paper_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="要触发 figure 理解的论文 ID 列表（独立小批量，≤50 篇）。",
    )
    skip_existing: bool = Field(
        default=True,
        description="True 时跳过已有 figure 的论文（幂等）；False 强制重跑全部。",
    )


class FigureUnderstandBatchResponse(BaseModel):
    dispatched: list[str]
    skipped: list[str]
    failed: list[str]


@router.post("/figures/understand-batch", response_model=FigureUnderstandBatchResponse)
def figure_understand_batch(req: FigureUnderstandBatchRequest) -> FigureUnderstandBatchResponse:
    """独立小批量触发 figure 理解（Qwen3-VL 视觉摘要 + 入库）。

    与 bulk DEPTH 解耦：由调用方显式触发，不内联进 DEPTH 节点。建议每批 ≤50 篇，
    配合 PAPERFORGE_DISABLE_FIGURE_TRIGGER=1 的 bulk DEPTH 使用。每篇论文后台
    worker 会自理 OCR 显存括号（释放 Qwen → OCR/VLM → 拉回 Qwen）。
    """
    db = SessionLocal()
    dispatched: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    try:
        for pid in req.paper_ids:
            paper = db.query(Paper).filter(Paper.id == pid).first()
            if paper is None:
                logger.warning("[figure-understand-batch] 论文不存在: %s", pid)
                failed.append(pid)
                continue
            if req.skip_existing and get_figures_by_paper(db, pid):
                skipped.append(pid)
                continue
            task_id = submit_figure_understanding_task(pid)
            if task_id is None:
                # TaskManager 不可用 / 提交失败
                failed.append(pid)
            else:
                # 若已有活跃任务，submit 会复用并返回其 task_id（不算失败）
                dispatched.append(pid)
        return FigureUnderstandBatchResponse(dispatched=dispatched, skipped=skipped, failed=failed)
    finally:
        db.close()
