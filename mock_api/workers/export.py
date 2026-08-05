"""Writing project export background worker.

负责在后台线程中执行写作项目导出，支持：
- 5 分钟 TTL 缓存（项目未修改时直接返回缓存）
- 分块进度上报（current_chapter / total_chapters）
- 结果持久化到 TaskManager（SQLite）
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from ..author_utils import parse_authors

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P2-2: 导出结果缓存（5 分钟 TTL + updated_at 校验）
# ---------------------------------------------------------------------------
@dataclass
class _CacheEntry:
    """单个项目的导出缓存条目。"""

    content: str
    filename: str
    references: list[dict]
    project_updated_at: str  # 项目 updated_at 的 ISO 字符串，内容变更时失效
    cached_at: float  # 缓存创建时间戳


_export_cache: dict[int, _CacheEntry] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 分钟


def _get_cached_export(project_id: int, project_updated_at: str) -> _CacheEntry | None:
    """查询缓存：存在且未过期且 updated_at 未变时返回条目，否则返回 None。"""
    with _cache_lock:
        entry = _export_cache.get(project_id)
        if not entry:
            return None
        now = time.time()
        # TTL 过期 → 清理并返回 None
        if now - entry.cached_at > CACHE_TTL:
            _export_cache.pop(project_id, None)
            return None
        # 项目内容变更 → 失效
        if entry.project_updated_at != project_updated_at:
            return None
        return entry


def _set_cached_export(
    project_id: int,
    project_updated_at: str,
    content: str,
    filename: str,
    references: list[dict],
) -> None:
    """写入导出结果缓存。"""
    with _cache_lock:
        _export_cache[project_id] = _CacheEntry(
            content=content,
            filename=filename,
            references=references,
            project_updated_at=project_updated_at,
            cached_at=time.time(),
        )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def export_worker(task_id: str, params: dict) -> None:
    """导出写作项目为 Markdown / Word 的 worker。

    流程：
    1. 检查缓存（5 分钟 TTL + updated_at 校验）
    2. 缓存未命中则调用 crud.export_project_markdown 导出
    3. 通过 TaskManager 上报进度/完成/失败
    """
    from .. import crud
    from ..database import SessionLocal
    from ..tasks import TaskManager

    project_id = params.get("projectId")
    if not project_id:
        TaskManager.fail(task_id, "缺少 projectId 参数")
        return

    db = SessionLocal()
    try:
        TaskManager.update_progress(task_id, 5, "开始导出")

        project_updated_at = crud.get_project_updated_at(db, project_id)
        if project_updated_at is None:
            TaskManager.fail(task_id, "项目不存在或已被删除")
            return

        # 缓存命中 → 直接返回缓存结果
        cached = _get_cached_export(project_id, project_updated_at)
        if cached:
            TaskManager.complete(
                task_id,
                {
                    "content": cached.content,
                    "filename": cached.filename,
                    "references": cached.references,
                    "current_chapter": 0,
                    "total_chapters": 0,
                },
            )
            return

        # 分块导出，progress_cb 每批回调更新进度
        def progress_cb(current: int, total: int) -> None:
            progress = 5 + int(85 * current / total) if total > 0 else 5
            TaskManager.update_progress(
                task_id,
                progress,
                f"导出中: {current}/{total} 章节",
            )

        result = crud.export_project_markdown(
            db,
            project_id,
            progress_cb=progress_cb,
        )
        if result is None:
            TaskManager.fail(task_id, "项目不存在或已被删除")
            return

        content, filename, references = result

        # 将 Pydantic 模型转为 dict，便于跨线程读取与 JSON 序列化
        ref_dicts = [
            {
                "id": r.id,
                "title": r.title,
                "authors": parse_authors(r.authors),
                "year": r.year,
            }
            for r in references
        ]

        TaskManager.complete(
            task_id,
            {
                "content": content,
                "filename": filename,
                "references": ref_dicts,
            },
        )

        # 写入缓存，下次同项目导出可直接命中
        _set_cached_export(project_id, project_updated_at, content, filename, ref_dicts)
    except Exception as e:  # noqa: BLE001 - 后台线程必须吞下所有异常
        TaskManager.fail(task_id, f"导出失败：{e}")
    finally:
        db.close()
