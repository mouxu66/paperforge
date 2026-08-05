"""偏移量重扫标准流程（P1）。

给定一份【基线】结果 jsonl（即 offset=0 下 DEPTH 对语料全文打分的结果，
字段含 depth_calibrated_score 与 human_accepted），本脚本在 baseline 分上做
**确定性加法偏移扫描**（数学等价于端到端跑 offset=Δ，无需重跑 LLM），快速确定
该语料专属的最优 Δ。

这正是 §8.2 中 N=198 所用的方法：在 peerread_rescore_n200_base.jsonl（offset=0）
上扫描 Δ，得到推荐 Δ=+0.18。本脚本将其固化为可复用流程。

用法：
  python scripts/offset_scan.py --in deliverables/peerread_rescore_n200_base.jsonl \
      --source-name peerread --out deliverables/peerread_offset_scan.json \
      --recommend-out deliverables/peerread_offset_recommend.json

输出：
  - stdout：Δ 扫描表 + 推荐 Δ（使固定阈值 κ 最大）。
  - --out：完整扫描结果 JSON（含每个 Δ 的 verdict 分布 / κ / acc / FP / FN / 组均分）。
  - --recommend-out：{"<source-name>": Δ} 可直接并入 PAPERFORGE_DEPTH_OFFSET_TABLE。

如何生产基线（offset=0）jsonl：
  对目标 source 跑 peerread_rescore 同类管线时，显式清零偏移：
    PAPERFORGE_DEPTH_OFFSET_TABLE='{"default":0,"<source>":0}' python scripts/peerread_rescore.py run --out baseline.jsonl
  （注意：DEFAULT_OFFSET_TABLE 已内置 {default:-0.09, peerread:0.18}，故必须显式把
   目标 source 与 default 都设为 0，才能得到真正的 offset=0 基线。）
"""
from __future__ import annotations

import argparse
import json
from collections import Counter


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


def load_baseline(path: str) -> list[tuple[float, bool, str]]:
    rows: list[tuple[float, bool, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            s = o.get("depth_calibrated_score")
            if s is None:
                s = o.get("calibrated_score")
            ha = o.get("human_accepted")
            if ha is None:
                ha = o.get("accepted")
            if s is None or ha is None:
                continue
            rows.append((float(s), bool(ha), str(o.get("stem", ""))))
    return rows


def scan(rows, fix_threshold=0.80, dmin=0.0, dmax=0.50, step=0.01):
    scores = [s for s, _, _ in rows]
    truth = [1 if ha else 0 for _, ha, _ in rows]
    N = len(rows)
    results = []
    best = (0.0, None)  # (kappa, delta)
    n_steps = int(round((dmax - dmin) / step))
    for i in range(n_steps + 1):
        d = round(dmin + i * step, 4)
        adj = [min(s + d, 1.0) for s in scores]
        pred = [1 if x >= fix_threshold else 0 for x in adj]
        tp = sum(1 for x, y in zip(pred, truth) if x == 1 and y == 1)
        fp = sum(1 for x, y in zip(pred, truth) if x == 1 and y == 0)
        fn = sum(1 for x, y in zip(pred, truth) if x == 0 and y == 1)
        tn = sum(1 for x, y in zip(pred, truth) if x == 0 and y == 0)
        acc = (tp + tn) / N if N else 0.0
        k = cohen_kappa_binary(pred, truth)
        bc = Counter()
        for x in adj:
            if x >= 0.80:
                bc["accept"] += 1
            elif x >= 0.70:
                bc["minor"] += 1
            elif x >= 0.50:
                bc["major"] += 1
            else:
                bc["reject"] += 1
        acc_grp = [s for s, ha, _ in rows if ha]
        rej_grp = [s for s, ha, _ in rows if not ha]
        am = sum(min(x + d, 1.0) for x in acc_grp) / len(acc_grp) if acc_grp else 0.0
        rm = sum(min(x + d, 1.0) for x in rej_grp) / len(rej_grp) if rej_grp else 0.0
        rec = {
            "delta": d,
            "kappa": round(k, 4),
            "acc": round(acc, 4),
            "accept": bc["accept"],
            "minor": bc["minor"],
            "major": bc["major"],
            "reject": bc["reject"],
            "fp": fp,
            "fn": fn,
            "tp": tp,
            "tn": tn,
            "accept_grp_mean": round(am, 4),
            "reject_grp_mean": round(rm, 4),
        }
        results.append(rec)
        if k > best[0]:
            best = (k, d)
    return results, best


def _is_show(r, best_delta):
    show_deltas = {0.0, 0.10, 0.15, 0.18, 0.20, 0.25, 0.30, 0.34, 0.40, 0.50}
    return (abs(r["delta"] - best_delta) < 1e-9) or (round(r["delta"], 2) in show_deltas)


def main():
    ap = argparse.ArgumentParser(description="DEPTH 偏移量重扫（基线 jsonl + 确定性加法扫描）")
    ap.add_argument("--in", dest="inp", required=True, help="基线 jsonl（offset=0 结果）")
    ap.add_argument("--source-name", default="peerread", help="推荐偏移表的来源键名")
    ap.add_argument("--fix-threshold", type=float, default=0.80)
    ap.add_argument("--min-delta", type=float, default=0.0)
    ap.add_argument("--max-delta", type=float, default=0.50)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--out", default=None, help="完整扫描结果 JSON 输出")
    ap.add_argument(
        "--recommend-out", default=None, help="推荐偏移表 JSON 输出 {'<source>': Δ}"
    )
    args = ap.parse_args()

    rows = load_baseline(args.inp)
    if not rows:
        print(f"[offset_scan] 无有效数据: {args.inp}")
        return
    results, best = scan(rows, args.fix_threshold, args.min_delta, args.max_delta, args.step)
    print(
        f"[offset_scan] N={len(rows)}  推荐 Δ={best[1]:.2f} "
        f"(固定 {args.fix_threshold} κ={best[0]:.3f})"
    )
    print(
        f"{'Δ':>6} {'κ':>7} {'acc':>7} {'acc#':>5} {'rej#':>5} "
        f"{'FP':>4} {'FN':>4} {'accGrp':>7} {'rejGrp':>7}"
    )
    for r in results:
        if _is_show(r, best[1]):
            print(
                f"{r['delta']:>6.2f} {r['kappa']:>7.3f} {r['acc']:>7.3f} "
                f"{r['accept']:>5} {r['reject']:>5} {r['fp']:>4} {r['fn']:>4} "
                f"{r['accept_grp_mean']:>7.3f} {r['reject_grp_mean']:>7.3f}"
            )
    if args.out:
        json.dump(
            {
                "source": args.source_name,
                "n": len(rows),
                "fix_threshold": args.fix_threshold,
                "recommended_delta": best[1],
                "scan": results,
            },
            open(args.out, "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=2,
        )
        print(f"[offset_scan] 完整扫描 -> {args.out}")
    if args.recommend_out:
        json.dump(
            {args.source_name: best[1]},
            open(args.recommend_out, "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=2,
        )
        print(f"[offset_scan] 推荐偏移表 -> {args.recommend_out}")


if __name__ == "__main__":
    main()
