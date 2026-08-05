"""对比 qwen_summary 兜底修复前/后的 figures-ON 结果。

读取三个 JSONL：
  - figures_qf_test_results_on_PRE_FIX.jsonl       (修复前 baseline，无 qwen_summary)
  - figures_qf_test_results_on_NAIVE_BACKFILL.jsonl (无脑回填 w_fig=0.1，含噪声)
  - figures_qf_test_results_on.jsonl              (质量门控 + w_fig=0.02 降权)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PRE = ROOT / "deliverables" / "figures_qf_test_results_on_PRE_FIX.jsonl"
NAIVE = ROOT / "deliverables" / "figures_qf_test_results_on_NAIVE_BACKFILL.jsonl"
# 找最新的 W002 备份
import glob
w002_files = sorted(glob.glob(str(ROOT / "deliverables" / "figures_qf_test_results_on_W002*.jsonl")))
W002 = Path(w002_files[-1]) if w002_files else ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"
QFOFF = ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"


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
    """2-class Cohen's kappa: accept vs non-accept."""
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
    datasets = [
        ("PRE_FIX (修复前 w=0.1)", pre),
        ("NAIVE_BACKFILL (无脑 w=0.1)", naive),
        ("W_FIG=0.02 (门控+降权)", w002),
        ("QF_OFF (QF节点关闭)", qfoff),
    ]

    print(f"=== 四方对比 ===")
    for name, d in datasets:
        print(f"  {name}: {len(d)} 篇")

    common = sorted(set(pre) & set(naive) & set(w002) & set(qfoff))
    print(f"共同: {len(common)} 篇")
    if not common:
        return

    # 按论文对比
    print(f"\n{'stem':<14} {'H':>3} | {'pre':>6} {'naive':>6} {'w002':>6} {'qfoff':>6} | {'pre_v':<14} {'naive_v':<14} {'w002_v':<14} {'qfoff_v':<14}")
    print("-" * 130)
    for stem in common:
        h = pre[stem].get("human_accepted")
        scores = [pre[stem].get("depth_calibrated_score", 0),
                 naive[stem].get("depth_calibrated_score", 0),
                 w002[stem].get("depth_calibrated_score", 0),
                 qfoff[stem].get("depth_calibrated_score", 0)]
        vs = [pre[stem].get("depth_verdict", "?"),
              naive[stem].get("depth_verdict", "?"),
              w002[stem].get("depth_verdict", "?"),
              qfoff[stem].get("depth_verdict", "?")]
        print(f"{stem:<14} {'Y' if h else 'N':>3} | {scores[0]:>6.3f} {scores[1]:>6.3f} {scores[2]:>6.3f} {scores[3]:>6.3f} | {vs[0]:<14} {vs[1]:<14} {vs[2]:<14} {vs[3]:<14}")

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
    pre_acc, pre_fn, pre_k = results[0][1], results[0][3], results[0][4]
    naiv_acc, naiv_fn, naiv_k = results[1][1], results[1][3], results[1][4]
    w002_acc, w002_fn, w002_k = results[2][1], results[2][3], results[2][4]
    qfoff_acc, qfoff_fn, qfoff_k = results[3][1], results[3][3], results[3][4]

    print(f"\n=== 关键对比 ===")
    print(f"acc:  修复前 {pre_acc:.3f} → 无脑 {naiv_acc:.3f} (Δ{naiv_acc-pre_acc:+.3f}) → 降权 {w002_acc:.3f} (Δ{w002_acc-naiv_acc:+.3f}) → QF关 {qfoff_acc:.3f} (Δ{qfoff_acc-w002_acc:+.3f})")
    print(f"FN:   修复前 {pre_fn} → 无脑 {naiv_fn} (Δ{naiv_fn-pre_fn:+d}) → 降权 {w002_fn} (Δ{w002_fn-naiv_fn:+d}) → QF关 {qfoff_fn} (Δ{qfoff_fn-w002_fn:+d})")
    print(f"κ:    修复前 {pre_k:.3f} → 无脑 {naiv_k:.3f} (Δ{naiv_k-pre_k:+.3f}) → 降权 {w002_k:.3f} (Δ{w002_k-naiv_k:+.3f}) → QF关 {qfoff_k:.3f} (Δ{qfoff_k-w002_k:+.3f})")

    # 结论
    print(f"\n=== 结论 ===")
    if qfoff_acc > w002_acc:
        print(f"✓ QF 关闭提升 acc（{w002_acc:.3f} → {qfoff_acc:.3f}）")
    elif qfoff_acc < w002_acc:
        print(f"⚠ QF 关闭降低 acc（{w002_acc:.3f} → {qfoff_acc:.3f}）")
    else:
        print(f"= QF 关闭对 acc 无影响（{qfoff_acc:.3f}）")

    if qfoff_acc >= pre_acc:
        print(f"✓ QF 关闭后 acc 超越修复前 baseline（{pre_acc:.3f} → {qfoff_acc:.3f}）")
        print(f"→ 证明 acc 下降主因是 QF 路径（含 figure 证据并入），而非 LLM 随机性")
    elif qfoff_acc >= pre_acc - 0.05:
        print(f"→ QF 关闭后 acc 接近修复前 baseline（{pre_acc:.3f} → {qfoff_acc:.3f}）")
        print(f"→ 证明 acc 下降主因是 QF 路径，LLM 随机性贡献较小")
    else:
        print(f"⚠ QF 关闭后 acc 仍低于修复前 baseline（{pre_acc:.3f} → {qfoff_acc:.3f}）")
        print(f"→ 证明 LLM 随机性是 acc 下降主因，与 QF 路径无关")


if __name__ == "__main__":
    main()
