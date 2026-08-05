"""通用异步任务管理器 —— 基于 SQLite 持久化 + asyncio.Queue SSE 推送。

设计原则：
- 所有耗时后台任务（DEPTH审稿、PDF导入、导出、批量删除等）统一走此框架
- 任务状态持久化到 SQLite tasks 表，服务重启后可查询历史
- SSE 实时推送进度给前端，避免轮询
- 后台线程池执行任务，不阻塞 FastAPI 事件循环
- FIFO 清理：保留最近 500 条已完成/失败任务
- 每任务独立超时保护：worker 超时自动标记为 timed_out（后续仍可写入完整结果）
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

from sqlalchemy import text

from .database import SessionLocal
from .models import Task as TaskORM
from .settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置（从集中式 settings 读取）
# ---------------------------------------------------------------------------
MAX_TASK_HISTORY = 500  # 保留最近 N 条已完成/失败任务
MAX_SSE_QUEUE_SIZE = 200  # 单任务 SSE 事件队列上限

# 线程池大小：默认 min(4, cpu_count) 避免 CPU 过度竞争
WORKER_THREADS = get_settings().effective_task_workers

#    单任务最大执行时长（秒），超时自动标记为 timed_out
TASK_TIMEOUT_SECONDS = get_settings().task_timeout

# ---------------------------------------------------------------------------
# 全局线程池 + SSE 订阅者注册表（每订阅者独立队列，广播模式）
# ---------------------------------------------------------------------------
# 🛡️ P1-4 修复：原版单队列 _sse_queues[task_id] 被多订阅者抢读，且队列满时
# 静默丢事件（含终结事件 → 连接/协程泄漏）。改为每订阅者独立队列 + 广播，
# 并保证终结事件（completed/failed）必达（即使队列满也强制 put）。
_executor = ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix="task-")


@dataclass
class _SubscriberEntry:
    """单个 SSE 订阅者注册项。"""

    queue: asyncio.Queue[dict]
    created_at: float = field(default_factory=time.time)
    # 终结事件是否已投递到该订阅者（幂等保证，避免重复 put）
    terminated: bool = False


# task_id → list[订阅者]；每订阅者独立队列，互不抢读
_subscribers: dict[str, list[_SubscriberEntry]] = {}
_subscribers_lock = threading.Lock()

# task_id → 协作式取消事件（worker 超时后设置，worker 应在长循环中检查）
_cancel_events: dict[str, threading.Event] = {}
_cancel_events_lock = threading.Lock()


def get_cancel_event(task_id: str) -> threading.Event:
    """获取某任务的协作式取消事件（worker 线程应周期性检查 is_set()）。"""
    with _cancel_events_lock:
        if task_id not in _cancel_events:
            _cancel_events[task_id] = threading.Event()
        return _cancel_events[task_id]


def _set_cancel(task_id: str) -> None:
    """设置某任务的取消事件（超时后调用，worker 应尽快退出）。"""
    get_cancel_event(task_id).set()


def _register_subscriber(task_id: str) -> _SubscriberEntry:
    """注册一个新的 SSE 订阅者，返回其独立队列。"""
    entry = _SubscriberEntry(queue=asyncio.Queue(maxsize=MAX_SSE_QUEUE_SIZE))
    with _subscribers_lock:
        _subscribers.setdefault(task_id, []).append(entry)
    return entry


def _unregister_subscriber(task_id: str, entry: _SubscriberEntry) -> None:
    """订阅者断连时移除自己（防内存泄漏）。"""
    with _subscribers_lock:
        subs = _subscribers.get(task_id)
        if subs and entry in subs:
            subs.remove(entry)
            if not subs:
                _subscribers.pop(task_id, None)


def _broadcast_event(task_id: str, event: dict, *, is_terminal: bool = False) -> None:
    """向某任务的所有订阅者广播事件。

    Args:
        is_terminal: True 表示这是终结事件（completed/failed），
                     即使队列满也强制 put（block=True 短暂等待），保证必达。
    """
    with _subscribers_lock:
        subs = list(_subscribers.get(task_id, []))
    for sub in subs:
        if is_terminal and sub.terminated:
            continue  # 幂等：已投递过终结事件
        try:
            if is_terminal:
                # 终结事件必达：block 直到队列有空间（最多 5s）
                loop = _main_loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(partial(_put_terminal, sub.queue, event))
                else:
                    _put_terminal(sub.queue, event)
                sub.terminated = True
            else:
                loop = _main_loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(partial(_safe_put_nowait, sub.queue, event))
                else:
                    _safe_put_nowait(sub.queue, event)
        except Exception:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
            pass  # 单订阅者失败不影响其他


def _put_terminal(queue: asyncio.Queue[dict], event: dict) -> None:
    """终结事件投递：队列满时先丢弃最旧的普通事件再 put，保证终结事件必达。"""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()  # 丢弃最旧
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # 极端情况仍丢弃，但已尽力


def _safe_put_nowait(queue: asyncio.Queue[dict], event: dict) -> None:
    """普通事件投递：队列满时静默丢弃（不阻塞 worker）。"""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


# Typed helpers to help mypy infer lambda types
def _broadcast_terminal(task_id: str, event: dict) -> None:
    _broadcast_event(task_id, event, is_terminal=True)


def _broadcast_normal(task_id: str, event: dict) -> None:
    _broadcast_event(task_id, event, is_terminal=False)


def _cleanup_subscribers(task_id: str) -> None:
    """任务完成后 60s 清理订阅者注册（给 SSE 客户端缓冲时间）。"""

    def _clean():
        time.sleep(60)
        with _subscribers_lock:
            _subscribers.pop(task_id, None)
        with _cancel_events_lock:
            _cancel_events.pop(task_id, None)

    threading.Thread(target=_clean, daemon=True).start()


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


class TaskManager:
    """统一异步任务管理器（所有方法为类方法，进程级单例）。"""

    # ── 提交任务 ────────────────────────────────────────────────
    @staticmethod
    def submit(
        task_type: str,
        params: dict | None = None,
        worker_fn: Callable[[str, dict], None] | None = None,
    ) -> str:
        """提交一个后台任务，立即返回 task_id。

        Args:
            task_type: 任务类型标识（depth_review / export / batch_delete / ...）
            params: 任务参数（JSON 可序列化）
            worker_fn: 后台执行函数，签名为 fn(task_id, params)。
                       在 ThreadPoolExecutor 中运行。
                       应在完成后调用 TaskManager.complete() 或 TaskManager.fail()。

        Returns:
            task_id (UUID)。
        """

        task_id = str(uuid.uuid4())
        params = params or {}

        # 1. 写入 SQLite
        db = SessionLocal()
        try:
            task = TaskORM(
                id=task_id,
                type=task_type,
                status="pending",
                progress=0,
                params=params,
            )
            db.add(task)
            db.commit()

            # 2. 清理超量历史任务（延迟导入避免循环依赖）
            _prune_history(db)

            logger.info("TaskManager: 提交任务 type=%s, id=%s", task_type, task_id)
        finally:
            db.close()

        # 2. 若提供了 worker_fn，在线程池中执行（带超时保护）
        if worker_fn:
            future = _executor.submit(_run_worker, task_id, task_type, params, worker_fn)
            # 后台监控超时：若 worker 超时未返回，主动标记 timed_out
            _monitor_timeout(task_id, future, TASK_TIMEOUT_SECONDS)
        else:
            logger.warning("TaskManager: 任务 type=%s 无对应 worker，立即标记失败", task_type)
            TaskManager.fail(task_id, f"不支持的任务类型: {task_type}")

        return task_id

    # ── 查询任务状态 ──────────────────────────────────────────────
    @staticmethod
    def get(task_id: str) -> dict | None:
        """查询单个任务状态。"""
        db = SessionLocal()
        try:
            task = db.query(TaskORM).filter(TaskORM.id == task_id).first()
            if not task:
                return None
            return _orm_to_dict(task)
        finally:
            db.close()

    @staticmethod
    def list(
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """查询任务列表（按创建时间倒序）。

        Args:
            task_type: 按类型过滤，None 表示全部
            status: 按状态过滤，None 表示全部
            limit: 最大返回数
        """
        db = SessionLocal()
        try:
            q = db.query(TaskORM)
            if task_type:
                q = q.filter(TaskORM.type == task_type)
            if status:
                q = q.filter(TaskORM.status == status)
            tasks = q.order_by(TaskORM.created_at.desc()).limit(limit).all()
            return [_orm_to_dict(t) for t in tasks]
        finally:
            db.close()

    # ── 进度更新（供 worker 调用）──────────────────────────────
    @staticmethod
    def update_progress(task_id: str, progress: int, message: str = "") -> None:
        """更新任务进度并推送 SSE 事件。

        Args:
            progress: 0-100
            message: 可读进度描述
        """
        progress = max(0, min(100, progress))
        db = SessionLocal()
        try:
            task = db.query(TaskORM).filter(TaskORM.id == task_id).first()
            if task and task.status in ("pending", "running"):
                task.progress = progress
                task.progress_message = message
                task.status = "running"
                task.updated_at = datetime.now()
                db.commit()
        finally:
            db.close()

        _broadcast_event(
            task_id,
            {
                "taskId": task_id,
                "status": "running",
                "progress": progress,
                "progressMessage": message,
            },
        )

    # ── 任务完成 ────────────────────────────────────────────────
    @staticmethod
    def complete(task_id: str, result: dict | None = None) -> None:
        """标记任务完成并写入结果。

        B6 修复：允许覆盖 "timed_out" 状态，避免超时后丢弃已产出结果。
        """
        db = SessionLocal()
        try:
            task = db.query(TaskORM).filter(TaskORM.id == task_id).first()
            if task and task.status in ("pending", "running", "timed_out"):
                task.status = "completed"
                task.progress = 100
                task.result = result
                task.completed_at = datetime.now()
                task.updated_at = datetime.now()
                db.commit()
                logger.info("TaskManager: 任务完成 type=%s, id=%s", task.type, task_id)
        finally:
            db.close()

        _broadcast_event(
            task_id,
            {
                "taskId": task_id,
                "status": "completed",
                "progress": 100,
                "progressMessage": "任务完成",
                "result": result,
            },
            is_terminal=True,
        )
        _cleanup_subscribers(task_id)

    # ── 任务超时 ────────────────────────────────────────────────
    @staticmethod
    def timeout(task_id: str, error: str) -> None:
        """标记任务超时（不丢弃已产出结果）。

        与 fail() 的区别：
        - timeout() 设置 status="timed_out"，后续 worker 仍可通过 complete()
          将任务更新为 "completed" 并写入 result。
        - fail() 设置 status="failed"，为最终失败态。
        """
        db = SessionLocal()
        try:
            task = db.query(TaskORM).filter(TaskORM.id == task_id).first()
            if task and task.status in ("pending", "running"):
                task.status = "timed_out"
                task.error = error
                task.completed_at = datetime.now()
                task.updated_at = datetime.now()
                db.commit()
                logger.warning(
                    "TaskManager: 任务超时 type=%s, id=%s: %s", task.type, task_id, error[:200]
                )
        finally:
            db.close()

        _broadcast_event(
            task_id,
            {
                "taskId": task_id,
                "status": "timed_out",
                "progress": 0,
                "progressMessage": "任务超时",
                "error": error,
            },
            is_terminal=True,
        )
        _cleanup_subscribers(task_id)

    # ── 任务失败 ────────────────────────────────────────────────
    @staticmethod
    def fail(task_id: str, error: str) -> None:
        """标记任务失败并记录错误。"""
        db = SessionLocal()
        try:
            task = db.query(TaskORM).filter(TaskORM.id == task_id).first()
            if task and task.status in ("pending", "running", "timed_out"):
                task.status = "failed"
                task.error = error
                task.completed_at = datetime.now()
                task.updated_at = datetime.now()
                db.commit()
                logger.warning(
                    "TaskManager: 任务失败 type=%s, id=%s: %s", task.type, task_id, error[:200]
                )
        finally:
            db.close()

        _broadcast_event(
            task_id,
            {
                "taskId": task_id,
                "status": "failed",
                "progress": 0,
                "progressMessage": "任务失败",
                "error": error,
            },
            is_terminal=True,
        )
        _cleanup_subscribers(task_id)

    # ── SSE 流式推送 ──────────────────────────────────────────────
    @staticmethod
    async def stream_progress(task_id: str):
        """SSE 异步生成器：实时推送任务进度事件。

        用法（FastAPI）：
            @app.get("/api/tasks/{task_id}/stream")
            async def task_stream(task_id: str):
                return StreamingResponse(
                    TaskManager.stream_progress(task_id),
                    media_type="text/event-stream",
                )

        事件格式：
            data: {"taskId": "...", "status": "running", "progress": 50, ...}

        发送完成/失败事件后自动关闭连接。
        """
        # 🛡️ P1-4 修复：每订阅者独立队列，断连即清理
        sub = _register_subscriber(task_id)
        queue = sub.queue

        # 先发送当前状态（避免客户端空白等待）
        current = TaskManager.get(task_id)
        if current:
            init_event = {
                "taskId": task_id,
                "status": current["status"],
                "progress": current["progress"],
                "progressMessage": current.get("progressMessage", ""),
            }
            yield f"data: {_json_dumps(init_event)}\n\n"
            # 若任务已是终态，直接结束
            if current["status"] in ("completed", "failed", "timed_out"):
                yield "data: [DONE]\n\n"
                _unregister_subscriber(task_id, sub)
                return

        # 持续推送直到任务终态
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {_json_dumps(event)}\n\n"

                    # 若任务已进入终态，发送 done 事件后关闭
                    if event.get("status") in ("completed", "failed", "timed_out"):
                        yield "data: [DONE]\n\n"
                        return
                except asyncio.TimeoutError:
                    # 心跳：每 30s 发一次 ping 保持连接
                    yield ": ping\n\n"
        finally:
            # 断连即清理订阅者（防内存泄漏 + 协程泄漏）
            _unregister_subscriber(task_id, sub)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _orm_to_dict(task: TaskORM) -> dict:
    """ORM → 字典。"""
    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "progress": task.progress,
        "progressMessage": task.progress_message,
        "params": task.params,
        "result": task.result,
        "error": task.error,
        "createdAt": task.created_at.isoformat() if task.created_at else "",
        "updatedAt": task.updated_at.isoformat() if task.updated_at else "",
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
    }


def _json_dumps(obj: Any) -> str:
    """安全 JSON 序列化（处理 datetime）。"""

    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    return json.dumps(obj, ensure_ascii=False, default=_default)


# 注入主事件循环引用（启动首次推送后锁定）
_main_loop: asyncio.AbstractEventLoop | None = None
_main_loop_lock = threading.Lock()


def bind_main_event_loop() -> None:
    """由主线程在 FastAPI 启动后调用——锁定本次进程的主事件循环引用。

    🛡️ P2 SSE 事件循环错修复：
    原版 _push_sse 会调用 asyncio.get_event_loop()。当你从后台 worker 线程
    调用时，在 Python 3.10+ 会报 DeprecationWarning，在 3.12+ 直接 RuntimeError。
    改为：主线程启动时记住 loop，worker 线程调 loop.call_soon_threadsafe(...)

    只要主循环未重启，bind 一次即可；期重启会重新 bind（接口在 main 启动后调用）。
    """
    global _main_loop
    try:
        with _main_loop_lock:
            _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        # 不在主线程中调用 — 宿主未就绪时跳过
        pass


def _run_worker(
    task_id: str,
    task_type: str,
    params: dict,
    worker_fn: Callable[[str, dict], None],
) -> None:
    """异常隔离 + 持久化 + 超时保护。

    worker 通过 ThreadPoolExecutor 的 future.result(timeout=TASK_TIMEOUT_SECONDS)
    强制走超时通道。超时后任务标记为 failed，但 worker 线程仍在后台继续运行
    （Python 无法安全中断线程）。通过孤儿线程跟踪避免资源泄漏噪声。
    """
    cancel_evt = get_cancel_event(task_id)
    try:
        # 更新状态为 running
        TaskManager.update_progress(task_id, 0, f"任务开始: {task_type}")
        # 🛡️ P1-3 修复：在调用 worker 前检查取消事件（超时已设置则不启动）
        # B6 修复：超时后由 _monitor_timeout 调用 timeout() 设置 timed_out，
        # 此处不再调用 fail() 覆盖，避免已产出结果被丢弃。
        if cancel_evt.is_set():
            TaskManager.timeout(task_id, "任务在启动前已被取消（超时或手动取消）")
            return
        worker_fn(task_id, params)
    except Exception as worker_exc:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
        logger.exception("TaskManager: worker 异常 task=%s type=%s", task_id, task_type)
        tb = traceback.format_exc()
        try:
            TaskManager.fail(
                task_id,
                f"Worker raised {type(worker_exc).__name__}: {worker_exc}"
                f"\n[full traceback saved below]\n{tb}",
            )
        except Exception as fail_exc:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
            # DB 二次失败兜底：绝不让异常传到 ThreadPoolExecutor 主任务
            tail = "\n".join(tb.splitlines()[-30:])  # 行级末尾 30 行，避免从行中切断
            logger.error(
                "TaskManager: fail() 二次失败 task=%s: %s；原始 traceback 末尾 30 行:\n%s",
                task_id,
                fail_exc,
                tail,
            )


def _monitor_timeout(
    task_id: str,
    future: futures.Future,
    timeout_seconds: float,
) -> None:
    """后台线程监控任务超时。

    在独立 daemon 线程中等待 future.result(timeout)，超时后调用 TaskManager.timeout()
    标记任务超时。future 对应的 worker 线程仍在后台运行（Python 无法安全中断），
    但任务管理层面不再等待；worker 完成后仍可将任务更新为 completed。
    """

    def _watch():
        try:
            future.result(timeout=timeout_seconds)
        except FutureTimeoutError:
            logger.warning(
                "TaskManager: 任务超时 task=%s (%.0fs)，设置取消事件并标记 timed_out",
                task_id,
                timeout_seconds,
            )
            # 🛡️ P1-3 修复：设置协作式取消事件，worker 应在长循环中检查 get_cancel_event(task_id).is_set()
            _set_cancel(task_id)
            try:
                TaskManager.timeout(
                    task_id,
                    f"任务执行超时（>{timeout_seconds:.0f}s），已设置取消信号。"
                    "可能因 CPU/IO 瓶颈或 LLM 响应延迟。",
                )
            except Exception:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
                pass
        except Exception:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
            pass  # worker 自身异常已由 _run_worker 处理

    threading.Thread(target=_watch, daemon=True, name=f"timeout-{task_id[:8]}").start()


def _prune_history(db) -> None:
    """FIFO 清理：保留最近 MAX_TASK_HISTORY 条终态任务，删除更旧的。"""
    try:
        terminal_statuses = ["completed", "failed", "timed_out"]
        count = db.query(TaskORM).filter(TaskORM.status.in_(terminal_statuses)).count()
        if count > MAX_TASK_HISTORY:
            overflow = count - MAX_TASK_HISTORY
            old_ids = (
                db.query(TaskORM.id)
                .filter(TaskORM.status.in_(terminal_statuses))
                .order_by(TaskORM.created_at.asc())
                .limit(overflow)
                .all()
            )
            if old_ids:
                ids_to_delete = [row[0] for row in old_ids]
                # 用显式占位符避免 SQLite IN 子句参数展开问题
                placeholders = ", ".join([f":id_{i}" for i in range(len(ids_to_delete))])
                params = {f"id_{i}": vid for i, vid in enumerate(ids_to_delete)}
                db.execute(
                    text(f"DELETE FROM tasks WHERE id IN ({placeholders})"),
                    params,
                )
                db.commit()
                logger.info("TaskManager: 清理了 %d 条历史任务", len(ids_to_delete))
    except Exception:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
        db.rollback()
        logger.warning("TaskManager: 历史清理失败，忽略")


# ---------------------------------------------------------------------------
# P2：轻量补识别任务 —— 对 arXiv 限流未完成的报告自动重试
# ---------------------------------------------------------------------------
def retry_reflection_source_resolution(batch_size: int = 50) -> dict:
    """重试 source_paper_status='rate_limited' 的报告，非每日全量扫描。

    流程：
    1. 查询 category='report' 且 status='rate_limited' 的记录，最多 batch_size 条
    2. 从 full_text 重新提取原论文题目
    3. 调用 report_paper_resolver.resolve_and_ingest 重试
    4. 更新 source_paper_id / source_paper_status

    返回汇总：{"processed": int, "resolved": int, "failed": int, "no_title": int}
    """
    from .database import SessionLocal
    from .models import Paper as PaperORM
    from .reflection_docx_parser import extract_title_from_text
    from .report_paper_resolver import resolve_and_ingest

    db = SessionLocal()
    try:
        rows = (
            db.query(PaperORM)
            .filter(PaperORM.category == "report")
            .filter(PaperORM.source_paper_status == "rate_limited")
            .order_by(PaperORM.id)
            .limit(batch_size)
            .all()
        )

        summary = {"processed": 0, "resolved": 0, "failed": 0, "no_title": 0}
        for paper in rows:
            summary["processed"] += 1
            title = extract_title_from_text(paper.full_text or "")
            if not title:
                paper.source_paper_status = "no_title"
                summary["no_title"] += 1
                db.commit()
                continue

            try:
                resolved = resolve_and_ingest(title, db=db)
                status = resolved.get("status", "no_match")
                if status == "download_failed":
                    status = "import_failed"
                paper.source_paper_status = status
                if status in ("exists", "imported") and resolved.get("paper_id"):
                    paper.source_paper_id = resolved["paper_id"]
                    summary["resolved"] += 1
                elif status == "rate_limited":
                    # 仍然限流，留待下次；不算失败也不算成功
                    pass
                else:
                    summary["failed"] += 1
            except Exception as e:  # noqa: BLE001 - TaskManager worker body - 后台任务异常必须兜底写 failed 状态
                logger.warning("补识别任务处理 %s 失败: %s", paper.id, e)
                paper.source_paper_status = "import_failed"
                summary["failed"] += 1

            db.commit()
        logger.info("补识别任务完成: %s", summary)
        return summary
    finally:
        db.close()
