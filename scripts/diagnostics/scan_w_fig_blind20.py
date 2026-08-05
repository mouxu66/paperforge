"""B+ 方案：在 20 篇独立盲评上做 w_fig 扫描。

关键背景（已修正）：
- 415 池批量设 DISABLE_FIGURE_TRIGGER=1，但 DEPTH_FIGURE_EVIDENCE_ENABLED=True（默认）
- 然而 415 池 qf 100% 是 P0-B 文本层路径（离散值分布证实），has_figures=False
- 合并门（depth_eval_v4.py:2931）要求 has_figures=True → 门关 → QF 微调从未生效
- 所以 calibrated_score 不含任何 QF 微调

修正后的公式：
  base_no_qf = calibrated_score（无需反推，门关着，QF 没进 final_base）
  new_final = base_no_qf + w_fig * (qf - 0.5) + offset

  w_fig=0 就是当前实际状态（门关，QF 不生效）
  w_fig>0 是"如果开门让 P0-B 信号进合并"的模拟

口径（与报告 §8.4 对齐）：
  - 用分数层 verdict（不含 fatal_veto），跟 my_verdict 比
  - 4 档：accept≥0.80 / minor≥0.70 / major≥0.50 / reject<0.50
  - 3 篇 fatal_veto 论文（2606.27060 / 2606.27294 / 2402.09353）的分数层 verdict 不变
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 3 篇 fatal_veto 论文（报告 §8.4）—— 分数层口径下不应用 veto 覆盖
FATAL_VETO_PIDS = {"2606.27060", "2606.27294", "2402.09353"}

# 4 档阈值
def verdict_from_score(s: float) -> str:
    if s >= 0.80:
        return "accept"
    if s >= 0.70:
        return "minor_revision"
    if s >= 0.50:
        return "major_revision"
    return "reject"


def cohen_kappa_multiclass(pred: list[str], truth: list[str]) -> float:
    """4 档 κ（unweighted）。"""
    n = len(pred)
    if n == 0:
        return float("nan")
    labels = sorted(set(pred) | set(truth))
    po = sum(1 for x, y in zip(pred, truth) if x == y) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x in pred if x == lab) / n
        pb = sum(1 for x in truth if x == lab) / n
        pe += pa * pb
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def main():
    pool = json.load(open(ROOT / "deliverables" / "depth_scores_415.json", encoding="utf-8"))
    my = json.load(open(ROOT / "calib_papers" / "runs" / "calib_my_review.json", encoding="utf-8"))

    pool_map = {p["id"]: p for p in pool}

    # 仅取盲评 20 篇
    rows = []
    for r in my:
        pid = r["pid"]
        if pid not in pool_map:
            continue
        p = pool_map[pid]
        rows.append({
            "pid": pid,
            "qf": float(p["qf"]),
            "calibrated": float(p["calibrated_score"]),
            "depth_verdict": p["verdict"],  # 全系统 verdict（含 fatal_veto）
            "my_verdict": r["my_verdict"],
            "my_final": float(r["my_final"]),
            "is_fatal_veto": pid in FATAL_VETO_PIDS,
        })

    n = len(rows)
    print("=" * 78)
    print(f"B+ 方案：20 篇独立盲评 w_fig 扫描（n={n}）")
    print("=" * 78)
    print(f"  公式: new_final = calibrated + w_fig*(qf-0.5) + offset")
    print(f"  w_fig=0 = 当前实际状态（门关，QF 不生效）")
    print(f"  w_fig>0 = 模拟开门让 P0-B 信号进合并")
    print(f"  4 档阈值: accept≥0.80 / minor≥0.70 / major≥0.50 / reject<0.50")
    print(f"  口径: 分数层 verdict（不应用 fatal_veto 覆盖），跟 my_verdict 比")
    print(f"  3 篇 fatal_veto 论文分数层 verdict 不变（与报告 §8.4 一致）")
    print()

    # 修正：calibrated 不含 QF 微调（门关），base_no_qf = calibrated
    for r in rows:
        r["base_no_qf"] = r["calibrated"]

    # 看 base 分布
    print("base_no_qf = calibrated 分布（n=20，门关，无 QF 微调）:")
    bases = [r["base_no_qf"] for r in rows]
    print(f"  mean={sum(bases)/n:.3f}  min={min(bases):.3f}  max={max(bases):.3f}")
    print()

    # 固定 offset=-0.09（报告最优值），扫描 w_fig
    OFFSET = -0.09
    print(f"固定 offset={OFFSET}, 扫描 w_fig:")
    print(f"{'w_fig':>6} {'κ(4档)':>8} {'acc':>6} {'accept':>7} {'minor':>6} {'major':>7} {'reject':>7} {'一致':>5}")
    print("-" * 70)
    best = (float("-inf"), None, None)
    for w_fig_x10 in range(0, 21):
        w_fig = w_fig_x10 * 0.1
        pred, truth = [], []
        for r in rows:
            new_final = r["base_no_qf"] + w_fig * (r["qf"] - 0.5) + OFFSET
            new_final = max(0.0, min(1.0, new_final))
            # 分数层 verdict（不应用 fatal_veto）
            v = verdict_from_score(new_final)
            pred.append(v)
            truth.append(r["my_verdict"])
        k = cohen_kappa_multiclass(pred, truth)
        acc = sum(1 for x, y in zip(pred, truth) if x == y) / n
        from collections import Counter
        pc = Counter(pred)
        match = sum(1 for x, y in zip(pred, truth) if x == y)
        marker = ""
        if w_fig == 0.0:
            marker = " ← QF 关闭（对照）"
        elif w_fig == 0.1:
            marker = " ← 当前生产"
        elif w_fig == 0.5:
            marker = " ← 大权重测试"
        print(
            f"{w_fig:>6.2f} {k:>8.3f} {acc:>6.2f} "
            f"{pc.get('accept', 0):>7} {pc.get('minor_revision', 0):>6} "
            f"{pc.get('major_revision', 0):>7} {pc.get('reject', 0):>7} "
            f"{match:>3}/{n}{marker}"
        )
        if k > best[0]:
            best = (k, w_fig, acc)

    print()
    print(f"最优 w_fig = {best[1]:.2f}, κ = {best[0]:.3f}, acc = {best[2]:.2f}")
    print(f"对照：w_fig=0.0（当前实际，QF 门关）→ 报告 §8.3 offset=-0.09 时 κ=0.375")

    # 联合扫描 w_fig × offset
    print()
    print("=" * 78)
    print("联合扫描 w_fig × offset（找最优组合）")
    print("=" * 78)
    print(f"{'offset':>7} {'w_fig':>6} {'κ':>7} {'acc':>6} {'一致':>6}")
    print("-" * 45)
    best_joint = (float("-inf"), None, None)
    for off_x100 in range(-15, 5):
        offset = off_x100 * 0.01
        for w_fig_x10 in range(0, 11):
            w_fig = w_fig_x10 * 0.1
            pred, truth = [], []
            for r in rows:
                new_final = r["base_no_qf"] + w_fig * (r["qf"] - 0.5) + offset
                new_final = max(0.0, min(1.0, new_final))
                v = verdict_from_score(new_final)
                pred.append(v)
                truth.append(r["my_verdict"])
            k = cohen_kappa_multiclass(pred, truth)
            acc = sum(1 for x, y in zip(pred, truth) if x == y) / n
            match = sum(1 for x, y in zip(pred, truth) if x == y)
            if k > best_joint[0]:
                best_joint = (k, (offset, w_fig), acc)
            # 只打印关键组合
            if offset in (-0.09, -0.05, 0.0) and w_fig in (0.0, 0.1, 0.3, 0.5):
                print(f"{offset:>7.2f} {w_fig:>6.2f} {k:>7.3f} {acc:>6.2f} {match:>3}/{n}")

    print()
    print(f"全局最优: offset={best_joint[1][0]:.2f}, w_fig={best_joint[1][1]:.2f}, "
          f"κ={best_joint[0]:.3f}, acc={best_joint[2]:.2f}")

    # 看 QF 信号方向：qf 高的论文是否 my_verdict 也偏向 accept？
    print()
    print("=" * 78)
    print("QF 信号与盲评 verdict 的相关性（看 QF 信号方向是否对）")
    print("=" * 78)
    # 按 qf 排序，看 my_verdict 分布
    sorted_rows = sorted(rows, key=lambda x: x["qf"])
    print(f"{'pid':>40} {'qf':>5} {'my_verdict':>18} {'my_final':>9}")
    print("-" * 80)
    for r in sorted_rows:
        print(f"{r['pid']:>40} {r['qf']:>5.2f} {r['my_verdict']:>18} {r['my_final']:>9.3f}")

    # 相关性
    qfs = [r["qf"] for r in rows]
    my_scores = [r["my_final"] for r in rows]
    my_accept = [1 if r["my_verdict"] in ("accept", "minor_revision") else 0 for r in rows]

    # Pearson(qf, my_final)
    n = len(qfs)
    mq = sum(qfs) / n
    ms = sum(my_scores) / n
    num = sum((q - mq) * (s - ms) for q, s in zip(qfs, my_scores))
    den_q = (sum((q - mq) ** 2 for q in qfs)) ** 0.5
    den_s = (sum((s - ms) ** 2 for s in my_scores)) ** 0.5
    r_pearson = num / (den_q * den_s) if den_q * den_s > 0 else 0.0
    print()
    print(f"Pearson(qf, my_final) = {r_pearson:.3f}")
    print(f"  正值 = QF 高的论文盲评分也高（信号方向正确）")
    print(f"  负值 = QF 高的论文盲评分反而低（信号方向错误 → 加权会害）")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
