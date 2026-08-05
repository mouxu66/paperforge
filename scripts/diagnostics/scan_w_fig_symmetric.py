"""A 方案验证：对称评分 + 开门后，P0-B 信号是否变正收益。

从旧 qf（不对称公式 0.4+0.55*avg）反推 avg，用新公式（0.05+0.9*avg）重算新 qf，
然后模拟开门后的 w_fig 扫描。不需要重跑 LLM。

验证两个数据集：
  1. PeerRead 200 篇（baseline_fig_pilot.jsonl + p0_text_evidence_diagnose.jsonl）
  2. 盲评 20 篇（depth_scores_415.json + calib_my_review.json）

公式：
  旧: score_old = 0.4 + 0.55 * avg   → avg = (score_old - 0.4) / 0.55
  新: score_new = 0.05 + 0.9 * avg   → score_new = 0.05 + 0.9 * (score_old - 0.4) / 0.55

  qf=0.5 的论文（无 text_ev）保持 0.5，不进合并（has_figures=False）
  qf≠0.5 的论文用新公式重算，进合并（has_figures=True）
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DELIVERABLES = ROOT / "deliverables"


def old_to_new_qf(old_qf: float) -> float:
    """从旧公式 qf 反推 avg，用新公式重算。"""
    if abs(old_qf - 0.5) < 1e-6:
        return 0.5  # 无 text_ev，中性，不进合并
    avg = (old_qf - 0.4) / 0.55
    avg = max(0.0, min(1.0, avg))
    return round(0.05 + 0.9 * avg, 3)


def cohen_kappa(y1, y2):
    labels = sorted(set(y1) | set(y2))
    n = len(y1)
    po = sum(1 for a, b in zip(y1, y2) if a == b) / n
    from collections import Counter
    c1, c2 = Counter(y1), Counter(y2)
    pe = sum(c1[l] * c2[l] for l in labels) / (n * n)
    if pe == 1.0:
        return 0.0
    return (po - pe) / (1 - pe)


def verdict_4tier(score):
    if score >= 0.80:
        return "accept"
    if score >= 0.70:
        return "minor"
    if score >= 0.50:
        return "major"
    return "reject"


# ============================================================
# 1. PeerRead 200 篇
# ============================================================
def scan_peerread():
    baseline_path = DELIVERABLES / "baseline_fig_pilot.jsonl"
    diagnose_path = DELIVERABLES / "p0_text_evidence_diagnose.jsonl"
    if not baseline_path.exists() or not diagnose_path.exists():
        print("[skip] PeerRead 数据不存在")
        return

    # 诊断结果：stem -> score (qf_old)
    diag = {}
    for line in diagnose_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        diag[d["stem"]] = d.get("score", 0.5)

    # baseline：stem -> depth_base_score, human_accepted
    rows = []
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        stem = d.get("stem", "")
        human = d.get("human_accepted")
        depth_base = d.get("depth_base_score")
        qf_old = diag.get(stem, 0.5)
        if human is None or depth_base is None:
            continue
        rows.append({
            "stem": stem,
            "human": "accept" if human else "reject",
            "base": depth_base,
            "qf_old": qf_old,
            "qf_new": old_to_new_qf(qf_old),
        })

    n = len(rows)
    has_signal = [r for r in rows if abs(r["qf_old"] - 0.5) > 1e-6]

    print("=" * 78)
    print(f"A 方案 · PeerRead 200 篇 · 对称评分扫描（n={n}，有信号={len(has_signal)}）")
    print("=" * 78)
    print(f"  旧公式: score = 0.4 + 0.55*avg  （不对称：奖+0.45 / 罚-0.10）")
    print(f"  新公式: score = 0.05 + 0.9*avg  （对称：奖+0.45 / 罚-0.45）")
    print(f"  模拟: new_final = base + w_fig*(qf - 0.5)")
    print()

    if has_signal:
        old_vals = [r["qf_old"] for r in has_signal]
        new_vals = [r["qf_new"] for r in has_signal]
        print(f"有信号论文 qf 对比（n={len(has_signal)}）:")
        print(f"  旧: mean={sum(old_vals)/len(old_vals):.3f} min={min(old_vals):.3f} max={max(old_vals):.3f}")
        print(f"  新: mean={sum(new_vals)/len(new_vals):.3f} min={min(new_vals):.3f} max={max(new_vals):.3f}")
        old_low = sum(1 for v in old_vals if v < 0.5)
        new_low = sum(1 for v in new_vals if v < 0.5)
        print(f"  qf<0.5 数量: 旧={old_low} → 新={new_low}（对称后惩罚面）")
    print()

    # 扫描
    print(f"{'w_fig':>6}  {'κ(旧)':>8}  {'κ(新)':>8}  {'acc(新)':>7}  {'accept':>6}  {'reject':>6}  {'FP':>4}  {'FN':>4}")
    print("-" * 72)

    y_h = [r["human"] for r in rows]
    best_new = (0.0, 0.0, 0.0)
    for w in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        y_old = [verdict_4tier(r["base"] + w * (r["qf_old"] - 0.5)) for r in rows]
        y_new = [verdict_4tier(r["base"] + w * (r["qf_new"] - 0.5)) for r in rows]
        k_old = cohen_kappa(y_h, y_old)
        k_new = cohen_kappa(y_h, y_new)
        acc = sum(1 for a, b in zip(y_h, y_new) if a == b) / n
        accept_n = sum(1 for v in y_new if v == "accept")
        reject_n = sum(1 for v in y_new if v == "reject")
        fp = sum(1 for h, p in zip(y_h, y_new) if h == "reject" and p == "accept")
        fn = sum(1 for h, p in zip(y_h, y_new) if h == "accept" and p == "reject")

        marker = ""
        if k_new > best_new[0]:
            best_new = (k_new, w, acc)
            marker = " *"
        print(f"{w:6.2f}  {k_old:8.3f}  {k_new:8.3f}  {acc:7.3f}  {accept_n:6d}  {reject_n:6d}  {fp:4d}  {fn:4d}{marker}")

    print()
    print(f"最优（新公式）: w_fig={best_new[1]:.2f}, κ={best_new[0]:.3f}, acc={best_new[2]:.3f}")


# ============================================================
# 2. 盲评 20 篇
# ============================================================
def scan_blind20():
    scores_path = DELIVERABLES / "depth_scores_415.json"
    my_path = ROOT / "calib_papers" / "runs" / "calib_my_review.json"
    if not scores_path.exists() or not my_path.exists():
        print("[skip] 盲评数据不存在")
        return

    my_reviews = json.loads(my_path.read_text(encoding="utf-8"))
    my_map = {r["pid"]: r for r in my_reviews}

    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    rows = []
    for p in scores:
        pid = p.get("id", "")
        if pid not in my_map:
            continue
        my = my_map[pid]
        my_v = my.get("my_verdict", "").lower()
        if not my_v:
            continue
        cal = p.get("calibrated_score", 0.5)
        qf_old = p.get("qf", 0.5)
        rows.append({
            "pid": pid,
            "my_verdict": my_v,
            "calibrated": cal,
            "qf_old": qf_old,
            "qf_new": old_to_new_qf(qf_old),
        })

    n = len(rows)
    if n == 0:
        print("[skip] 盲评匹配失败")
        return

    has_signal = [r for r in rows if abs(r["qf_old"] - 0.5) > 1e-6]

    print()
    print("=" * 78)
    print(f"A 方案 · 盲评 20 篇 · 对称评分扫描（n={n}，有信号={len(has_signal)}）")
    print("=" * 78)
    print(f"  模拟: new_final = calibrated + offset + w_fig*(qf - 0.5)")
    print()

    if has_signal:
        print("盲评 qf 对比（有信号的论文）:")
        for r in sorted(has_signal, key=lambda x: x["qf_old"]):
            print(f"  {r['pid']:40s}  旧={r['qf_old']:.3f} → 新={r['qf_new']:.3f}  ({r['my_verdict']})")
    print()

    # 固定 offset=-0.09
    offset = -0.09
    print(f"固定 offset={offset}, 扫描 w_fig:")
    print(f"{'w_fig':>6}  {'κ(旧)':>8}  {'κ(新)':>8}  {'acc(新)':>7}  {'一致':>6}")
    print("-" * 50)

    y_my = [r["my_verdict"] for r in rows]
    best_new = (0.0, 0.0, 0.0)
    for w in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        y_old = [verdict_4tier(r["calibrated"] + offset + w * (r["qf_old"] - 0.5)) for r in rows]
        y_new = [verdict_4tier(r["calibrated"] + offset + w * (r["qf_new"] - 0.5)) for r in rows]
        k_old = cohen_kappa(y_my, y_old)
        k_new = cohen_kappa(y_my, y_new)
        acc = sum(1 for a, b in zip(y_my, y_new) if a == b) / n
        match = sum(1 for a, b in zip(y_my, y_new) if a == b)

        marker = ""
        if k_new > best_new[0]:
            best_new = (k_new, w, acc)
            marker = " *"
        print(f"{w:6.2f}  {k_old:8.3f}  {k_new:8.3f}  {acc:7.3f}  {match:3d}/{n}")

    print()
    print(f"最优（新公式）: w_fig={best_new[1]:.2f}, κ={best_new[0]:.3f}, acc={best_new[2]:.3f}")

    # 联合扫描
    print()
    print("联合扫描 offset × w_fig（新公式）:")
    print(f"{'offset':>8} {'w_fig':>6}  {'κ':>8}  {'acc':>7}  {'一致':>6}")
    print("-" * 45)
    best_joint = (0.0, 0, 0, 0.0)
    for off in [-0.13, -0.09, -0.05, 0.0]:
        for w in [0.0, 0.05, 0.10, 0.20, 0.50]:
            y_new = [verdict_4tier(r["calibrated"] + off + w * (r["qf_new"] - 0.5)) for r in rows]
            k = cohen_kappa(y_my, y_new)
            acc = sum(1 for a, b in zip(y_my, y_new) if a == b) / n
            match = sum(1 for a, b in zip(y_my, y_new) if a == b)
            marker = ""
            if k > best_joint[0]:
                best_joint = (k, off, w, acc)
                marker = " *"
            print(f"{off:8.2f} {w:6.2f}  {k:8.3f}  {acc:7.3f}  {match:3d}/{n}{marker}")
    print()
    print(f"全局最优: offset={best_joint[1]:.2f}, w_fig={best_joint[2]:.2f}, κ={best_joint[0]:.3f}, acc={best_joint[3]:.3f}")


if __name__ == "__main__":
    scan_peerread()
    scan_blind20()
