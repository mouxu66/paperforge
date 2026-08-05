"""DEPTH v4.2 scoring metrics — DWM weighted scoring + adaptive delta bounds.

Extracted from DepthReviewer to keep the review engine lean.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..depth_eval_v4 import (
        CritiquePoint,
        Q1Result,
        Q2Result,
        Q3Result,
        Q4Result,
        QFResult,
    )

from ..depth_prompts_v4 import ELASTIC_THRESHOLD, TYPE_WEIGHTS
from ..utils.validators import _clamp_float


def _adaptive_delta_bounds(
    balanced_cp: list[CritiquePoint],
    default_min: float,
    default_max: float,
    *,
    delta_adaptive_enabled: bool = True,
    delta_hard_min: float = -0.25,
    delta_hard_max: float = 0.25,
) -> tuple[float, float]:
    """根据平衡后质疑的严重度分布，计算本次 Q5c 允许的 delta 区间。

    规则（delta_adaptive_enabled=False 时恒为 [default_min, default_max]）：
    - 存活 fatal 越多 → 向下修正空间越大（每个 fatal -0.06）；
    - 存活 minor 超过 1 条后每条再补 -0.02；
    - 无任何存活质疑（干净论文）→ 向上放宽到 +0.20；
    - 全局硬上限 [delta_hard_min, delta_hard_max] 永不被突破。
    """
    if not delta_adaptive_enabled:
        return default_min, default_max
    fatal_n = sum(1 for cp in balanced_cp if getattr(cp, "severity", "") == "fatal")
    minor_n = sum(1 for cp in balanced_cp if getattr(cp, "severity", "") == "minor")
    lo = default_min - min(0.17, 0.06 * fatal_n + 0.02 * max(0, minor_n - 1))
    hi = default_max + (0.08 if fatal_n == 0 and minor_n == 0 else 0.0)
    return max(delta_hard_min, lo), min(delta_hard_max, hi)


def _compute_dwm(
    q1: Q1Result,
    q2: Q2Result,
    q3: Q3Result,
    q4: Q4Result,
    objective_score: float | None = None,
    qf: QFResult | None = None,
    *,
    oim_weight: float = 0.0,
    depth_figure_weight: float = 0.0,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[float, float, dict[str, float]]:
    """返回 (base_score, final_base_score, weights)。

    P2-1: hotspot_alignment_score 不再参与 DWM 计分。
    P0-1: 当 oim_weight>0 且 objective_score 有效时，
          final_base = (1-w)*base_score + w*objective_score。
    v4.2 #6: 弹性权重改平滑插值。
    v4.2 #1深: QF 图文一致性对 final_base 做有界微调。
    """
    base = TYPE_WEIGHTS.get(q1.type, TYPE_WEIGHTS["B"])
    if q1.confidence >= ELASTIC_THRESHOLD:
        weights = dict(base)
    else:
        others = [t for t in TYPE_WEIGHTS if t != q1.type]
        t = min(1.0, q1.confidence / ELASTIC_THRESHOLD) if ELASTIC_THRESHOLD > 0 else 1.0
        weights = {}
        for dim in base:
            other_avg = sum(TYPE_WEIGHTS[ot][dim] for ot in others) / len(others)
            weights[dim] = t * base[dim] + (1.0 - t) * other_avg

    base_score = (
        weights["beta"] * q2.novelty_score
        + weights["gamma"] * q3.rigor_score
        + weights["delta"] * q4.influence_score
        + weights["epsilon"] * q4.reproducibility_score
    )
    base_score = _clamp_float(base_score)

    # P0-1 OIM 合并
    final_base = base_score
    if objective_score is not None and oim_weight > 0:
        w = min(max(oim_weight, 0.0), 1.0)
        final_base = (1.0 - w) * base_score + w * objective_score
        final_base = _clamp_float(final_base)
        if log_fn is not None:
            log_fn(
                f"OIM: w={w:.2f}, objective={objective_score:.3f}, "
                f"base={base_score:.3f} -> final_base={final_base:.3f}"
            )

    # v4.2 QF 图文一致性微调
    if qf is not None and getattr(qf, "has_figures", False) and depth_figure_weight > 0:
        w_fig = min(max(depth_figure_weight, 0.0), 0.5)
        adjusted = final_base + w_fig * (qf.figure_consistency_score - 0.5)
        adjusted = _clamp_float(adjusted)
        if log_fn is not None:
            log_fn(
                f"QF 微调: w={w_fig:.2f}, figure_consistency={qf.figure_consistency_score:.3f}, "
                f"claim_validation_penalty={getattr(qf, 'claim_validation_penalty', 0.0):.3f}, "
                f"claim_validation_bonus={getattr(qf, 'claim_validation_bonus', 0.0):.3f}, "
                f"final_base {final_base:.3f} -> {adjusted:.3f}"
            )
        final_base = adjusted

    return base_score, final_base, weights
