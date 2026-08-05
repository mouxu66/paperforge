"""scorers 子模块 —— v4.2 从 depth_eval_v4.py 拆分出的评分/计算函数。

转发导入以保持 depth_eval_v4.* 的外部兼容性。
"""

from .legacy import _score_text_figure_consistency  # noqa: F401
from .metrics import _adaptive_delta_bounds, _compute_dwm  # noqa: F401

__all__ = [
    "_score_text_figure_consistency",
    "_adaptive_delta_bounds",
    "_compute_dwm",
]
