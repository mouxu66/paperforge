"""Batch delete papers background worker."""

from __future__ import annotations


def batch_delete_worker(task_id: str, params: dict) -> None:
    """批量删除论文的 worker。"""
    from .. import crud
    from ..database import SessionLocal
    from ..tasks import TaskManager

    paper_ids = params.get("paperIds", [])
    if not paper_ids:
        TaskManager.fail(task_id, "缺少 paperIds 参数")
        return

    db2 = SessionLocal()
    try:
        result = crud.batch_delete_papers(db2, paper_ids)
        TaskManager.complete(
            task_id,
            {"deleted": result.deleted_count, "failed": result.failed_ids},
        )
    except Exception as e:  # noqa: BLE001 - worker loop - 单条失败应隔离，不应中断批处理
        TaskManager.fail(task_id, str(e))
    finally:
        db2.close()
