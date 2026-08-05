"""utils/validators.py —— v4.2 从 depth_eval_v4.py 拆分的校验/钳制函数。

迁移函数：
- _clamp_float
- _cap_reproducibility
"""

from __future__ import annotations

from typing import Any


def _clamp_float(val: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return (lo + hi) / 2
    return max(lo, min(hi, f))


def _cap_reproducibility(repro: float, full_text: str) -> tuple[float, bool]:
    """缺发布确定性封顶。

    当可复现性评分偏高（>0.6）但正文无任何代码/数据/权重公开获取方式时，
    封顶到 0.6。
    """
    from ..extractors.evidence import _detect_code_release

    repro = _clamp_float(repro)
    if repro > 0.6 and not _detect_code_release(full_text):
        return 0.6, True
    return repro, False
