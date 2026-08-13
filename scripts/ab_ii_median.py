"""II 维中位数采样收益 A/B（离线，零 LLM 成本）。

对比同一批报告（deliverables/ornstein_vs_human_full.csv 的 orn_II_samples 列）：
  A) 单次采样：每篇取第一个原始 II 样本（等价于旧版单次调用）
  B) 中位数  ：每篇对 3 个原始 II 样本取中位数（现行生产口径）

看 B 相对 A 是否更贴近人工金标 II（human_II）：
  - MAE(system, human)：越低越好
  - II 跨度 max-min：反映排名区分度（不是越高越好，对齐人工跨度最佳）
  - Spearman ρ(system, human)：越高越好
  - 逐篇胜率：B 比 A 更接近人工 II 的篇数占比

用法：
  python scripts/ab_ii_median.py [--input deliverables/ornstein_vs_human_full.csv]
"""
import argparse
import csv
import os
from statistics import mean, median

DEFAULT_INPUT = "deliverables/ornstein_vs_human_full.csv"


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman 秩相关（并列值取平均秩）。"""
    if len(xs) < 2:
        return float("nan")

    def _rank(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-indexed 平均秩
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


def _mae(a: list[float], b: list[float]) -> float:
    return mean([abs(x - y) for x, y in zip(a, b)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"输入缺失: {args.input}（先跑 scripts/orn_review.py 全量 41 篇）")

    rows = list(csv.DictReader(open(args.input, encoding="utf-8-sig")))
    usable = []
    for r in rows:
        raw = (r.get("orn_II_samples") or "").strip()
        if not raw:
            continue
        try:
            samples = [float(x) for x in raw.split(",") if x.strip() != ""]
        except ValueError:
            continue
        if len(samples) < 2 or not r.get("human_II"):
            continue
        usable.append((r["sid"], samples, float(r["human_II"])))

    if len(usable) < 5:
        raise SystemExit(f"可用样本太少（{len(usable)} 篇），无法 A/B")

    single = [u[1][0] for u in usable]
    medians = [median(u[1]) for u in usable]
    human = [u[2] for u in usable]
    n = len(usable)

    mae_single = _mae(single, human)
    mae_median = _mae(medians, human)
    span_single = max(single) - min(single)
    span_median = max(medians) - min(medians)
    span_human = max(human) - min(human)
    rho_single = _spearman(single, human)
    rho_median = _spearman(medians, human)

    # 样本内散布（措辞噪声的直接度量）：每篇 3 样本的极差均值
    intra_spread = mean([max(s) - min(s) for _, s, _ in usable])
    big_spread = sum(1 for _, s, _ in usable if max(s) - min(s) > 0.2)
    # 单次调用（第 1 个样本）有多少篇恰好已落在中位数上（此时中位数无增量价值）
    s0_is_median = sum(1 for _, s, _ in usable if s[0] == median(s))

    # 单次采样的「采样位置」稳定性：同一篇报告换一个样本，MAE 会抖动多少。
    # 若样本间 MAE 差异大 → 单次采样不可复现；中位数 MAE 固定 → 可复现。
    per_pos_mae = []
    for k in range(3):
        vals = [u[1][k] for u in usable if len(u[1]) > k]
        hs = [u[2] for u in usable if len(u[1]) > k]
        per_pos_mae.append(_mae(vals, hs))

    print(f"=== II 维中位数采样收益 A/B（n={n}，source={args.input}）===\n")
    print(f"{'指标':<24}{'A 单次采样':>14}{'B 中位数':>14}{'人工金标':>14}")
    print("-" * 66)
    print(f"{'MAE vs human_II':<24}{mae_single:>14.4f}{mae_median:>14.4f}{'—':>14}")
    print(f"{'II 跨度 max-min':<24}{span_single:>14.4f}{span_median:>14.4f}{span_human:>14.4f}")
    print(f"{'Spearman ρ vs human':<24}{rho_single:>14.4f}{rho_median:>14.4f}{'—':>14}")
    print("-" * 66)
    print(f"样本1/2/3 各自的 MAE: {per_pos_mae[0]:.4f} / {per_pos_mae[1]:.4f} / {per_pos_mae[2]:.4f}")
    print(f"每篇 3 样本极差均值（措辞噪声）: {intra_spread:.4f}（极差>0.2 的 {big_spread}/{n} 篇）")
    print(f"单次调用已落在中位数上的篇数: {s0_is_median}/{n}（其余 {n - s0_is_median} 篇中位数避免了取到极端样本）")
    print()
    # 结论（ASCII 标记，避免 Windows GBK 控制台编码崩溃）
    print("=== 结论 ===")
    if mae_median < mae_single:
        print(f"[+] 中位数 MAE 更低：{mae_single:.4f} -> {mae_median:.4f}（改善 {(mae_single-mae_median)/mae_single:.1%}）")
    else:
        print(f"[-] 中位数 MAE 未改善：{mae_single:.4f} -> {mae_median:.4f}（差值 {mae_median-mae_single:+.4f}，噪声内）")
    if rho_median > rho_single:
        print(f"[+] 中位数排序相关性更高：rho {rho_single:.4f} -> {rho_median:.4f}")
    else:
        print(f"[~] 排序相关性未提升：rho {rho_single:.4f} -> {rho_median:.4f}")
    mae_spread = max(per_pos_mae) - min(per_pos_mae)
    print(f"[i] 单次采样的 MAE 随样本位置抖动 {mae_spread:.4f}（不可复现），中位数 MAE 固定 {mae_median:.4f}（可复现）")


if __name__ == "__main__":
    main()
