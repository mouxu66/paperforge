from __future__ import annotations

from collections.abc import Callable

from .batch_delete import batch_delete_worker
from .export import export_worker
from .reviews import (
    depth_batch_worker,
    depth_review_worker,
    reflection_review_worker,
    v4_batch_review_worker,
)

"""Background task workers registry.

所有耗时后台任务 worker 统一注册在此，供路由层通过 `get_worker`
获取对应任务类型的 worker 函数。
"""


def get_worker(task_type: str) -> Callable[[str, dict], None] | None:
    """根据任务类型返回对应的 worker 函数。

    Args:
        task_type: 任务类型标识。

    Returns:
        对应的 worker 函数；未找到时返回 None。
    """
    return {
        "depth_review": depth_review_worker,
        "reflection_review": reflection_review_worker,
        "depth_batch": depth_batch_worker,
        "v4_batch_review": v4_batch_review_worker,
        "export": export_worker,
        "batch_delete": batch_delete_worker,
    }.get(task_type)
