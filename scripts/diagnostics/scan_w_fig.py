"""B 方案：在 70 篇有 QF 信号的 PeerRead 样本上模拟 w_fig 扫描。

关键发现：
- P0-1 文本层兜底路径 has_figures=False（depth_eval_v4.py:1977），
  导致 qf.has_figures 判断为 False，QF 微调 (depth_eval_v4.py:2931) 完全不生效。
- 当前 70/198 篇有非 0.5 QF 信号，但信号被丢弃。

本脚本模拟：如果把这 70 篇的 QF 信号"激活"（has_figures=True），
不同 w_fig 下 κ / acc / FP / FN 如何变化。

公式（depth_eval_v4.py:2933）：
    final_base = final_base + w_fig * (qf.figure_consistency_score - 0.5)

输入：
  - deliverables/baseline_fig_pilot.jsonl （含 depth_base_score / human_accepted）
  - deliverables/p0_text_evidence_diagnose.jsonl （含 score）

baseline_fig_pilot 用的是 calibrated_score（已含 +0.18 偏移），
我们要在 base_score 上做 w_fig 模拟，再加 +0.18 偏移，与报告口径对齐。

固定阈值 0.80（与 offset_scan.py 一致）。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cohen_kappa_binary(pred, truth):
    n = len(pred)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(pred, truth) if x == y) / n
    pa = sum(pred) / n
    pb = sum(truth) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def load_baseline(path: Path) -> dict[str, dict]:
    """stem -> {base_score, calibrated_score, human_accepted}"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            stem = o.get("stem")
            if not stem:
                continue
            out[stem] = {
                "base_score": float(o["depth_base_score"]),
                "calibrated_score": float(o["depth_calibrated_score"]),
                "human_accepted": bool(o["human_accepted"]),
            }
    return out


def load_qf(path: Path) -> dict[str, float]:
    """stem -> qf_score（仅含有 evidences 的样本）"""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if not o.get("ok"):
                continue
            if o.get("evidences", 0) == 0:
                continue
            out[o["stem"]] = float(o["score"])
    return out


def simulate(base_scores: dict[str, float], qf_scores: dict[str, float],
             humans: dict[str, bool], w_fig: float, delta: float = 0.18,
             threshold: float = 0.80) -> dict:
    """模拟 final_base + w_fig*(qf-0.5) + delta 后的二值分类指标。"""
    pred, truth = [], []
    n_with_qf = 0
    n_total = 0
    for stem, base in base_scores.items():
        n_total += 1
        human = humans[stem]
        truth.append(1 if human else 0)
        if stem in qf_scores:
            n_with_qf += 1
            qf = qf_scores[stem]
            adj_base = base + w_fig * (qf - 0.5)
        else:
            adj_base = base
        final = min(adj_base + delta, 1.0)
        pred.append(1 if final >= threshold else 0)
    tp = sum(1 for x, y in zip(pred, truth) if x == 1 and y == 1)
    fp = sum(1 for x, y in zip(pred, truth) if x == 1 and y == 0)
    fn = sum(1 for x, y in zip(pred, truth) if x == 0 and y == 1)
    tn = sum(1 for x, y in zip(pred, truth) if x == 0 and y == 0)
    acc = (tp + tn) / n_total if n_total else 0.0
    k = cohen_kappa_binary(pred, truth)
    return {
        "w_fig": w_fig,
        "delta": delta,
        "n_total": n_total,
        "n_with_qf": n_with_qf,
        "kappa": round(k, 4),
        "acc": round(acc, 4),
        "accept_pred": sum(pred),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def main():
    baseline = load_baseline(ROOT / "deliverables" / "baseline_fig_pilot.jsonl")
    qf = load_qf(ROOT / "deliverables" / "p0_text_evidence_diagnose.jsonl")

    # 仅保留同时有 baseline 和 qf 的 stem
    base_scores = {s: v["base_score"] for s, v in baseline.items()}
    humans = {s: v["human_accepted"] for s, v in baseline.items()}
    # 限制 qf 只用 baseline 里的 stem（避免 baseline 之外的污染）
    qf_in_baseline = {s: v for s, v in qf.items() if s in baseline}

    print("=" * 78)
    print("B 方案：w_fig 扫描（在 200 篇 baseline 上，激活 70 篇 QF 信号）")
    print("=" * 78)
    print(f"  baseline N = {len(baseline)}")
    print(f"  QF 信号 N = {len(qf_in_baseline)}  (在 baseline 中的)")
    print(f"  公式: final = min(base + w_fig*(qf-0.5) + 0.18, 1.0), threshold=0.80")
    print(f"  当前生产配置: w_fig=0.1, 但 has_figures=False → QF 实际未生效")
    print()

    # 对照组：w_fig=0（即当前实际行为，QF 信号被丢弃）
    print(f"{'w_fig':>6} {'κ':>7} {'acc':>7} {'accept#':>8} {'FP':>4} {'FN':>4} {'QF激活N':>8}")
    print("-" * 60)
    for w_fig in [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        r = simulate(base_scores, qf_in_baseline, humans, w_fig)
        marker = " ← 当前" if w_fig == 0.0 else (" ← 生产配置" if w_fig == 0.1 else "")
        print(
            f"{r['w_fig']:>6.2f} {r['kappa']:>7.3f} {r['acc']:>7.3f} "
            f"{r['accept_pred']:>8} {r['fp']:>4} {r['fn']:>4} "
            f"{r['n_with_qf']:>8}{marker}"
        )

    # w_fig=0 vs w_fig=0.5 的细节
    print()
    print("对照 w_fig=0 vs w_fig=0.5（Δ 固定 +0.18）：")
    r0 = simulate(base_scores, qf_in_baseline, humans, 0.0)
    r5 = simulate(base_scores, qf_in_baseline, humans, 0.5)
    print(f"  w_fig=0.0: κ={r0['kappa']:.3f}, acc={r0['acc']:.3f}, FP={r0['fp']}, FN={r0['fn']}")
    print(f"  w_fig=0.5: κ={r5['kappa']:.3f}, acc={r5['acc']:.3f}, FP={r5['fp']}, FN={r5['fn']}")
    print(f"  Δκ = {r5['kappa'] - r0['kappa']:+.3f}, Δacc = {r5['acc'] - r0['acc']:+.3f}")

    # 仅在 70 篇有 QF 信号上单独看效果（剔除 130 篇无信号的"对照组"）
    print()
    print("=" * 78)
    print("仅 70 篇有 QF 信号的子集（更纯的对照，剔除无 QF 信号的 130 篇噪声）")
    print("=" * 78)
    base_70 = {s: base_scores[s] for s in qf_in_baseline}
    human_70 = {s: humans[s] for s in qf_in_baseline}
    print(f"  N = {len(base_70)}, accepted = {sum(human_70.values())}")
    print()
    print(f"{'w_fig':>6} {'κ':>7} {'acc':>7} {'accept#':>8} {'FP':>4} {'FN':>4}")
    print("-" * 50)
    for w_fig in [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        r = simulate(base_70, qf_in_baseline, human_70, w_fig)
        print(
            f"{r['w_fig']:>6.2f} {r['kappa']:>7.3f} {r['acc']:>7.3f} "
            f"{r['accept_pred']:>8} {r['fp']:>4} {r['fn']:>4}"
        )

    # 也扫描 Δ，看 w_fig=0.3 时最优 Δ 是否变化
    print()
    print("=" * 78)
    print("w_fig=0.3 时 Δ 扫描（看最优偏移是否需要调整）")
    print("=" * 78)
    print(f"{'Δ':>6} {'κ':>7} {'acc':>7} {'FP':>4} {'FN':>4}")
    print("-" * 40)
    best = (0.0, None)
    for i in range(51):
        d = round(i * 0.01, 2)
        r = simulate(base_scores, qf_in_baseline, humans, 0.3, delta=d)
        if r["kappa"] > best[0]:
            best = (r["kappa"], d)
        if d in (0.0, 0.10, 0.15, 0.18, 0.20, 0.25, 0.30, 0.40, 0.50):
            print(f"{d:>6.2f} {r['kappa']:>7.3f} {r['acc']:>7.3f} {r['fp']:>4} {r['fn']:>4}")
    print(f"  最优 Δ = {best[1]:.2f}, κ = {best[0]:.3f}")
    print()
    print("（对照：w_fig=0 时最优 Δ=0.18, κ=0.414）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
