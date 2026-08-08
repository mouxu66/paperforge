"""Bootstrap 置信区间与不确定门控（ADR-014 · P2）。

解决 W4：DEPTH 此前只给一个**点估计**分数，没有「这个分数有多可靠」的概念。
本模块提供：

1. ``bootstrap_ci``：对一组分数做有放回重采样，给出 95% 置信区间（默认 1000 次）。
2. ``uncertainty_gate``：基于置信区间宽度判定 verdict 是否「不确定到需要人工复核」。

fail-open：样本不足 / 方差为 0 / 异常时返回 ``status="no_data"`` 或 ``"error"``，
绝不因统计计算失败改变原有的 verdict。

设计原则：置信区间仅作为**附加信号**（advisory），不直接覆写 DEPTH 的硬裁决，
除非显式开启 ``PAPERFORGE_UNCERTAINTY_GATE=1`` 且 CI 过宽 → 把 verdict 标为
``needs_human_review``（不自动 accept/reject，把判断权交还人类）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

# 默认 95% 置信区间
_DEFAULT_ALPHA = 0.05


def _percentile(sorted_vals: list[float], q: float) -> float:
    """在已排序列表上线性插值取 q 分位（q∈[0,1]）。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * max(0.0, min(1.0, q))
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_boot: int = 1000,
    alpha: float = _DEFAULT_ALPHA,
    seed: int | None = 0,
) -> tuple[float, float]:
    """对 values 做 bootstrap 重采样，返回 (lo, hi) 置信区间（默认 95%）。

    Args:
        values: 单次评分的「子分数样本」（如各维度分数、或多评审员分数）。
                至少需 2 个值才有意义；不足 2 个返回 (NaN, NaN) 表示无数据。
        n_boot: 重采样次数。
        alpha: 显著性水平，默认 0.05 → 95% CI。
        seed: 随机种子（默认 0，保证 CI 可复现）；None 表示不固定。

    Returns:
        (ci_low, ci_high)，均为 [0,1] 区间的截断值。
    """
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    lo_q = alpha / 2.0
    hi_q = 1.0 - alpha / 2.0
    means: list[float] = []
    n = len(vals)
    for _ in range(n_boot):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    ci_lo = _percentile(means, lo_q)
    ci_hi = _percentile(means, hi_q)
    ci_lo = max(0.0, min(1.0, ci_lo))
    ci_hi = max(0.0, min(1.0, ci_hi))
    return (ci_lo, ci_hi)


@dataclass
class UncertaintyResult:
    """不确定门控结果。"""

    score: float = 0.0
    ci_low: float = float("nan")
    ci_high: float = float("nan")
    ci_width: float = float("nan")
    status: str = "no_data"  # ok | needs_human_review | no_data | error
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "ci_low": None if math.isnan(self.ci_low) else round(self.ci_low, 4),
            "ci_high": None if math.isnan(self.ci_high) else round(self.ci_high, 4),
            "ci_width": None if math.isnan(self.ci_width) else round(self.ci_width, 4),
            "status": self.status,
            "note": self.note,
        }


def uncertainty_gate(
    score: float,
    values: Sequence[float] | None = None,
    *,
    ci: tuple[float, float] | None = None,
    width_threshold: float = 0.15,
    gate_enabled: bool = False,
) -> UncertaintyResult:
    """计算分数置信区间并判定不确定性。

    Args:
        score: 待评估的主分数（如 calibrated_score）。
        values: 用于 bootstrap 的子分数样本；若提供则内部算 CI。
        ci: 已算好的 (lo, hi)，优先于 values。
        width_threshold: CI 宽度超过此值视为「不确定」。
        gate_enabled: True 时 CI 过宽 → status="needs_human_review"；
                     False 时仅报告，不改变 verdict（纯 advisory）。

    Returns:
        UncertaintyResult（fail-open）。
    """
    res = UncertaintyResult(score=float(score))
    try:
        if ci is None:
            if not values:
                res.status = "no_data"
                res.note = "无子分数样本，无法估计置信区间"
                return res
            ci = bootstrap_ci(values)
        res.ci_low, res.ci_high = ci
        if math.isnan(res.ci_low):
            res.status = "no_data"
            res.note = "样本不足，无法估计置信区间"
            return res
        res.ci_width = res.ci_high - res.ci_low
        if res.ci_width > width_threshold:
            res.status = "needs_human_review" if gate_enabled else "ok"
            res.note = (
                f"置信区间过宽({res.ci_width:.2f})，建议人工复核"
                if gate_enabled
                else f"置信区间较宽({res.ci_width:.2f})，仅供参考"
            )
        else:
            res.status = "ok"
            res.note = "置信区间在可接受范围"
        return res
    except Exception as e:  # noqa: BLE001
        res.status = "error"
        res.note = f"不确定门控异常: {type(e).__name__}"
        return res
