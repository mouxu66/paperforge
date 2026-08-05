"""测试 compute_mode 安全网（Windows Store Python → 零值兜底）与 win32 默认禁检测。

这些测试可在任意 CI（Linux / macOS）上复跑：通过 monkeypatch 模拟
sys.platform == "win32" 且 sys.base_prefix 含 "WindowsApps"，从而触发
compute_mode.detect_resources() 的「层级 2」安全网，验证其不会触碰任何原生
调用（subprocess / psutil / ctypes）并返回全零 SystemResources —— 这正是
SEV-2→SEV-3 事故根因修复的回归防护。
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from mock_api import compute_mode, config
from mock_api.settings import reset_settings

# 模拟 WindowsApps Store 版 Python 解释器的 base_prefix（真实路径形态）
_WINDOWS_APPS_PREFIX = (
    r"C:\Users\test\AppData\Local\Microsoft\WindowsApps"
    r"\PythonSoftwareFoundation\Python3.13"
)


@pytest.fixture(autouse=True)
def _reset_modules():
    """每个测试前后重置 settings 缓存与 config 动态预设缓存，避免状态泄漏。"""
    reset_settings()
    config._dynamic_preset = None
    config._cached_resources = None
    yield
    reset_settings()
    config._dynamic_preset = None
    config._cached_resources = None


def test_detect_resources_windowsapps_returns_zero_without_native_call(monkeypatch):
    """层级 2 安全网：WindowsApps 解释器下 detect_resources() 返回全零，
    且不调用任何原生检测函数（subprocess / psutil / ctypes）。"""
    monkeypatch.setenv("PAPERFORGE_ENABLE_RESOURCE_DETECT", "1")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "base_prefix", _WINDOWS_APPS_PREFIX)
    reset_settings()

    # 监控原生检测函数是否被调用 —— 安全网必须在触碰它们之前返回
    with patch.object(compute_mode, "detect_gpu_memory") as mock_gpu, \
         patch.object(compute_mode, "detect_system_memory") as mock_ram:
        res = compute_mode.detect_resources()

    mock_gpu.assert_not_called()
    mock_ram.assert_not_called()

    # 全零兜底：GPU 与系统内存字段均为 0
    assert res.gpu_vram_mb == 0
    assert res.gpu_vram_total_mb == 0
    assert res.system_ram_mb == 0
    assert res.system_ram_total_mb == 0
    assert res.gpu_count == 0
    assert res.detected_at  # 时间戳已填充


def test_get_compute_mode_config_windowsapps_does_not_crash(monkeypatch):
    """端到端：WindowsApps 解释器 + 显式启用检测时，get_compute_mode_config()
    不应崩溃 / segfault，并返回合法配置字典（资源为零 → 解析到最低预设）。"""
    monkeypatch.setenv("PAPERFORGE_ENABLE_RESOURCE_DETECT", "1")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "base_prefix", _WINDOWS_APPS_PREFIX)
    reset_settings()

    cfg = config.get_compute_mode_config()  # 不应抛异常 / 不应 segfault

    assert isinstance(cfg, dict)
    # get_compute_mode_config 仅合并特定字段（不含 id / name）
    for key in ("temperature", "max_tokens", "parallel", "ctx_size",
                "max_chars_full", "max_chars_short", "paged_attn", "backend"):
        assert key in cfg

    # 资源为零 → 解析到最低预设；与 resolve_preset(零资源) 一致（避免硬编码预设值）
    expected = compute_mode.resolve_preset(compute_mode.SystemResources())
    assert cfg["temperature"] == expected.temperature
    assert cfg["max_tokens"] == expected.max_tokens
    assert cfg["ctx_size"] == expected.ctx_size


def test_get_dynamic_preset_win32_default_disabled_returns_none(monkeypatch):
    """win32 默认未显式启用 → get_dynamic_preset() 返回 None（config.py:191-194
    默认禁检测，不触碰原生调用）。"""
    monkeypatch.delenv("PAPERFORGE_ENABLE_RESOURCE_DETECT", raising=False)
    monkeypatch.delenv("PAPERFORGE_DISABLE_RESOURCE_DETECT", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    reset_settings()

    preset = config.get_dynamic_preset()
    assert preset is None



# ---------------------------------------------------------------------------
# P2-3: compute_mode fast accepted + unknown rejected
# ---------------------------------------------------------------------------
def test_compute_mode_fast_accepted_via_settings(monkeypatch):
    """Settings(compute_mode='fast') is accepted by the validator."""
    from mock_api.settings import Settings, reset_settings

    monkeypatch.setenv("PAPERFORGE_COMPUTE_MODE", "fast")
    reset_settings()
    s = Settings()
    assert s.compute_mode == "fast"


def test_compute_mode_speed_still_accepted_as_legacy_alias(monkeypatch):
    """Settings(compute_mode='speed') still works (legacy users)."""
    from mock_api.settings import Settings, reset_settings

    monkeypatch.setenv("PAPERFORGE_COMPUTE_MODE", "speed")
    reset_settings()
    s = Settings()
    assert s.compute_mode == "speed"


def test_compute_mode_unknown_rejected(monkeypatch):
    """Settings(compute_mode='boost') raises ValueError."""
    import pytest
    from mock_api.settings import Settings, reset_settings

    monkeypatch.setenv("PAPERFORGE_COMPUTE_MODE", "boost")
    reset_settings()
    with pytest.raises(ValueError):
        Settings()


def test_llm_cache_backend_default_inprocess(monkeypatch):
    """Settings.llm_cache_backend defaults to 'inprocess'."""
    from mock_api.settings import Settings, reset_settings

    monkeypatch.delenv("PAPERFORGE_LLM_CACHE_BACKEND", raising=False)
    reset_settings()
    s = Settings()
    assert s.llm_cache_backend == "inprocess"
