"""DEPTH v3 向后兼容 shim。

DEPTH 评估逻辑已彻底合并到 ``mock_api.depth_eval_v4``（DepthReviewer + v3 兼容层
``evaluate_paper`` / ``get_cached_score``）。本模块保留仅为兼容外部脚本 ``from
mock_api.depth_eval import ...`` 的写法。

【迁移建议】
- 新代码请直接 ``from mock_api.depth_eval_v4 import evaluate_paper, get_cached_score``。
- ``mock_api.depth_eval_v4.DepthReviewer`` 是推荐的 v4.1 入口，提供更丰富的
  证据池 / 自洽采样 / 硬 verdict 兜底等能力。
- 本 shim 仅做轻量 re-export，不再维护独立实现。
"""

from __future__ import annotations

from .depth_eval_v4 import evaluate_paper, get_cached_score

__all__ = ["evaluate_paper", "get_cached_score"]
