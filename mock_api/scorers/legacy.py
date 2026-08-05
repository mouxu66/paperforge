"""scorers/legacy.py —— v4.2 从 depth_eval_v4.py 迁出的遗留评分函数。

迁移函数：
- _score_text_figure_consistency（已弃用，仅保留供参考/复现）
"""

from __future__ import annotations

from typing import Any


def _score_text_figure_consistency(
    evidences: list[dict[str, Any]],
) -> tuple[float, str, list[str]]:
    """[DEPRECATED] P0-B 文本层图文一致性评分。

    该函数已被证明无判别力（PeerRead/盲评两个数据集开门即 FP 飙升），
    自 v4.2 起不再被生产路径调用，仅保留代码供参考/复现。
    调用方在无 PaperFigure 时应直接返回中性 0.5，避免 regex IO。
    """
    per_caption: list[float] = []
    flags: list[str] = []
    total_nums = 0
    for ev in evidences:
        nums = ev.get("body_matches") or []
        if not nums:
            continue
        total_nums += len(nums)
        found = sum(1 for x in nums if x.get("found"))
        ratio = found / len(nums)
        per_caption.append(ratio)
        missing = [str(x["value"]) for x in nums if not x.get("found")]
        if missing:
            flags.append(f"cap{ev.get('figure_number')}_num_missing:{','.join(missing[:3])}")
    if not per_caption:
        return 0.5, "无可用图注数值断言", []
    avg = sum(per_caption) / len(per_caption)
    # 对称评分：全命中→0.95（+0.45），全不命中→0.05（-0.45），avg=0.5→0.5（中性）
    score = 0.05 + 0.9 * avg
    found_total = sum(1 for ev in evidences for x in ev.get("body_matches", []) if x.get("found"))
    reasoning = (
        f"文本层图文一致性（无渲染图）：{len(evidences)} 个图注含 {total_nums} 个数值断言，"
        f"{found_total}/{total_nums} 在正文其余部分命中（按图注平均一致率 {avg:.0%}）"
    )
    return round(score, 3), reasoning, flags[:5]
