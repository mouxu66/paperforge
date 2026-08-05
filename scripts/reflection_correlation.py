"""修复前后对比 + 与人工基准分的相关性分析。

用法：
    python scripts/reflection_correlation.py \
        --baseline deliverables/reflection_baseline.csv \
        --fixed    deliverables/reflection_fixed.csv \
        --human    deliverables/human_benchmark.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIMS = ["understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support"]
# 与 mock_api/reflection_pipeline.W 保持一致（2026-08-04 校准：创新+覆盖主导）
W = {"understanding_accuracy": 0.05, "analysis_depth": 0.15, "innovative_insights": 0.35,
     "evidence_support": 0.05, "fidelity": 0.05, "coverage": 0.35}


def load(p: Path) -> dict[str, dict]:
    rows = {}
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            rows[r["sid"]] = r
    return rows


def f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """斯皮尔曼秩相关（无 scipy 依赖）。"""
    if len(xs) < 3:
        return None
    n = len(xs)

    def ranks(vals):
        idx = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[idx[j + 1]] == vals[idx[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den = (
        sum((a - mean_x) ** 2 for a in rx) ** 0.5
        * sum((b - mean_y) ** 2 for b in ry) ** 0.5
    )
    return num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--fixed", type=Path, required=True)
    ap.add_argument("--human", type=Path, default=None)
    args = ap.parse_args()

    base, fixed = load(args.baseline), load(args.fixed)

    # —— 1. 分数总览（修复前 vs 修复后）——
    print("=" * 78)
    print("1) 修复前后 6 维均分对比（全部报告）")
    print(f"{'学号':<14}{'fid前→后':>16}{'cov前→后':>16}{'avg前→后':>16}{'copy':>7}  verdict")
    for sid in sorted(set(base) & set(fixed)):
        a, b = base[sid], fixed[sid]
        print(
            f"{sid:<14}{a['fidelity']+'→'+b['fidelity']:>16}"
            f"{a['coverage']+'→'+b['coverage']:>16}"
            f"{a['average']+'→'+b['average']:>16}"
            f"{b['copy_ratio']:>7}  {a['verdict']} → {b['verdict']}"
        )

    avgs = lambda rows, key: [f(r[key]) for r in rows.values() if f(r[key]) is not None]
    for key, label in [("fidelity", "忠实度"), ("coverage", "覆盖度"), ("average", "总分")]:
        ba = avgs(base, key)
        fa = avgs(fixed, key)
        if ba and fa:
            print(
                f"\n{label}: 前均 {statistics.mean(ba):.3f} → 后均 {statistics.mean(fa):.3f}"
                f"（均值差 {statistics.mean(fa)-statistics.mean(ba):+.3f}）"
            )

    # —— 2. copy_ratio 分布 ——
    print("\n" + "=" * 78)
    print("2) 修复后照抄比例（copy_ratio）分布")
    copies = [(sid, f(r["copy_ratio"]) or 0.0) for sid, r in fixed.items()]
    copies.sort(key=lambda x: -x[1])
    high = [(s, c) for s, c in copies if c >= 0.10]
    print(f"copy_ratio ≥ 0.10 的报告 {len(high)} 篇：")
    for s, c in high[:10]:
        print(f"   {s}: {c:.3f}  verdict={fixed[s]['verdict']}")

    # —— 3. 人工基准相关性 ——
    if args.human and args.human.exists():
        print("\n" + "=" * 78)
        print("3) 与人工基准分的 Spearman 相关（13 篇样本）")
        human = load(args.human)
        common = [s for s in human if s in fixed and f(human[s]["total"]) is not None]
        for key, label in [("average", "系统总分")] + [(d, d) for d in DIMS] + [("fidelity", "fidelity"), ("coverage", "coverage")]:
            for tag, rows in [("修复前", base), ("修复后", fixed)]:
                xs, ys = [], []
                for s in common:
                    sv = f(rows[s].get(key))
                    hv = f(human[s].get(key))
                    if sv is not None and hv is not None:
                        xs.append(sv)
                        ys.append(hv)
                r = spearman(xs, ys)
                if r is not None:
                    print(f"   {label:<22} vs 人工 {key:<22} {tag}: ρ={r:+.3f} (n={len(xs)})")
        # 总分散点
        print("\n   人工总分 vs 系统总分（修复后）:")
        for s in sorted(common, key=lambda s: -f(human[s]["total"])):
            print(f"     {s} 人工={human[s]['total']} 系统={fixed[s].get('average','-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
