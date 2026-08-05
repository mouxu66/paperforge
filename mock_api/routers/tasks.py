"""通用任务提交 / 查询 / 流式进度（共享基础设施）。

PR8 抽取：从 main.py 搬迁 tasks 路由（依赖 task_manager + get_worker）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import tasks as task_manager
from ..schemas import TaskCreate
from ..workers import get_worker

router = APIRouter(tags=["tasks"])


@router.post("/api/tasks")
def create_task(payload: TaskCreate) -> dict:
    """创建通用任务。"""
    task_type = payload.type
    if not task_type:
        raise HTTPException(status_code=400, detail="task type 不能为空")
    worker = get_worker(task_type)
    if worker is None:
        raise HTTPException(status_code=400, detail=f"未知任务类型: {task_type}")
    task_id = task_manager.TaskManager.submit(
        task_type,
        params=payload.params,
        worker_fn=worker,
    )
    # taskId 是通用任务 API 的规范字段；保留 task_id 一轮，兼容旧客户端。
    return {"taskId": task_id, "task_id": task_id, "status": "pending"}


@router.get("/api/tasks")
def list_tasks(
    type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50),
) -> dict:
    """列出所有任务（可按类型和状态过滤）。"""
    tasks = task_manager.TaskManager.list(task_type=type, status=status, limit=limit)
    return {"items": tasks, "total": len(tasks)}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    """查询任务状态。"""
    task = task_manager.TaskManager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return task


@router.get("/api/tasks/{task_id}/stream")
def stream_task(task_id: str) -> StreamingResponse:
    """SSE 流式订阅任务进度。"""
    task = task_manager.TaskManager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return StreamingResponse(
        task_manager.TaskManager.stream_progress(task_id),
        media_type="text/event-stream",
    )
