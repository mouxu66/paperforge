"""Zotero 本地目录监控（Watchdog）—— 自动感应新 PDF 并解析入库。

当用户在 Zotero 中拖入新 PDF 时，PaperForge 后台自动：
1. 检测文件变更事件（created / moved to）
2. 校验文件类型（仅 .pdf）
3. 解析 PDF 元数据 + 全文
4. 写入 papers 表 + FTS5 索引
5. 记录导入日志

依赖：watchdog >= 3.0.0
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Watchdog 状态
_watchdog_instance: ZoteroWatcher | None = None
_watchdog_lock = threading.Lock()


class ZoteroWatcher:
    """Zotero 本地目录监控器。

    使用 watchdog 的 Observer 模式监控 Zotero 存储目录的 PDF 文件变更。
    """

    def __init__(self, watch_dir: str):
        self._watch_dir = Path(watch_dir).resolve()
        self._observer: Any | None = None
        self._running = False
        self._last_event: str | None = None
        self._auto_imported = 0
        self._lock = threading.Lock()
        self._recent_files: dict[str, float] = {}  # 防抖：{filepath: timestamp}
        self._debounce_seconds = 10.0  # 10 秒内同一文件只处理一次

    @property
    def watch_dir(self) -> str:
        return str(self._watch_dir)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_event(self) -> str | None:
        with self._lock:
            return self._last_event

    @property
    def auto_imported(self) -> int:
        with self._lock:
            return self._auto_imported

    def start(self) -> bool:
        """启动文件监控。

        Returns:
            True 启动成功，False 失败。
        """
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("Zotero Watchdog: watchdog 未安装，请运行 pip install watchdog>=3.0.0")
            return False

        if not self._watch_dir.exists():
            logger.warning("Zotero Watchdog: 监控目录不存在 %s", self._watch_dir)
            return False

        class _PdfHandler(FileSystemEventHandler):
            def __init__(self, watcher: ZoteroWatcher):
                self._watcher = watcher

            def on_created(self, event):
                if not event.is_directory and event.src_path.lower().endswith(".pdf"):
                    logger.info("Zotero Watchdog: 检测到新 PDF: %s", event.src_path)
                    self._watcher._on_pdf_found(event.src_path)

            def on_moved(self, event):
                if not event.is_directory and event.dest_path.lower().endswith(".pdf"):
                    logger.info("Zotero Watchdog: 检测到移动 PDF: %s", event.dest_path)
                    self._watcher._on_pdf_found(event.dest_path)

        self._observer = Observer()
        self._observer.schedule(
            _PdfHandler(self),
            str(self._watch_dir),
            recursive=True,
        )
        self._observer.start()
        self._running = True
        logger.info("Zotero Watchdog: 已启动，监控目录 %s", self._watch_dir)
        return True

    def stop(self) -> None:
        """停止文件监控（非阻塞）。"""
        if self._observer:
            self._observer.stop()

            # 在单独线程中 join，避免阻塞调用方
            def _join():
                try:
                    if self._observer:
                        self._observer.join(timeout=5)
                except Exception:  # noqa: BLE001 - Zotero 监控 poll - 单次异常不打断监控循环
                    pass

            threading.Thread(target=_join, daemon=True).start()
            self._observer = None
        with self._lock:
            self._running = False
        logger.info("Zotero Watchdog: 已停止")

    def _on_pdf_found(self, filepath: str) -> None:
        """处理检测到的新 PDF：解析 → 入库。"""
        # 防抖：同一文件 10 秒内只处理一次
        now = time.time()
        with self._lock:
            last_time = self._recent_files.get(filepath, 0)
            if now - last_time < self._debounce_seconds:
                return
            self._recent_files[filepath] = now
            self._last_event = datetime.now().isoformat()

        # 等待文件写入完成：轮询文件大小稳定（最多等 15s）
        try:
            file_path = Path(filepath)
            if not file_path.exists():
                return
            prev_size = -1
            for _ in range(15):
                time.sleep(1)
                try:
                    cur_size = file_path.stat().st_size
                except OSError:
                    return
                if cur_size == prev_size and cur_size > 0:
                    break
                prev_size = cur_size
            else:
                logger.warning("Zotero Watchdog: 文件写入未稳定 %s", filepath)
                return
        except Exception:  # noqa: BLE001 - Zotero 监控 poll - 单次异常不打断监控循环
            pass

        try:
            file_path = Path(filepath)
            if not file_path.exists():
                logger.warning("Zotero Watchdog: 文件已不存在 %s", filepath)
                return

            # 跳过太小或太大的文件（< 1KB 或 > 200MB）
            size = file_path.stat().st_size
            if size < 1024:
                logger.warning("Zotero Watchdog: 文件过小 %s (%d bytes)，跳过", filepath, size)
                return
            if size > 200 * 1024 * 1024:
                logger.warning(
                    "Zotero Watchdog: 文件过大 %s (%.1f MB)，跳过", filepath, size / 1024 / 1024
                )
                return

            content = file_path.read_bytes()

            # 解析入库
            from .database import SessionLocal
            from .pdf_parser import process_one_pdf

            db = SessionLocal()
            try:
                result = process_one_pdf(content, file_path.name, db)
                if result.success:
                    with self._lock:
                        self._auto_imported += 1
                    logger.info(
                        "Zotero Watchdog: 自动导入成功 %s -> paper_id=%s",
                        filepath,
                        result.id,
                    )
                else:
                    logger.warning(
                        "Zotero Watchdog: 解析失败 %s: %s",
                        filepath,
                        result.error,
                    )
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 - Zotero 监控 poll - 单次异常不打断监控循环
            logger.exception("Zotero Watchdog: 处理异常 %s: %s", filepath, e)


# ---------------------------------------------------------------------------
# 全局生命周期管理
# ---------------------------------------------------------------------------
def get_watchdog() -> ZoteroWatcher | None:
    """获取当前运行的 watchdog 实例。"""
    with _watchdog_lock:
        return _watchdog_instance


def start_watchdog(watch_dir: str) -> bool:
    """启动 Zotero 目录监控。

    Args:
        watch_dir: Zotero 本地存储目录路径。

    Returns:
        True 启动成功，False 失败。
    """
    global _watchdog_instance

    # 先停止已有实例
    stop_watchdog()

    with _watchdog_lock:
        watcher = ZoteroWatcher(watch_dir)
        if not watcher.start():
            return False
        _watchdog_instance = watcher
        return True


def stop_watchdog() -> None:
    """停止 Zotero 目录监控。"""
    global _watchdog_instance
    with _watchdog_lock:
        if _watchdog_instance:
            _watchdog_instance.stop()
            _watchdog_instance = None


def get_watchdog_status() -> dict:
    """获取当前监控状态。"""
    watcher = get_watchdog()
    if watcher is None:
        return {
            "enabled": False,
            "watchDir": "",
            "running": False,
            "lastEvent": None,
            "autoImported": 0,
        }
    return {
        "enabled": True,
        "watchDir": watcher.watch_dir,
        "running": watcher.running,
        "lastEvent": watcher.last_event,
        "autoImported": watcher.auto_imported,
    }
