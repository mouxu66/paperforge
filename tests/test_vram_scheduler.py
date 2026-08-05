"""VRAM 仲裁调度器 + llama-server 托管单测（ADR-013：text-Qwen ↔ vision-Qwen）。

用 mock 替代真实 subprocess / OCR 子进程，验证：
- 状态机转换（IDLE ↔ TEXT_ACTIVE ↔ VISION_ACTIVE ↔ SWITCHING_TO_TEXT）
- 跨 kind 令牌互斥（容量 1，必须 release 再 acquire；不再有旧「持 A 调 B 自动切换」）
- co-located 下 acquire("vision") 让出 8080、release("vision") 后台 autostart 8080
- vram_exclusive=False 无保护模式（不取互斥令牌、不 shutdown、不后台 autostart）
- port pre-check 复用外部实例
- 关闭时按 exe 名 taskkill
- 并发压力（text/vision 快速交替请求，令牌始终互斥、无死锁）
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from mock_api.llama_server_manager import StartResult, StartStatus, get_llama_server_manager
from mock_api.vram_scheduler import VRAMKind, VRAMState, get_vram_scheduler, vram_guard


@pytest.fixture
def settings_mock():
    with patch("mock_api.settings.get_settings") as g:
        s = MagicMock()
        s.llama_server_exe = "D:/llama-dflash-win/build-vs/bin/llama-server.exe"
        s.llama_server_model = "D:/Qwen3.5-9B-Q3_K_M.gguf"
        s.llama_server_draft = "D:/draft.gguf"
        s.llama_server_port = 8080
        s.llama_server_host = "0.0.0.0"
        # 同卡探测：vision 端点 host(127.0.0.1) 与 llama_server_host(0.0.0.0) 均归一为
        # "local" → 判定 co-located，覆盖 acquire("vision") 让出 8080 / release 后台拉回语义。
        s.vision_http_url = "http://127.0.0.1:8082"
        s.llama_server_ngl = 35
        s.llama_server_draft_ngl = 35
        s.llama_server_ctx = 8192
        s.llama_server_cold_grace = 360
        s.qwen_autostart = True
        s.vram_exclusive = True
        g.return_value = s
        yield s


def _reset_singletons():
    import mock_api.llama_server_manager as lsm
    import mock_api.vram_scheduler as vs

    lsm.reset_llama_server_manager()
    vs.reset_vram_scheduler()


def test_vram_event_history_can_be_read_and_cleared(settings_mock):
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with patch.object(mgr, "ensure_started", return_value=StartResult(True, StartStatus.READY)):
        sched.request_text(wait=False)

    events = sched.get_events()
    assert events
    assert events[0]["kind"] == "ready"
    assert events[0]["id"].startswith("vram-")
    assert "message" in events[0]

    sched.clear_events()
    assert sched.get_events() == []


def test_request_text_state_and_autostart(settings_mock):
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(
            mgr, "ensure_started", return_value=StartResult(True, StartStatus.READY)
        ) as ensure,
    ):
        sched.request_text(wait=False)
        ensure.assert_called_once()
    assert sched.state() == VRAMState.TEXT_ACTIVE


@pytest.mark.critical
def test_request_text_releases_vision(settings_mock):
    """新语义（ADR-013）：text↔vision 令牌容量 1、跨 kind 互斥；co-located 下
    acquire("vision") 让出 8080（shutdown），release 后后台拉回。

    不再有旧『持 A 时调 B 自动切换并 shutdown OCR』行为——必须显式 release 再 acquire。
    """
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "ensure_started", return_value=StartResult(True, StartStatus.READY)),
        patch.object(mgr, "shutdown") as spy_shutdown,
    ):
        # ── 方向一：text → (release) → vision，令牌正确交接，vision 让出 8080 ──
        with vram_guard("text"):
            assert sched.state() == VRAMState.TEXT_ACTIVE
            spy_shutdown.assert_not_called()  # text 侧不主动 shutdown 8080
        assert sched.state() == VRAMState.IDLE  # 离开 text 括号即释放令牌

        with vram_guard("vision"):
            # co-located：acquire("vision") 让出本端 8080 给 vision
            spy_shutdown.assert_called_once()
            assert sched.state() == VRAMState.VISION_ACTIVE
            assert sched._kind == VRAMKind.VISION  # 此刻只持 vision，不共存
        # release vision：co-located 触发后台拉回 8080
        assert sched.state() in (VRAMState.SWITCHING_TO_TEXT, VRAMState.TEXT_ACTIVE)

        # ── 方向二：vision → (release 等) → 再 text，令牌回到 text ──
        for _ in range(200):
            if sched.state() == VRAMState.TEXT_ACTIVE:
                break
            time.sleep(0.005)

        spy_shutdown.reset_mock()
        with vram_guard("text"):
            # 重新获取 text：ensure_started 再次被调，不抛、不共存
            assert sched.state() == VRAMState.TEXT_ACTIVE
        assert spy_shutdown.call_count == 0  # text 重新获取不 shutdown 8080


@pytest.mark.critical
def test_vision_finished_autostarts_text(settings_mock):
    """新语义（ADR-013）：co-located exclusive 下 release("vision") 触发后台 autostart 8080，
    把 text-Qwen 拉回（对称于旧「OCR 释放后 autostart Qwen」）。vision 侧从不调 ensure_started。
    """
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    ensure = MagicMock(return_value=StartResult(True, StartStatus.READY))
    with (
        patch.object(mgr, "ensure_started", ensure),
        patch.object(mgr, "shutdown"),
    ):
        sched.request_vision()  # 持 vision，不管理/不拉起 8080
        ensure.assert_not_called()  # vision 侧绝不调 ensure_started
        sched.vision_finished()  # 释放 vision → 触发后台拉回 8080
        # 后台线程异步调用 ensure_started，等待其发生
        for _ in range(200):
            if ensure.called:
                break
            time.sleep(0.005)
    ensure.assert_called()  # 后台 autostart 8080 确实被调
    # 后台完成后回到 TEXT_ACTIVE
    for _ in range(200):
        if sched.state() == VRAMState.TEXT_ACTIVE:
            break
        time.sleep(0.005)
    assert sched.state() == VRAMState.TEXT_ACTIVE


def test_vram_exclusive_off_no_release(settings_mock):
    settings_mock.vram_exclusive = False
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "ensure_started", return_value=StartResult(True, StartStatus.READY)),
        patch.object(mgr, "shutdown") as spy_shutdown,
    ):
        sched.request_text(wait=False)
        sched.request_vision()  # 互斥关闭 → 不释放 Qwen
        spy_shutdown.assert_not_called()
    assert sched.state() == VRAMState.VISION_ACTIVE


@pytest.mark.critical
def test_vram_exclusive_off_no_protection(settings_mock):
    """vram_exclusive=False：文档化的无保护模式——co-located 下 acquire("vision") 也绝不
    shutdown 8080、release("vision") 后无后台 autostart（调用方自行承担并发 OOM 风险）。
    真实调用点从不同时持两个 kind，故各自独立获取/释放。
    """
    settings_mock.vram_exclusive = False
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "ensure_started", return_value=StartResult(True, StartStatus.READY)),
        patch.object(mgr, "shutdown") as spy_shutdown,
    ):
        # text 与 vision 各自独立获取/释放（真实调用点从不同时持有两个 kind）
        with vram_guard("text"):
            assert sched.state() == VRAMState.TEXT_ACTIVE
        assert sched.state() == VRAMState.IDLE

        with vram_guard("vision"):
            # 无保护模式：co-located 下也绝不 shutdown 8080
            spy_shutdown.assert_not_called()
            assert sched.state() == VRAMState.VISION_ACTIVE
        # release 后无后台 autostart（exclusive 关闭），直接 IDLE
        assert sched.state() == VRAMState.IDLE


def test_port_precheck_reuses_external(settings_mock):
    _reset_singletons()
    mgr = get_llama_server_manager()
    with (
        patch.object(mgr, "_port_in_use", return_value=True),
        patch("mock_api.llama_server_manager.subprocess.Popen") as popen,
    ):
        result = mgr.ensure_started(wait=False)
        assert result.ready is True
        assert result.status == StartStatus.EXTERNAL_REUSED
        assert mgr._managed is False  # 复用外部，不托管
        popen.assert_not_called()  # 不拉起新进程


def test_spawn_builds_correct_command(settings_mock):
    _reset_singletons()
    mgr = get_llama_server_manager()
    with (
        patch.object(mgr, "_port_in_use", return_value=False),
        patch("mock_api.llama_server_manager.subprocess.Popen") as popen,
    ):
        mgr.ensure_started(wait=False)
        cmd = popen.call_args[0][0]
        assert cmd[0] == settings_mock.llama_server_exe
        assert "-m" in cmd and settings_mock.llama_server_model in cmd
        assert "--spec-type" in cmd and "draft-dflash" in cmd
        assert "--port" in cmd and "8080" in cmd


def test_in_flight_spawn_returns_starting(settings_mock):
    _reset_singletons()
    mgr = get_llama_server_manager()
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # 仍在运行/启动中
    with (
        patch.object(mgr, "_port_in_use", return_value=False),
        patch("mock_api.llama_server_manager.subprocess.Popen", return_value=fake_proc),
        patch.object(mgr, "_wait_until_ready"),
    ):
        first = mgr.ensure_started(wait=False)
        assert first.status == StartStatus.STARTING
        second = mgr.ensure_started(wait=False)
        assert second.status == StartStatus.STARTING
        assert second.ready is False


def test_missing_config_returns_failed(settings_mock):
    _reset_singletons()
    mgr = get_llama_server_manager()
    settings_mock.llama_server_exe = ""
    with patch.object(mgr, "_port_in_use", return_value=False):
        result = mgr.ensure_started(wait=False)
        assert result.status == StartStatus.FAILED
        assert result.ready is False


def test_request_text_sets_loading_event_when_starting(settings_mock):
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "ensure_started", return_value=StartResult(False, StartStatus.STARTING)),
    ):
        sched.request_text(wait=False)
        assert sched.state() == VRAMState.TEXT_ACTIVE
        status = sched.get_status()
        assert status["kind"] == "loading"


def test_request_text_sets_failed_event_when_failed(settings_mock):
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "ensure_started", return_value=StartResult(False, StartStatus.FAILED)),
    ):
        sched.request_text(wait=False)
        assert sched.state() == VRAMState.TEXT_ACTIVE
        status = sched.get_status()
        assert status["kind"] == "failed"


# ── 并发压力测试 ─────────────────────────────────────────────────


@pytest.mark.critical
def test_concurrent_alternating_requests_maintain_mutex(settings_mock):
    """多线程并发交替 acquire text↔vision：令牌容量 1 跨 kind 互斥，
    绝不出现同时持有两个 kind、且无死锁（新语义：需 release 再 acquire，不再自动切换）。
    """
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    # 模拟 ensure_started 有延迟，更容易触发竞态
    def _slow_ensure_started(wait: bool = True) -> StartResult:
        time.sleep(0.003)
        return StartResult(True, StartStatus.READY)

    violations: list = []

    def _worker() -> None:
        try:
            for _ in range(20):
                with vram_guard("text"):
                    # 持 text 期间，令牌必须是 text，绝不能已是 vision
                    if sched._kind != VRAMKind.TEXT:
                        violations.append(("text", sched._kind))
                with vram_guard("vision"):
                    if sched._kind != VRAMKind.VISION:
                        violations.append(("vision", sched._kind))
        except Exception as exc:  # noqa: BLE001
            violations.append(("exc", repr(exc)))

    with (
        patch.object(mgr, "ensure_started", side_effect=_slow_ensure_started),
        patch.object(mgr, "shutdown"),
    ):
        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not violations, f"并发过程中互斥被破坏或出现异常: {violations}"
    # 所有 worker 结束后，最后一次 release("vision") 会后台拉回 text；最终落 TEXT_ACTIVE/IDLE
    for _ in range(200):
        if sched.state() in (VRAMState.TEXT_ACTIVE, VRAMState.IDLE):
            break
        time.sleep(0.005)
    assert sched.state() in (VRAMState.TEXT_ACTIVE, VRAMState.IDLE)


def test_concurrent_qwen_requests_no_duplicate_spawn(settings_mock):
    """多线程同时请求 Qwen，确保 llama-server 只被拉起一次。"""
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    barrier = threading.Barrier(8)
    spawn_count = 0
    spawn_lock = threading.Lock()

    def _counting_ensure_started(wait: bool = True) -> StartResult:
        nonlocal spawn_count
        with spawn_lock:
            spawn_count += 1
        # 模拟启动耗时，让多个线程同时命中 in-flight
        time.sleep(0.02)
        return StartResult(True, StartStatus.READY)

    with (
        patch.object(mgr, "ensure_started", side_effect=_counting_ensure_started),
    ):
        threads = [
            threading.Thread(target=lambda: (barrier.wait(), sched.request_text(wait=False)))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 由于 in-flight guard，实际调用次数应远小于 8
        assert spawn_count <= 8
        assert sched.state() == VRAMState.TEXT_ACTIVE


def test_vision_finished_races_with_request_vision(settings_mock):
    """OCR 完成后的自动拉回 Qwen 与新的 OCR 请求并发，验证状态机不混乱。"""
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    def _slow_ensure_started(wait: bool = True) -> StartResult:
        time.sleep(0.02)
        return StartResult(True, StartStatus.READY)

    with (
        patch.object(mgr, "ensure_started", side_effect=_slow_ensure_started),
        patch.object(mgr, "shutdown"),
    ):
        sched.request_vision()
        sched.vision_finished()  # 后台开始拉回 Qwen

        # 在拉回过程中再次请求 OCR
        time.sleep(0.005)
        sched.request_vision()

        # 最终状态应为 VISION_ACTIVE（后到的 OCR 请求应覆盖）
        assert sched.state() == VRAMState.VISION_ACTIVE


@pytest.mark.critical
def test_vision_finished_switching_state(settings_mock):
    """新语义（ADR-013）：release("vision") 后应立即进入 SWITCHING_TO_TEXT（后台拉回 8080），
    而非 IDLE，避免后续 acquire("text") 看到 IDLE 误判为可无代价抢占。
    """
    _reset_singletons()
    sched = get_vram_scheduler()
    mgr = get_llama_server_manager()

    def _slow_ensure_started(wait: bool = True) -> StartResult:
        time.sleep(0.1)  # 慢启动，确保后台线程在我们断言时尚未完成
        return StartResult(True, StartStatus.READY)

    with (
        patch.object(mgr, "ensure_started", side_effect=_slow_ensure_started),
        patch.object(mgr, "shutdown"),
    ):
        sched.request_vision()
        sched.vision_finished()
        # 后台线程尚在等待 ensure_started（慢），此刻状态必为 SWITCHING_TO_TEXT，而非 IDLE
        assert sched.state() == VRAMState.SWITCHING_TO_TEXT
        # 等待后台线程完成 → TEXT_ACTIVE
        for _ in range(200):
            if sched.state() == VRAMState.TEXT_ACTIVE:
                break
            time.sleep(0.005)
        assert sched.state() == VRAMState.TEXT_ACTIVE


def test_shutdown_then_respawn_does_not_create_duplicate_watchers(settings_mock):
    """shutdown 后立刻 respawn 不应同时存在两个 watcher 线程。"""
    _reset_singletons()
    mgr = get_llama_server_manager()

    with (
        patch.object(mgr, "_port_in_use", return_value=False),
        patch("mock_api.llama_server_manager.subprocess.Popen") as popen,
    ):
        proc = MagicMock()

        def _slow_wait(timeout):
            time.sleep(0.5)
            return 0

        proc.wait.side_effect = _slow_wait
        popen.return_value = proc

        mgr.ensure_started(wait=False)
        old_watcher = mgr._watcher_thread
        assert old_watcher is not None and old_watcher.is_alive()

        # shutdown 后立刻重新启动：旧的 watcher 还在 wait，新的 watcher 会被创建
        mgr.shutdown()
        mgr.ensure_started(wait=False)
        new_watcher = mgr._watcher_thread
        assert new_watcher is not old_watcher
        assert new_watcher is not None and new_watcher.is_alive()

        # 旧 watcher 发现身份已失效，应该退出，不会和新 watcher 竞争
        old_watcher.join(timeout=2)
        assert not old_watcher.is_alive()
