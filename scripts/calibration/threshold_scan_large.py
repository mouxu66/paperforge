"""阈值扫描：在 100 篇大样本结果上扫描 verdict_accept_threshold，找大样本最优。

不重跑 DEPTH，直接用已有 calibrated_score 重新算 verdict，找使 acc 最大的阈值。

用法：
    python scripts/calibration/threshold_scan_large.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "deliverables" / "figures_off_large_scale.jsonl"


def verdict_from_score(score: float, threshold: float) -> str:
    """根据阈值从分数反推 verdict（strict: accept vs reject）。"""
    return "accept" if score >= threshold else "reject"


def confusion(y_pred, y_true) -> dict:
    tp = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 1)
    fn = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 1)  # pred reject, true accept
    fp = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 0)  # pred accept, true reject
    tn = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 0)
    n = len(y_pred)
    return {
        "n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "acc": round((tp + tn) / n, 4) if n else 0,
        "prec": round(tp / (tp + fp), 4) if (tp + fp) else 0,
        "rec": round(tp / (tp + fn), 4) if (tp + fn) else 0,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else 0,
    }


def kappa(y_pred, y_true) -> float:
    n = len(y_pred)
    po = sum(1 for p, t in zip(y_pred, y_true) if p == t) / n
    pa = sum(y_pred) / n
    pb = sum(y_true) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def main():
    rows = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"=== 阈值扫描（n={len(rows)}）===")
    print(f"human 分布: accept={sum(1 for r in rows if r['human_accepted'])}, "
          f"reject={sum(1 for r in rows if not r['human_accepted'])}")

    y_true = [1 if r["human_accepted"] else 0 for r in rows]

    # 扫描 0.5 - 0.85
    print(f"\n{'threshold':>10} | {'acc':>6} {'κ':>7} | {'TP':>3} {'FN':>3} {'FP':>3} {'TN':>3} | {'prec':>5} {'rec':>5} {'F1':>5}")
    print("-" * 75)
    best_acc = 0
    best_t = 0
    best_f1 = 0
    best_t_f1 = 0
    for t_10 in range(50, 86):
        t = t_10 / 100
        y_pred = [1 if (r["depth_calibrated_score"] or 0) >= t else 0 for r in rows]
        cm = confusion(y_pred, y_true)
        k = kappa(y_pred, y_true)
        flag = ""
        if cm["acc"] > best_acc:
            best_acc = cm["acc"]
            best_t = t
            flag = " <- best acc"
        if cm["f1"] > best_f1:
            best_f1 = cm["f1"]
            best_t_f1 = t
            flag += " <- best F1"
        if t_10 % 5 == 0 or flag:
            print(f"  {t:.2f}     | {cm['acc']:.4f} {k:+.4f} | {cm['tp']:>3} {cm['fn']:>3} {cm['fp']:>3} {cm['tn']:>3} | {cm['prec']:.3f} {cm['rec']:.3f} {cm['f1']:.3f}{flag}")

    print(f"\n=== 最优阈值 ===")
    print(f"  最高 acc: threshold={best_t:.2f}, acc={best_acc:.4f}")
    print(f"  最高 F1:  threshold={best_t_f1:.2f}, F1={best_f1:.4f}")

    # 看分数分布
    print(f"\n=== accept/reject 论文分数分布 ===")
    accept_scores = sorted([r["depth_calibrated_score"] for r in rows if r["human_accepted"]])
    reject_scores = sorted([r["depth_calibrated_score"] for r in rows if not r["human_accepted"]])
    print(f"  accept 分数: min={accept_scores[0]:.3f}, median={accept_scores[len(accept_scores)//2]:.3f}, max={accept_scores[-1]:.3f}")
    print(f"  reject 分数: min={reject_scores[0]:.3f}, median={reject_scores[len(reject_scores)//2]:.3f}, max={reject_scores[-1]:.3f}")


if __name__ == "__main__":
    main()
