"""运行时参数覆盖（runtime overrides）—— 用于在线 A/B 调优。

PaperForge 启动时会从环境变量 / .env 加载 settings.py 中的默认值。
运营同学可以通过 /api/depth/settings 端点在线覆盖部分 DEPTH 参数，
新值会保存在本模块的进程级字典中，立即对后续 DEPTH 审稿生效（无需重启、无需改 env）。

注意：
- 运行时覆盖仅在当前进程生效；多进程部署时每个进程各自维护一份。
- 进程重启后覆盖会丢失，如需持久化请通过 .env 或配置中心。
"""

from __future__ import annotations

import threading
from typing import Any

# 当前支持的 DEPTH A/B 可调参数。
# key 与 settings.py 中的字段名保持一致，便于统一处理。
_SUPPORTED_DEPTH_PARAMS: set[str] = {
    "depth_severity_fallback_threshold",
    "depth_severity_fatal_weight",
    "depth_severity_minor_weight",
    "depth_q5c_claim_severity_factor",
    "depth_claim_validation_penalty_per_claim",
    "depth_claim_validation_penalty_max",
    "depth_delta_default_min",
    "depth_delta_default_max",
    "depth_delta_bounds_overrides",
}

_runtime_overrides: dict[str, Any] = {}
_runtime_overrides_lock = threading.Lock()


def get_runtime_override(name: str, default: Any = None) -> Any:
    """获取某个参数的运行时覆盖值；不存在时返回 default。"""
    with _runtime_overrides_lock:
        return _runtime_overrides.get(name, default)


def get_all_runtime_overrides() -> dict[str, Any]:
    """返回所有运行时覆盖值的只读快照。"""
    with _runtime_overrides_lock:
        return dict(_runtime_overrides)


def set_runtime_overrides(updates: dict[str, Any]) -> None:
    """批量设置运行时覆盖值。

    只接受 _SUPPORTED_DEPTH_PARAMS 中声明的参数，其他 key 会被静默忽略。
    """
    with _runtime_overrides_lock:
        for key, value in updates.items():
            if key in _SUPPORTED_DEPTH_PARAMS:
                _runtime_overrides[key] = value


def reset_runtime_overrides() -> None:
    """清空所有运行时覆盖。"""
    with _runtime_overrides_lock:
        _runtime_overrides.clear()


def get_supported_depth_params() -> set[str]:
    """返回支持的 DEPTH 可调参数集合。"""
    return set(_SUPPORTED_DEPTH_PARAMS)
