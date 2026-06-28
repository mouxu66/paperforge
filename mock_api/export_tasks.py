"""异步导出任务管理（内存任务表 + 后台线程）。

为子任务 2「大文档导出进度提示」提供后端实现：
- POST /api/writing/projects/{id}/export  → 调用 start_export_task 返回 task_id
- GET  /api/writing/export/{task_id}/progress  → 调用 get_export_task 查询进度

P2-2 增强：
- 分块处理：章节按 20 个一批渲染，每批完成后更新 current_chapter / total_chapters
- 缓存机制：同一项目 5 分钟内未修改时直接返回缓存结果，跳过重复导出

设计约束（按用户要求保持简单）：
- 进度存储在内存 dict 中（不引入 Redis 等外部依赖）
- 后台使用 threading.Thread(daemon=True) 执行，主进程退出时自动结束
- SQLAlchemy Session 不是线程安全的，因此在后台线程内独立创建 SessionLocal()
- 任务列表上限 MAX_TASKS=100，超过后按创建时间 FIFO 淘汰最旧任务
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .database import SessionLocal


# ---------------------------------------------------------------------------
# 任务数据结构
# ---------------------------------------------------------------------------
@dataclass
class ExportTask:
    """单个导出任务的状态快照。

    字段说明：
    - task_id：UUID，客户端用于轮询
    - project_id：被导出的写作项目 ID
    - progress：0-100 的进度百分比
    - status：pending | running | done | error
    - content / filename / references：status=done 时填充（导出结果）
    - error：status=error 时填充错误信息
    - created_at：用于 FIFO 淘汰
    - current_chapter / total_chapters：P2-2 分块进度（正在处理第 X/Y 章节）
    """
    task_id: str
    project_id: int
    progress: int = 0
    status: str = "pending"  # pending | running | done | error
    content: str = ""
    filename: str = ""
    references: list[dict] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    # P2-2: 分块进度
    current_chapter: int = 0
    total_chapters: int = 0


# ---------------------------------------------------------------------------
# 全局任务表（线程安全）
# ---------------------------------------------------------------------------
_tasks: dict[str, ExportTask] = {}
_lock = threading.Lock()
MAX_TASKS = 100


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


def _get_cached_export(
    project_id: int, project_updated_at: str
) -> Optional[_CacheEntry]:
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
# 公共 API
# ---------------------------------------------------------------------------
def start_export_task(project_id: int) -> str:
    """启动一个异步导出任务，返回 task_id。

    - 创建 ExportTask 记录（status=pending, progress=0）
    - 启动 daemon 线程在后台执行 _run_export
    - 调用方应立即返回 202 + task_id 给前端
    """
    task_id = uuid.uuid4().hex
    task = ExportTask(task_id=task_id, project_id=project_id)
    with _lock:
        _cleanup_old_tasks_locked()
        _tasks[task_id] = task

    # daemon=True：主进程退出时线程自动终止，避免阻塞 uvicorn 关闭
    t = threading.Thread(
        target=_run_export,
        args=(task_id, project_id),
        daemon=True,
        name=f"export-{task_id[:8]}",
    )
    t.start()
    return task_id


def get_export_task(task_id: str) -> Optional[ExportTask]:
    """查询任务进度。任务不存在（或已被淘汰）时返回 None。"""
    with _lock:
        return _tasks.get(task_id)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------
def _run_export(task_id: str, project_id: int) -> None:
    """后台线程：执行导出并更新进度。

    P2-2 改进：
    - 缓存命中时直接返回，进度跳至 100
    - 章节按 20 个一批渲染，每批完成后更新 current_chapter / total_chapters
    - 进度区间：5%（开始）→ 5-90%（分批渲染）→ 90-100%（引用解析 + 完成）

    任何异常均捕获并写入 task.error，status 置为 error
    """
    # 延迟导入，避免循环依赖（crud 会反向引用 export_tasks 的常量）
    from . import crud

    db = SessionLocal()
    try:
        _set_progress(task_id, 5, status="running")

        # P2-2: 获取项目 updated_at 用于缓存校验
        project_updated_at = crud.get_project_updated_at(db, project_id)
        if project_updated_at is None:
            _set_error(task_id, "项目不存在或已被删除")
            return

        # P2-2: 缓存命中 → 直接返回缓存结果
        cached = _get_cached_export(project_id, project_updated_at)
        if cached:
            _set_progress(task_id, 100)
            _set_done(
                task_id,
                content=cached.content,
                filename=cached.filename,
                references=cached.references,
            )
            return

        # P2-2: 分块导出，progress_cb 每批回调更新进度
        def progress_cb(current: int, total: int) -> None:
            _set_chapter_progress(task_id, current, total)

        result = crud.export_project_markdown(
            db, project_id, progress_cb=progress_cb
        )

        if result is None:
            _set_error(task_id, "项目不存在或已被删除")
            return

        content, filename, references = result
        _set_progress(task_id, 90)

        # 将 Pydantic 模型转为 dict，便于跨线程读取与 JSON 序列化
        ref_dicts = [
            {
                "id": r.id,
                "title": r.title,
                "authors": list(r.authors),
                "year": r.year,
            }
            for r in references
        ]

        _set_done(task_id, content=content, filename=filename, references=ref_dicts)

        # P2-2: 写入缓存，下次同项目导出可直接命中
        _set_cached_export(
            project_id, project_updated_at, content, filename, ref_dicts
        )
    except Exception as e:  # noqa: BLE001 - 后台线程必须吞下所有异常
        _set_error(task_id, f"导出失败：{e}")
    finally:
        db.close()


def _set_progress(task_id: str, progress: int, status: str = "running") -> None:
    """更新进度百分比（线程安全）。"""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.progress = max(task.progress, progress)
            if status:
                task.status = status


def _set_chapter_progress(task_id: str, current: int, total: int) -> None:
    """P2-2: 更新分块进度 —— current_chapter / total_chapters + 映射到 5-90% 区间。"""
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        task.current_chapter = current
        task.total_chapters = total
        if total > 0:
            # 章节渲染占 5%-90% 的进度区间
            task.progress = max(task.progress, 5 + int(85 * current / total))


def _set_done(
    task_id: str,
    content: str,
    filename: str,
    references: list[dict],
) -> None:
    """标记任务完成并写入最终结果。"""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.progress = 100
            task.status = "done"
            task.content = content
            task.filename = filename
            task.references = references


def _set_error(task_id: str, error: str) -> None:
    """标记任务失败。"""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.status = "error"
            task.error = error


def _cleanup_old_tasks_locked() -> None:
    """FIFO 淘汰：当任务数超过 MAX_TASKS 时删除最旧的条目。

    必须在持有 _lock 的情况下调用（后缀 _locked 表示已加锁）。
    """
    if len(_tasks) <= MAX_TASKS:
        return
    # 按 created_at 升序，删除最旧的 (len - MAX_TASKS) 个
    sorted_ids = sorted(_tasks.keys(), key=lambda tid: _tasks[tid].created_at)
    overflow = len(_tasks) - MAX_TASKS
    for tid in sorted_ids[:overflow]:
        _tasks.pop(tid, None)
