"""正确公式重算：calibrated 已含 offset，不加额外 offset。
+ PeerRead: depth_base 不含 offset，需加 offset=0.18。"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def verdict_2tier(s):
    return "accept" if s >= 0.80 else "reject"


def kappa(y1, y2):
    labels = sorted(set(y1) | set(y2))
    n = len(y1)
    po = sum(1 for a, b in zip(y1, y2) if a == b) / n
    c1, c2 = Counter(y1), Counter(y2)
    pe = sum(c1[l] * c2[l] for l in labels) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1.0 else 0.0


def old_to_new_qf(old):
    if abs(old - 0.5) < 1e-6: return 0.5
    avg = (old - 0.4) / 0.55
    return round(0.05 + 0.9 * max(0.0, min(1.0, avg)), 3)


def scan_peerread():
    baseline_path = ROOT / "deliverables/baseline_fig_pilot.jsonl"
    diag_path = ROOT / "deliverables/p0_text_evidence_diagnose.jsonl"
    if not baseline_path.exists() or not diag_path.exists():
        print("[skip] PeerRead")
        return

    diag = {}
    for line in diag_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            diag[d["stem"]] = d.get("score", 0.5)

    rows = []
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        stem = d.get("stem", "")
        human = d.get("human_accepted")
        base = d.get("depth_base_score")
        qf_old = diag.get(stem, 0.5)
        if human is None or base is None:
            continue
        rows.append({
            "human": "accept" if human else "reject",
            "base": base,
            "qf_old": qf_old,
            "qf_new": old_to_new_qf(qf_old),
        })

    n = len(rows)
    has_signal = [r for r in rows if abs(r["qf_old"] - 0.5) > 1e-6]
    y_h = [r["human"] for r in rows]

    print("=" * 70)
    print(f"PeerRead 200 (n={n}, signal={len(has_signal)})")
    print("=" * 70)
    print("formula: final = depth_base + offset + w_fig*(qf-0.5)")
    print()

    # offset=0.18 是 PeerRead 最优
    offset = 0.18
    print(f"offset={offset}:")
    print(f"{'w_fig':>6}  {'k_old':>7}  {'k_new':>7}  {'acc_o':>6}  {'acc_n':>6}  {'FP':>4}  {'FN':>4}")
    print("-" * 55)
    best_old = (0, 0, 0)
    best_new = (0, 0, 0)
    for w in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        y_old = [verdict_2tier(r["base"] + offset + w * (r["qf_old"] - 0.5)) for r in rows]
        y_new = [verdict_2tier(r["base"] + offset + w * (r["qf_new"] - 0.5)) for r in rows]
        k_old = kappa(y_h, y_old)
        k_new = kappa(y_h, y_new)
        acc_o = sum(1 for a, b in zip(y_h, y_old) if a == b) / n
        acc_n = sum(1 for a, b in zip(y_h, y_new) if a == b) / n
        fp = sum(1 for h, p in zip(y_h, y_new) if h == "reject" and p == "accept")
        fn = sum(1 for h, p in zip(y_h, y_new) if h == "accept" and p == "reject")
        star = " *" if k_new > best_new[0] else ""
        if k_old > best_old[0]: best_old = (k_old, w, acc_o)
        if k_new > best_new[0]: best_new = (k_new, w, acc_n)
        print(f"{w:6.2f}  {k_old:7.3f}  {k_new:7.3f}  {acc_o:6.3f}  {acc_n:6.3f}  {fp:4d}  {fn:4d}{star}")

    print(f"\nold best: w={best_old[1]:.2f} k={best_old[0]:.3f}")
    print(f"new best: w={best_new[1]:.2f} k={best_new[0]:.3f}")


def scan_blind20():
    scores = json.load(open(ROOT / "deliverables/depth_scores_415.json", encoding="utf-8"))
    my = json.load(open(ROOT / "calib_papers" / "runs" / "calib_my_review.json", encoding="utf-8"))
    my_map = {r["pid"]: r for r in my}

    rows = []
    for p in scores:
        pid = p["id"]
        if pid not in my_map:
            continue
        # 2 档：accept vs reject（minor/major 算 reject）
        my_v = "accept" if my_map[pid]["my_verdict"].lower() == "accept" else "reject"
        cal = p["calibrated_score"]
        qf = p.get("qf", 0.5)
        rows.append((pid, my_v, cal, qf))

    n = len(rows)
    y_my = [r[1] for r in rows]

    print()
    print("=" * 70)
    print(f"Blind 20 (n={n})")
    print("=" * 70)
    print("formula: final = calibrated + w_fig*(qf-0.5)  [calibrated has offset]")
    print()

    print(f"{'w_fig':>6}  {'k_old':>7}  {'k_new':>7}  {'acc_o':>6}  {'acc_n':>6}")
    print("-" * 45)
    best_old = (0, 0, 0)
    best_new = (0, 0, 0)
    for w in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
        y_old = [verdict_2tier(r[2] + w * (r[3] - 0.5)) for r in rows]
        new_qf = [old_to_new_qf(r[3]) for r in rows]
        y_new = [verdict_2tier(r[2] + w * (new_qf[i] - 0.5)) for i, r in enumerate(rows)]
        k_old = kappa(y_my, y_old)
        k_new = kappa(y_my, y_new)
        acc_o = sum(1 for a, b in zip(y_my, y_old) if a == b) / n
        acc_n = sum(1 for a, b in zip(y_my, y_new) if a == b) / n
        star = " *" if k_new > best_new[0] else ""
        if k_old > best_old[0]: best_old = (k_old, w, acc_o)
        if k_new > best_new[0]: best_new = (k_new, w, acc_n)
        print(f"{w:6.2f}  {k_old:7.3f}  {k_new:7.3f}  {acc_o:6.3f}  {acc_n:6.3f}{star}")

    print(f"\nold best: w={best_old[1]:.2f} k={best_old[0]:.3f}")
    print(f"new best: w={best_new[1]:.2f} k={best_new[0]:.3f}")


if __name__ == "__main__":
    scan_peerread()
    scan_blind20()
