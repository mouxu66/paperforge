"""Figure understanding background worker.

Triggers scripts/pipeline_figure_understanding.py for a paper when the
DEPTH pipeline detects that PaperFigure data is missing. Runs in a
TaskManager background task so the current review is not blocked.
"""

from __future__ import annotations

import logging
import time

from scripts.pipeline_figure_understanding import run_pipeline

from ..crud.figures import get_figures_by_paper
from ..database import SessionLocal
from ..models import Paper
from ..tasks import TaskManager, get_cancel_event
from ..utils.paths import _get_uploads_dir
from ..vram_scheduler import VRAMState, get_vram_scheduler

logger = logging.getLogger(__name__)


def _active_task_for_paper(paper_id: str) -> dict | None:
    """Return an pending/running figure_understanding task for the same paper, if any."""
    for task in TaskManager.list(task_type="figure_understanding"):
        if task.get("params", {}).get("paper_id") == paper_id and task.get("status") in (
            "pending",
            "running",
        ):
            return task
    return None


def submit_figure_understanding_task(paper_id: str) -> str | None:
    """Submit a background task to run figure understanding for a paper.

    If there is already a pending or running figure_understanding task for the
    same paper_id, return that task's id instead of creating a duplicate.

    Returns the task_id, or None if TaskManager is unavailable.
    """
    try:
        existing = _active_task_for_paper(paper_id)
        if existing:
            task_id = existing["id"]
            logger.info(
                "[figure_understanding] dedup: reuse active task=%s paper=%s",
                task_id,
                paper_id,
            )
            return task_id

        task_id = TaskManager.submit(
            "figure_understanding",
            params={"paper_id": paper_id},
            worker_fn=_figure_understanding_worker,
        )
        logger.info("[figure_understanding] scheduled task=%s paper=%s", task_id, paper_id)
        return task_id
    except Exception as e:  # noqa: BLE001 - worker submission failure must not crash caller
        logger.warning("[figure_understanding] failed to schedule paper=%s: %s", paper_id, e)
        return None


def _wait_for_vram_available(task_id: str, timeout: int = 300, poll_interval: int = 5) -> bool:
    """Light-weight queueing: wait until VRAM is not busy with vision/switching.

    Returns True once idle/text-active, False if timed out or cancelled.
    """
    scheduler = get_vram_scheduler()
    cancel_event = get_cancel_event(task_id)
    busy_states = {VRAMState.VISION_ACTIVE, VRAMState.SWITCHING_TO_TEXT}
    elapsed = 0
    while scheduler.state() in busy_states:
        if cancel_event.is_set():
            return False
        if elapsed >= timeout:
            return False
        TaskManager.update_progress(
            task_id,
            0,
            f"VRAM 被 vision/切换占用，排队等待中 ({elapsed}s/{timeout}s)",
        )
        time.sleep(poll_interval)
        elapsed += poll_interval
    return True


def _figure_understanding_worker(task_id: str, params: dict) -> None:
    """Background worker: run the figure-understanding pipeline.

    Expects params={"paper_id": str}. The pipeline coordinates VRAM itself
    via vram_scheduler, so this worker only needs to supply the PDF bytes
    and a database session.
    """
    paper_id = params.get("paper_id")
    if not paper_id:
        TaskManager.fail(task_id, "missing paper_id")
        return

    # Deduplication / self-guard: if another active figure_understanding task
    # for the same paper exists with a different task_id, exit early.
    for task in TaskManager.list(task_type="figure_understanding", status="running"):
        if task.get("params", {}).get("paper_id") == paper_id and task.get("id") != task_id:
            TaskManager.complete(task_id, {"paper_id": paper_id, "dedup": True})
            return

    db = SessionLocal()
    try:
        TaskManager.update_progress(task_id, 0, "开始图表理解 pipeline")

        # Resolve PDF path
        pdf_path = _get_uploads_dir() / f"{paper_id}.pdf"
        if not pdf_path.exists():
            TaskManager.fail(task_id, f"PDF 文件不存在: {pdf_path}")
            return

        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if paper is None:
            TaskManager.fail(task_id, f"论文不存在: {paper_id}")
            return

        # Light-weight rate limiting / queuing against busy VRAM.
        if not _wait_for_vram_available(task_id):
            TaskManager.fail(task_id, "等待 VRAM 空闲超时")
            return

        pdf_bytes = pdf_path.read_bytes()

        TaskManager.update_progress(task_id, 10, "执行 vision + VLM 图表理解")
        # vram_bracket=True：run_pipeline 内部自理 vision 显存括号
        # （request_vision 串行化 vision + 同卡 exclusive 下让出 8080 → vision_finished 拉回 Qwen），
        # 避免 vision 与 text-Qwen 在 8GB 显存上并发抢显存 OOM。
        rc = run_pipeline(
            paper_id=paper_id,
            title=paper.title or "",
            pdf_bytes=pdf_bytes,
            db=db,
            vram_bracket=True,
        )

        if rc == 0:
            count = len(get_figures_by_paper(db, paper_id))
            TaskManager.complete(task_id, {"paper_id": paper_id, "figures_count": count})
        else:
            TaskManager.fail(task_id, "figure understanding pipeline 返回失败")
    except Exception as e:  # noqa: BLE001 - worker body must isolate failures
        logger.exception("[figure_understanding] worker failed paper=%s", paper_id)
        try:
            db.rollback()
        except Exception:
            pass
        TaskManager.fail(task_id, str(e))
    finally:
        db.close()
