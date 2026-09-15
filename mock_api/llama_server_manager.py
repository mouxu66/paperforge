"""llama-server（Qwen / DEPTH 后端）生命周期托管。

把桌面上 start-llama-dflash-wsl.bat 的启动命令固化为 Python 托管：
- PaperForge 启动即拉起 llama-server（qwen_autostart）。
- port pre-check：若 8080 已被外部 llama-server 占用则复用，不重复拉起
  （兼容你手动双击 bat 启动的场景）。
- 冷启动宽限：首次 CUDA kernel 编译可能 3-5 分钟，grace window 内不判失败。
- 探活 /health：就绪后才放 DEPTH 请求，期间调用方显示「模型加载中」提示。
- 释放：按 exe 名找 PID，taskkill /F /T 杀进程树（兼容 start /MIN 独立窗口）。

与 OCR 的互斥已由 vram_scheduler 仲裁（ADR-013：text↔vision 互斥由 vram_scheduler 仲裁），
本模块只管 llama-server（text-Qwen, 8080）自身死活；vision 为外部 HTTP，本模块不托管。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import urllib.request
from enum import Enum

logger = logging.getLogger(__name__)


class ModelLoadingError(Exception):
    """llama-server 尚未就绪，调用方应转为前端的『模型加载中』提示。

    Attributes:
        eta_seconds: 预估还需等待秒数（粗估，用于前端倒计时提示）。
    """

    def __init__(self, message: str, eta_seconds: int = 0) -> None:
        super().__init__(message)
        self.eta_seconds = eta_seconds


class StartStatus(str, Enum):
    """llama-server 启动/就绪状态。"""

    READY = "ready"
    EXTERNAL_REUSED = "external_reused"
    STARTING = "starting"
    FAILED = "failed"


class StartResult:
    """ensure_started 的返回值，保留 bool 兼容性。"""

    def __init__(self, ready: bool, status: StartStatus) -> None:
        self.ready = ready
        self.status = status

    def __bool__(self) -> bool:
        return self.ready

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return self.ready == other
        return super().__eq__(other)

    def __repr__(self) -> str:
        return f"StartResult(ready={self.ready}, status={self.status.value})"


class LlamaServerManager:
    """管理 llama-server 进程生命周期（单例风格）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._managed = False  # 是否由本进程拉起（外部复用则为 False）
        self._ready = False
        self._shutdown_event = threading.Event()
        self._watcher_thread: threading.Thread | None = None
        self._restart_count = 0
        self._max_restarts = 3
        self._last_crash_time = 0.0  # 上次崩溃的 time.time()，用于时间窗 replenish

    # ── 端口/探活 ──────────────────────────────────────────────────
    @staticmethod
    def _port_in_use(host: str, port: int) -> bool:
        """探测端口是否已有进程 LISTEN（复用外部实例的判定）。"""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect((host, port))
                return True  # 连得上 = 有人 LISTEN
            except OSError:
                return False

    def _health_ok(self, port: int) -> bool:
        url = f"http://127.0.0.1:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - 探活失败 = 未就绪
            return False

    # ── 启动 ──────────────────────────────────────────────────────
    def ensure_started(self, wait: bool = True) -> StartResult:
        """确保 llama-server 就绪。

        - 若 8080 已被外部占用 → 标记复用，直接返回 True（不拉起）。
        - 否则 subprocess.Popen 拉起，后台轮询 /health 至就绪或 grace 超时。
        - wait=False：仅触发拉起，不阻塞等待（供启动自启后台调用）。
        - 启动失败返回 StartStatus.FAILED，不抛异常，便于调用方降级。
        """
        from .settings import get_settings

        settings = get_settings()
        port = settings.llama_server_port

        with self._lock:
            # 已就绪直接返回
            if self._ready and (self._managed or self._port_in_use("127.0.0.1", port)):
                return StartResult(True, StartStatus.READY)
            # 端口已被外部占用 → 复用
            if self._port_in_use("127.0.0.1", port):
                self._managed = False
                self._ready = True
                logger.info("复用外部 llama-server（端口 %s 已 LISTEN）", port)
                return StartResult(True, StartStatus.EXTERNAL_REUSED)
            # 配置缺失 → 不托管（DEPTH 会按原逻辑连接失败降级）
            if not settings.llama_server_exe or not settings.llama_server_model:
                logger.warning("llama_server_exe / llama_server_model 未配置，跳过托管启动")
                return StartResult(False, StartStatus.FAILED)
            # 已有托管进程在启动/运行中，避免重复 spawn 导致进程/线程泄漏
            if self._managed and self._proc is not None and self._proc.poll() is None:
                logger.debug("llama-server 已在启动中，跳过重复 spawn")
                return StartResult(False, StartStatus.STARTING)
            # 拉起
            self._proc = self._spawn(settings)
            self._managed = True
            self._ready = False

        if wait:
            ready = self._wait_until_ready(settings.llama_server_cold_grace)
            return StartResult(ready, StartStatus.READY if ready else StartStatus.FAILED)
        # 后台等待，避免阻塞启动线程
        threading.Thread(
            target=self._wait_until_ready,
            args=(settings.llama_server_cold_grace,),
            daemon=True,
        ).start()
        return StartResult(False, StartStatus.STARTING)

    def _spawn(self, settings) -> subprocess.Popen:
        exe = settings.llama_server_exe
        cmd = [
            exe,
            "-m",
            settings.llama_server_model,
            "--port",
            str(settings.llama_server_port),
            "--host",
            settings.llama_server_host,
            "-ngl",
            str(settings.llama_server_ngl),
            "-c",
            str(settings.llama_server_ctx),
            "--parallel",
            str(settings.llama_server_parallel),
            "-ctk",
            "q4_0",
            "-ctv",
            "q4_0",
            "--jinja",
            "--reasoning",
            settings.llama_server_reasoning,
            "--temp",
            str(settings.llama_server_temp),
            "--top-p",
            str(settings.llama_server_top_p),
            "--top-k",
            str(settings.llama_server_top_k),
            "--repeat-penalty",
            "1.0",
            "--min-p",
            str(settings.llama_server_min_p),
            "--presence-penalty",
            str(settings.llama_server_presence_penalty),
            # ⚠️ 链内必须含 top_p，否则 --top-p 设置空转不生效（2026-08-21 Ornith 修正）
            "--samplers",
            "top_k;top_p;temp;penalties",
            "-fit",
            "off",
        ]
        if settings.llama_server_flash_attn:
            cmd += ["-fa", "on"]
        if settings.llama_server_n_cpu_moe:
            cmd += ["--n-cpu-moe", str(settings.llama_server_n_cpu_moe)]
        if settings.llama_server_draft:
            cmd += [
                "--spec-type",
                "draft-dflash",
                "--spec-draft-model",
                settings.llama_server_draft,
                "--spec-draft-n-max",
                str(settings.llama_server_draft_n_max),
                "--spec-draft-ngl",
                str(settings.llama_server_draft_ngl),
            ]
        logger.info("拉起 llama-server: %s", " ".join(cmd))
        kwargs: dict[str, object] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if __import__("sys").platform == "win32":
            # 避免弹出黑窗口
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, **kwargs)
        self._managed = True
        self._shutdown_event.clear()
        # 避免崩溃重启时启动多个 watcher 线程；只有一个 watcher 负责循环自愈
        if self._watcher_thread is None or not self._watcher_thread.is_alive():
            self._watcher_thread = threading.Thread(
                target=self._watch_process,
                args=(settings,),
                daemon=True,
            )
            self._watcher_thread.start()
        return proc

    def _wait_until_ready(self, grace: int) -> bool:
        from .settings import get_settings

        port = get_settings().llama_server_port
        deadline = time.time() + grace
        while time.time() < deadline:
            if self._health_ok(port):
                with self._lock:
                    self._ready = True
                logger.info("llama-server 已就绪（端口 %s）", port)
                return True
            time.sleep(3)
        with self._lock:
            self._ready = False
        # 仅打 log，不自动杀（让用户看到失败原因）；调用方可重试或降级
        logger.error("llama-server 在 %s 秒宽限内未就绪", grace)
        return False

    def _watch_process(self, settings) -> None:
        """后台守护线程：llama-server 崩溃后自动自愈重启。

        仅对由本进程托管且已就绪过的进程生效；首次启动失败不会无限重试。
        重启次数限制为 _max_restarts，避免异常配置导致 spin-loop。
        """
        while not self._shutdown_event.is_set():
            proc = None
            with self._lock:
                # 当 shutdown() 重置 _watcher_thread 或 spawn 启动了新 watcher 时，
                # 旧 watcher 立即退出，避免与新的 watcher 并发重启/监控。
                if self._watcher_thread is not threading.current_thread():
                    break
                proc = self._proc
            if proc is None:
                break
            try:
                exit_code = proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                time.sleep(2)
                continue
            if self._shutdown_event.is_set():
                break
            with self._lock:
                was_ready = self._ready
                self._ready = False
                # 外部复用或本进程已放弃托管，不再重启
                if not self._managed or proc is not self._proc:
                    break
            if not was_ready:
                # 启动阶段就失败，不自动重启，避免配置错误时无限循环
                logger.warning("llama-server 在启动阶段退出，不自动重启")
                break
            with self._lock:
                now = time.time()
                # 健康 5 分钟后 replenish 计数器
                if now - self._last_crash_time > 300:
                    self._restart_count = 0
                self._restart_count += 1
                self._last_crash_time = now
                if self._restart_count >= self._max_restarts:
                    logger.error(
                        "llama-server 在 5 分钟内崩溃 %s 次，停止自愈重启", self._max_restarts
                    )
                    break
                restart_count = self._restart_count
            logger.warning(
                "llama-server 异常退出（code=%s），第 %s 次自愈重启", exit_code, restart_count
            )
            with self._lock:
                self._proc = self._spawn(settings)
                self._managed = True
            # 释放锁后再等待就绪：_wait_until_ready 内部重新取锁，持锁会死锁。
            # 修复自愈重启后 _ready 永久 False、自愈仅生效一次的问题（recheck5 #2）。
            self._wait_until_ready(settings.llama_server_cold_grace)

    # ── 释放 ──────────────────────────────────────────────────────
    def shutdown(self) -> None:
        """关闭 llama-server。

        - 仅杀死由本进程托管的子进程；外部复用的实例不会被 taskkill。
        """
        with self._lock:
            self._ready = False
            self._managed = False
            self._shutdown_event.set()
            # 重置 watcher 引用，确保下次 spawn 能创建新的 watcher。
            # 旧 watcher 线程会在检查到 shutdown event 后退出。
            self._watcher_thread = None
            proc = self._proc
            self._proc = None

        # 只终止本进程拉起的进程；外部实例不要动。
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass

    # ── 查询 ──────────────────────────────────────────────────────
    def is_ready(self) -> bool:
        from .settings import get_settings

        port = get_settings().llama_server_port
        with self._lock:
            if self._ready:
                return True
        # 外部复用场景：端口在就认为就绪
        return self._port_in_use("127.0.0.1", port)


_manager_instance: LlamaServerManager | None = None
_manager_lock = threading.Lock()


def get_llama_server_manager() -> LlamaServerManager:
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = LlamaServerManager()
    return _manager_instance


def reset_llama_server_manager() -> None:
    """测试 / 配置变更时清理单例。"""
    global _manager_instance
    with _manager_lock:
        if _manager_instance is not None:
            try:
                _manager_instance.shutdown()
            except Exception:  # noqa: BLE001
                pass
        _manager_instance = None
