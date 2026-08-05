"""五方对比：修复前 / 无脑回填 / 降权 / QF关 / figures-OFF。

确认 figures-OFF（关闭 figure 证据并入）是否回到 0.6 baseline。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PRE = ROOT / "deliverables" / "figures_qf_test_results_on_PRE_FIX.jsonl"
NAIVE = ROOT / "deliverables" / "figures_qf_test_results_on_NAIVE_BACKFILL.jsonl"
import glob
w002_files = sorted(glob.glob(str(ROOT / "deliverables" / "figures_qf_test_results_on_W002*.jsonl")))
W002 = Path(w002_files[-1]) if w002_files else ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"
QFOFF = ROOT / "deliverables" / "figures_qf_test_results_on_QFOFF.jsonl"
FIGOFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"


def load(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        data[r["stem"]] = r
    return data


def to_2class(v):
    return "accept" if v == "accept" else "reject"


def kappa(a, b):
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    from collections import Counter
    ca = Counter(a)
    cb = Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    pre = load(PRE)
    naive = load(NAIVE)
    w002 = load(W002)
    qfoff = load(QFOFF)
    figoff = load(FIGOFF)
    datasets = [
        ("PRE_FIX (修复前 w=0.1 q_sum空)", pre),
        ("NAIVE (无脑 w=0.1)", naive),
        ("W002 (门控+降权 w=0.02)", w002),
        ("QFOFF (QF关 w=0.0)", qfoff),
        ("FIGOFF (figures-OFF)", figoff),
    ]

    print(f"=== 五方对比 ===")
    for name, d in datasets:
        print(f"  {name}: {len(d)} 篇")

    common = sorted(set(pre) & set(naive) & set(w002) & set(qfoff) & set(figoff))
    print(f"共同: {len(common)} 篇")
    if not common:
        return

    # 按论文对比
    print(f"\n{'stem':<14} {'H':>3} | {'pre':>6} {'naive':>6} {'w002':>6} {'qfoff':>6} {'figoff':>6} | {'figoff_v':<14}")
    print("-" * 110)
    for stem in common:
        h = pre[stem].get("human_accepted")
        scores = [pre[stem].get("depth_calibrated_score", 0),
                 naive[stem].get("depth_calibrated_score", 0),
                 w002[stem].get("depth_calibrated_score", 0),
                 qfoff[stem].get("depth_calibrated_score", 0),
                 figoff[stem].get("depth_calibrated_score", 0)]
        figoff_v = figoff[stem].get("depth_verdict", "?")
        print(f"{stem:<14} {'Y' if h else 'N':>3} | {scores[0]:>6.3f} {scores[1]:>6.3f} {scores[2]:>6.3f} {scores[3]:>6.3f} {scores[4]:>6.3f} | {figoff_v:<14}")

    # 2档指标
    print(f"\n=== 2档指标（accept vs non-accept）===")
    humans = ["accept" if pre[s].get("human_accepted") else "reject" for s in common]

    def metrics(preds):
        n = len(preds)
        if n == 0: return 0.0, 0, 0, 0.0
        acc = sum(1 for p, h in zip(preds, humans) if p == h) / n
        fp = sum(1 for p, h in zip(preds, humans) if p == "accept" and h == "reject")
        fn = sum(1 for p, h in zip(preds, humans) if p == "reject" and h == "accept")
        k = kappa(preds, humans)
        return acc, fp, fn, k

    print(f"{'配置':<32} {'acc':>8} {'FP':>6} {'FN':>6} {'κ':>8}")
    print("-" * 65)
    results = []
    for name, d in datasets:
        preds = [to_2class(d[s].get("depth_verdict", "")) for s in common]
        acc, fp, fn, k = metrics(preds)
        results.append((name, acc, fp, fn, k))
        print(f"{name:<32} {acc:>8.3f} {fp:>6d} {fn:>6d} {k:>8.3f}")

    # 关键对比
    pre_acc = results[0][1]
    figoff_acc = results[4][1]
    figoff_fn = results[4][3]
    figoff_k = results[4][4]

    print(f"\n=== 关键对比 ===")
    print(f"修复前 baseline acc: {pre_acc:.3f}")
    print(f"figures-OFF acc:      {figoff_acc:.3f}")
    print(f"Δ (figoff - pre):    {figoff_acc - pre_acc:+.3f}")

    print(f"\n=== 结论 ===")
    if figoff_acc >= pre_acc - 0.05:
        print(f"✓ figures-OFF 回到修复前 baseline（{pre_acc:.3f} → {figoff_acc:.3f}）")
        print(f"→ 证明 acc 下降主因是 figure 证据并入 QE 证据池的连代效应")
        print(f"→ QF 微调本身无影响（之前已证明），figure 证据影响 Q4/Q5c 辩论")
    else:
        print(f"⚠ figures-OFF 仍低于修复前 baseline（{pre_acc:.3f} → {figoff_acc:.3f}）")
        print(f"→ 还有其他因素导致 acc 下降")


if __name__ == "__main__":
    main()
