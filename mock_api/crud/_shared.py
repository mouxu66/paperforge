"""Shared CRUD utilities used across multiple mock_api.crud modules.

This module holds small, stateless helpers that were previously duplicated
across CRUD submodules (and occasionally outside of ``crud/``). Keeping them
here makes the duplication visible and avoids copy-paste drift.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from sqlalchemy.orm import Session


def _fmt_dt(dt) -> str:
    """datetime → 'YYYY-MM-DD HH:MM' 字符串，None 返回空串。"""
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def run_in_bg_session(fn: Callable[[Session], None]) -> None:
    """在后台线程中创建独立 Session 并执行 ``fn``，执行后自动关闭。

    用于不阻塞主线程的后台任务（如导入后的富化、审稿）。异常被静默吞掉，
    避免后台线程崩溃影响主流程。
    """
    from ..database import SessionLocal

    def _run() -> None:
        db2 = SessionLocal()
        try:
            fn(db2)
        except Exception:  # noqa: BLE001 - 后台任务失败不应影响主流程
            pass
        finally:
            db2.close()

    threading.Thread(target=_run, daemon=True).start()
