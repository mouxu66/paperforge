"""分析 figures-OFF 分数分布，找最佳 verdict 阈值。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FIGOFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"

rows = []
for line in FIGOFF.read_text(encoding="utf-8").splitlines():
    if line.strip():
        rows.append(json.loads(line))

print(f"=== figures-OFF 分数分布（{len(rows)} 篇）===\n")
print(f"{'stem':<14} {'H':>3} {'score':>8} {'verdict':<14}")
print("-" * 45)
for r in sorted(rows, key=lambda x: -(x.get("depth_calibrated_score") or 0)):
    s = r.get("depth_calibrated_score", 0) or 0
    h = "Y" if r.get("human_accepted") else "N"
    v = r.get("depth_verdict", "?")
    print(f"{r['stem']:<14} {h:>3} {s:>8.3f} {v:<14}")

# 按阈值扫描
print(f"\n=== 阈值扫描（accept if score >= threshold）===")
print(f"{'threshold':>10} {'acc':>8} {'FP':>6} {'FN':>6}")
print("-" * 35)
humans = [r.get("human_accepted", False) for r in rows]
scores = [r.get("depth_calibrated_score", 0) or 0 for r in rows]
for thresh in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
    preds = [s >= thresh for s in scores]
    acc = sum(1 for p, h in zip(preds, humans) if p == h) / len(preds)
    fp = sum(1 for p, h in zip(preds, humans) if p and not h)
    fn = sum(1 for p, h in zip(preds, humans) if not p and h)
    print(f"{thresh:>10.2f} {acc:>8.3f} {fp:>6d} {fn:>6d}")

# 看 reject 论文的分数
print(f"\n=== reject 论文（human=N）分数 ===")
for r in rows:
    if not r.get("human_accepted"):
        print(f"  {r['stem']}: {r.get('depth_calibrated_score', 0):.3f}")

print(f"\n=== accept 论文（human=Y）分数分布 ===")
acc_scores = [r.get("depth_calibrated_score", 0) or 0 for r in rows if r.get("human_accepted")]
acc_scores.sort()
print(f"  min={min(acc_scores):.3f} max={max(acc_scores):.3f} median={acc_scores[len(acc_scores)//2]:.3f}")
print(f"  < 0.65: {sum(1 for s in acc_scores if s < 0.65)} 篇")
print(f"  < 0.70: {sum(1 for s in acc_scores if s < 0.70)} 篇")
print(f"  < 0.75: {sum(1 for s in acc_scores if s < 0.75)} 篇")
