"""VRAM 仲裁（text-Qwen ↔ vision-Qwen）— ADR-013 单锁重构。

拓扑变更（2026-07-27 OCR 退役后）：
- 旧 `qwen ↔ ocr` 互斥已失效（旧 OCR 已退役，视觉服务由外部 HTTP 管理）。
- 新拓扑：`text-Qwen`（8080 托管 llama-server）↔ `vision-Qwen`（外部 HTTP
  `vision_http_url`，Qwen3-VL-4B，不可由本调度器托管/kill）。

核心原则（Archi ADR-013）：
- **单一仲裁原语**：内部只有一把 `threading.Lock` + `Condition` 守护权威状态机，
  令牌（GPU 模型槽位）容量=1，跨 kind 互斥、同 kind 可重入。
- async API（`acquire`/`release`/`VRAMContext`）一律经 `loop.run_in_executor`
  复用**同一把锁**的同步核心（`_core_acquire`/`_core_release`），根除旧版
  async(`asyncio.Queue`) 与 sync(`threading.Lock`) 双路径互不协调、可同时进入的 bug。
- vision 不可托管——只协调（令牌串行 + 同卡时让出 8080），不 kill vision 进程。
- `vram_exclusive=False` 保留为**文档化的无保护模式**：任一 kind 直接放行，
  不取互斥令牌、不做 8080/视觉切换协调（仅 text 在需要且配置时仍 ensure_started）。

状态机：
    IDLE ──acquire("text")────▶ TEXT_ACTIVE
    IDLE ──acquire("vision")──▶ VISION_ACTIVE
    TEXT_ACTIVE  ─acquire("vision")──▶ (co-located 时 shutdown 8080) ▶ VISION_ACTIVE
    VISION_ACTIVE ─acquire("text")───▶ (等待 vision release) ▶ TEXT_ACTIVE
    VISION_ACTIVE ─release("vision")─▶ (SWITCHING_TO_TEXT, 后台拉回 8080) ▶ TEXT_ACTIVE

异步用法（推荐）：::

    async with VRAMContext("text", timeout=30) as ctx:
        result = await call_llm(prompt, **ctx.llm_kwargs)

同步用法（向后兼容 + vision 包裹）：::

    with vram_guard("vision"):
        do_vision_http_call()

    # 或直接调旧别名 API（保留一轮）:
    get_vram_scheduler().request_text(wait=False)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from .llama_server_manager import StartStatus, get_llama_server_manager

logger = logging.getLogger(__name__)


class VRAMKind(str, Enum):
    """仲裁 kind：text（8080 托管 Qwen）= 原 qwen；vision（外部 HTTP VL）= 原 ocr 语义位。"""

    TEXT = "text"
    VISION = "vision"


class VRAMState(str, Enum):
    IDLE = "idle"
    TEXT_ACTIVE = "text_active"
    VISION_ACTIVE = "vision_active"
    SWITCHING_TO_TEXT = "switching_to_text"

    # ── 向后兼容别名（保留一轮，下一轮删除）──────────────────────
    QWEN_ACTIVE = TEXT_ACTIVE
    OCR_ACTIVE = VISION_ACTIVE
    SWITCHING_TO_QWEN = SWITCHING_TO_TEXT


class VRAMTimeoutError(asyncio.TimeoutError):
    """跨 kind 令牌获取超时（另一侧在 exclusive 模式下未释放 VRAM）。"""


def _as_kind(kind: str | VRAMKind) -> VRAMKind:
    """归一化 kind 字符串，兼容旧 "qwen"/"ocr" 调用。"""
    if isinstance(kind, VRAMKind):
        return kind
    s = str(kind).strip().lower()
    if s in ("text", "qwen"):
        return VRAMKind.TEXT
    if s in ("vision", "ocr"):
        return VRAMKind.VISION
    raise ValueError(f"未知 VRAM kind: {kind!r}（期望 text/vision，兼容 qwen/ocr）")


class VRAMContext:
    """异步上下文管理器：独占 VRAM 访问令牌。

    用法::

        async with VRAMContext("text", timeout=30) as ctx:
            result = await call_llm(prompt)

    ``__aexit__`` 自动 release，消灭手动 _release 散落调用。
    兼容旧 ``VRAMContext("qwen"|"ocr")``。
    """

    def __init__(
        self,
        kind: str,
        timeout: float = 30,
        scheduler: VRAMScheduler | None = None,
    ) -> None:
        self._kind = _as_kind(kind)
        self._timeout = timeout
        self._scheduler = scheduler or get_vram_scheduler()
        self._acquired = False

    async def __aenter__(self) -> VRAMContext:
        await self._scheduler.acquire(self._kind, timeout=self._timeout)
        self._acquired = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        try:
            await self._scheduler.release(self._kind)
        finally:
            self._acquired = False

    @property
    def kind(self) -> str:
        return self._kind.value


class _SyncVRAMGuard:
    """同步上下文管理器：供非异步调用方（figure_qwen、pdf_parser、workers、routers）使用。"""

    def __init__(self, kind: str, scheduler: VRAMScheduler | None = None) -> None:
        self._kind = _as_kind(kind)
        self._scheduler = scheduler or get_vram_scheduler()

    def __enter__(self) -> _SyncVRAMGuard:
        self._scheduler._core_acquire(self._kind)
        return self

    def __exit__(self, *args: Any) -> None:
        self._scheduler._core_release(self._kind)


def vram_guard(kind: str) -> _SyncVRAMGuard:
    """同步上下文管理器工厂：为 figure_qwen / pdf_parser / workers / routers 等非异步调用方提供。

    用法::

        with vram_guard("vision"):
            resp = requests.post(vision_url, ...)
    """
    return _SyncVRAMGuard(kind)


# ===========================================================================
# VRAMScheduler（保持向后兼容的单例）
# ===========================================================================
class VRAMScheduler:
    """text-Qwen(8080) 与 vision-Qwen(vision_http_url) 的显存互斥仲裁器。

    ADR-013：内部权威状态机只由一把 ``_lock`` + ``_cond`` 守护；令牌容量=1，
    跨 kind 互斥、同 kind 重入。async API 经 run_in_executor 复用同一把锁。
    """

    def __init__(self) -> None:
        # ── 唯一权威锁 + 条件变量（根除 async/sync 双路径互杀）──
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # 令牌持有者（kind）与重入计数；跨 kind 互斥靠 _cond.wait
        self._kind: VRAMKind | None = None
        self._count: int = 0

        self._state = VRAMState.IDLE

        # 前端轮询用事件
        self._event: dict[str, Any] = {"kind": "ready", "eta_seconds": 0, "ts": 0.0}
        self._event_history: list[dict[str, Any]] = []
        self._event_lock = threading.Lock()

    # ── 事件管理 ──────────────────────────────────────────────────
    def _set_event(self, kind: str, eta_seconds: int = 0) -> None:
        now = time.time()
        event = {
            "id": f"vram-{time.time_ns()}",
            "kind": kind,
            "message": f"VRAM 调度状态：{kind}",
            "eta_seconds": eta_seconds,
            "ts": now,
        }
        with self._event_lock:
            self._event = event.copy()
            self._event_history.append(event)
            # 运行时诊断记录只保留最近 200 条，避免长时间运行无限增长。
            del self._event_history[:-200]

    def get_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回最近的 VRAM 调度事件，按时间倒序排列。"""
        limit = max(1, min(int(limit), 100))
        with self._event_lock:
            return [event.copy() for event in reversed(self._event_history[-limit:])]

    def clear_events(self) -> None:
        """清空当前进程内的运行时 VRAM 事件。"""
        with self._event_lock:
            self._event_history.clear()

    def get_status(self) -> dict[str, Any]:
        with self._event_lock:
            event = dict(self._event)
        with self._lock:
            event["state"] = self._state.value
        if event["kind"] == "ready" and time.time() - event["ts"] > 60:
            event["kind"] = "idle"
        return event

    # ── 同卡探测（关键开关）──────────────────────────────────────
    def _colocated_with_vision(self) -> bool:
        """解析 vision_http_url 的 host 与 llama_server_host 比较。

        - 同机（127.0.0.1/localhost/0.0.0.0 指向同一 GPU）→ True，启用跨 kind 互斥切换。
        - 异机/异卡 → False，vision 仅做自身串行化，text 不受影响，不 shutdown 8080。
        """
        try:
            from .settings import get_settings

            s = get_settings()
            vision_url = s.vision_http_url
            if not vision_url:
                return False
            vh = (urlparse(vision_url).hostname or "").strip().lower()
            th = (s.llama_server_host or "").strip().lower()
            if not vh or not th:
                return False

            def _norm(h: str) -> str:
                # 这些均代表「本机/任意本地接口」→ 视为同一张卡
                return "local" if h in ("127.0.0.1", "localhost", "0.0.0.0", "") else h

            return _norm(vh) == _norm(th)
        except Exception:  # noqa: BLE001
            logger.warning("同卡探测失败，按异卡处理（不 shutdown 8080）", exc_info=True)
            return False

    # ==================================================================
    # 同步核心（唯一事实权威）：_core_acquire / _core_release
    # 所有 sync/async API 最终都走到这里，复用同一把锁。
    # ==================================================================
    def _core_acquire(self, kind: str | VRAMKind, timeout: float = 30, wait: bool = False) -> None:
        """同步获取 VRAM 令牌（内部核心）。

        - exclusive 模式下，若另一 kind 持有令牌，则等待其释放（跨 kind 互斥）。
        - 同 kind 重入：计数 +1，无副作用。
        - 首获该 kind：执行 side effect（text → ensure_started 8080；
          vision 同卡 exclusive → shutdown 8080 让出显存给 vision）。
        """
        from .settings import get_settings

        k = _as_kind(kind)
        settings = get_settings()
        exclusive = settings.vram_exclusive

        with self._cond:
            if exclusive and self._kind is not None and self._kind != k:
                deadline = time.monotonic() + timeout
                while self._kind is not None and self._kind != k:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise VRAMTimeoutError(
                            f"VRAM 令牌获取超时 ({timeout:.0f}s)：{k.value} 等待中，"
                            f"当前 VRAM 被 {self._kind.value} 占用"
                        )
                    self._cond.wait(remaining)

            first = self._kind != k
            self._kind = k
            self._count += 1
            if not first:
                return  # 重入：无副作用

            # ── 首获该 kind 的 side effect ──
            if k == VRAMKind.TEXT:
                self._state = VRAMState.TEXT_ACTIVE
                self._text_acquire_side_effects(settings, wait)
            else:  # VISION
                colocated = self._colocated_with_vision()
                if exclusive and colocated:
                    mgr = get_llama_server_manager()
                    logger.info("互斥切换：释放 text-Qwen(8080) 显存以启用 vision")
                    mgr.shutdown()
                self._state = VRAMState.VISION_ACTIVE
                self._set_event("vision")

    def _core_release(self, kind: str | VRAMKind) -> None:
        """同步释放 VRAM 令牌（内部核心）。"""
        from .settings import get_settings

        k = _as_kind(kind)
        settings = get_settings()
        exclusive = settings.vram_exclusive

        with self._cond:
            if self._kind != k:
                logger.warning(
                    "VRAM release 不匹配（当前持有 %s，尝试释放 %s），忽略",
                    self._kind,
                    k,
                )
                return
            self._count -= 1
            if self._count > 0:
                return  # 重入释放，仍持有

            self._kind = None
            self._cond.notify_all()

            # ── 完全释放后的 side effect ──
            if k == VRAMKind.VISION:
                colocated = self._colocated_with_vision()
                if exclusive and colocated:
                    # 同卡：后台拉回 8080（对称于旧「OCR 释放后 autostart Qwen」）
                    self._state = VRAMState.SWITCHING_TO_TEXT
                    if settings.qwen_autostart:
                        self._autostart_text_in_background()
                    else:
                        self._state = VRAMState.IDLE
                else:
                    self._state = VRAMState.IDLE
            else:  # TEXT：Qwen 常驻，不 shutdown，仅释放令牌
                self._state = VRAMState.IDLE

    # ── text side effect ──────────────────────────────────────────
    def _text_acquire_side_effects(self, settings: Any, wait: bool) -> None:
        mgr = get_llama_server_manager()
        result = mgr.ensure_started(wait=wait)
        if result.ready:
            self._set_event("ready")
        elif result.status == StartStatus.STARTING:
            self._set_event("loading", eta_seconds=settings.llama_server_cold_grace)
        else:
            self._set_event("failed")

    def _autostart_text_in_background(self) -> None:
        """后台拉回 text-Qwen(8080)，避免阻塞 release 调用方。"""

        def _run() -> None:
            mgr = get_llama_server_manager()
            try:
                ready = mgr.ensure_started(wait=True).ready
                with self._cond:
                    if self._state == VRAMState.SWITCHING_TO_TEXT:
                        self._state = VRAMState.TEXT_ACTIVE if ready else VRAMState.IDLE
                        if not ready:
                            self._set_event("failed")
            except Exception:  # noqa: BLE001
                logger.warning("后台 autostart text-Qwen 失败", exc_info=True)
                with self._cond:
                    if self._state == VRAMState.SWITCHING_TO_TEXT:
                        self._state = VRAMState.IDLE

        threading.Thread(target=_run, daemon=True).start()

    # ==================================================================
    # async 包装：一律 run_in_executor 复用同一把锁（根除双路径互杀）
    # ==================================================================
    async def acquire(self, kind: str | VRAMKind, timeout: float = 30) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self._core_acquire(kind, timeout=timeout))

    async def release(self, kind: str | VRAMKind) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self._core_release(kind))

    # ==================================================================
    # 面向调用方的同步 API（向后兼容 + 新命名）
    # ==================================================================
    def request_text(self, wait: bool = True) -> None:
        """请求使用 text-Qwen（DEPTH/figure 文本回退前调用）。

        若已处于 TEXT_ACTIVE（如被 VRAMContext/vram_guard 包裹），跳过 acquire，
        仅按需等待 ensure_started。避免 DAG 内 call_llm 重复获取锁。
        """
        if self.state() == VRAMState.TEXT_ACTIVE:
            if wait:
                get_llama_server_manager().ensure_started(wait=True)
            return
        self._core_acquire(VRAMKind.TEXT, wait=wait)

    def request_vision(self) -> None:
        """请求使用 vision（figure 视觉理解前调用）— 只串行化 + 同卡让出 8080。

        vision 进程不可托管，不拉起/强杀；仅取令牌标记 VISION_ACTIVE。
        """
        self._core_acquire(VRAMKind.VISION)

    def vision_finished(self) -> None:
        """vision 用完 — 释放令牌；同卡 exclusive 下后台拉回 text-Qwen(8080)。"""
        self._core_release(VRAMKind.VISION)

    def state(self) -> VRAMState:
        with self._lock:
            return self._state

    # ── 向后兼容别名（保留一轮，下一轮删除）──────────────────────
    # request_qwen → request_text；request_ocr → request_vision；
    # ocr_finished → vision_finished。
    request_qwen = request_text
    request_ocr = request_vision
    ocr_finished = vision_finished


# ===========================================================================
# 单例
# ===========================================================================
_scheduler_instance: VRAMScheduler | None = None
_scheduler_lock = threading.Lock()


def get_vram_scheduler() -> VRAMScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = VRAMScheduler()
    return _scheduler_instance


def reset_vram_scheduler() -> None:
    global _scheduler_instance
    with _scheduler_lock:
        _scheduler_instance = None
