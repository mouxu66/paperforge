"""DEPRECATED since v4.2 — 内容已合并到 compute_mode.py。

本文件仅为向后兼容保留，所有功能已迁移至:
    from .compute_mode import (
        get_compute_mode_config, get_delta_bounds,
        get_depth_severity_fallback_threshold, ...
    )

v5.0 将彻底移除本文件。新代码请直接从 compute_mode.py 导入。
"""

from __future__ import annotations

# Re-export everything from compute_mode for backward compatibility
from .compute_mode import *  # noqa: F403, E402
