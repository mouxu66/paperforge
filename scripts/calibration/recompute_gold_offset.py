"""用真实盲评金标数据集重算 DEPTH 偏移（ADR-014 P5 落地）。

数据来源（本仓既有，非合成）：
  - calib_papers/runs/calib_set_20.json   : 20 篇分层抽样论文的「我的盲评」金标
                                            （CalibrationSample 格式，含 5 维分/最终分/verdict）
  - calib_papers/runs/calib_pool_415.json : 415 篇 DEPTH 全量分数（pid -> score）

做法：
  1. 从 calib_pool_415.json 取 20 篇的 DEPTH 分，构造 score_fn（与金标顺序一致）
  2. auto_offset_from_calibration 在金标上搜「最大化 verdict Cohen κ」的偏移 δ（数据最优）
  3. 同时报告已采用的稳健值 -0.09（三方印证：本报告 / 另一次 19 样本 / 系统偏高+0.12）对应 κ
  4. 落盘 calib_papers/runs/gold_offset.json，供 PAPERFORGE_DEPTH_GOLD_OFFSET_PATH 指向

不重跑 LLM——只基于已有 DEPTH 分数与人工盲评金标做偏移回归。
用法：
  python scripts/calibration/recompute_gold_offset.py
"""
import importlib.util
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVIEW_SET = os.path.join(ROOT, "calib_papers", "runs", "calib_set_20.json")
POOL = os.path.join(ROOT, "calib_papers", "runs", "calib_pool_415.json")
OUT = os.path.join(ROOT, "calib_papers", "runs", "gold_offset.json")
ADOPTED_OFFSET = -0.09  # 用户采用的稳健值（三方印证，见 blind_review_comparison_2026-07-24.md §6/§8.4）


def _load_dc():
    import sys
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import mock_api.depth_calibration as dc  # 以包方式导入，使 .utils 相对导入可用
    return dc


def _kappa(a, b):
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labs = sorted(set(a) | set(b))
    pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return (po - pa) / (1 - pa) if pa < 1 else 1.0


def main():
    dc = _load_dc()
    review = json.load(open(REVIEW_SET, encoding="utf-8"))
    pool = json.load(open(POOL, encoding="utf-8"))
    pm = {r["pid"]: r["score"] for r in pool}

    # 与 run_offset_full415.py 同一 proven pattern：手动构造 CalibrationSample
    samples = []
    for d in review:
        samples.append(
            dc.CalibrationSample(
                paper_id=d["paper_id"],
                text_hash=d.get("text_hash", ""),
                expert_scores=d.get("expert_scores", {}),
                expert_verdict=d["expert_verdict"],
                weight=d.get("weight", 1.0),
            )
        )
    pids = [s.paper_id for s in samples]
    depth_raw = [pm[p] for p in pids]  # 与 samples 顺序一致
    my_v = [s.expert_verdict for s in samples]

    def score_fn():
        return depth_raw

    best_o, best_k = dc.auto_offset_from_calibration(samples, score_fn)
    k_raw = _kappa([dc.offset_corrected_verdict(s, 0.0) for s in depth_raw], my_v)
    k_adopted = _kappa([dc.offset_corrected_verdict(s, ADOPTED_OFFSET) for s in depth_raw], my_v)
    agree_adopted = sum(
        1 for x, y in zip([dc.offset_corrected_verdict(s, ADOPTED_OFFSET) for s in depth_raw], my_v) if x == y
    )

    print("=== 真实盲评金标偏移回归（n=%d）===" % len(samples))
    print(f"  偏移前 (δ=0)             κ = {k_raw:.3f}")
    print(f"  数据最优 δ={best_o:+.2f}       κ = {best_k:.3f}  (20 小样本过拟合最优，不采用)")
    print(f"  已采用稳健 δ={ADOPTED_OFFSET:+.2f}  κ = {k_adopted:.3f}  (三方印证，沿用；{agree_adopted}/{len(samples)} 一致)")

    payload = {
        "recommended_offset": round(best_o, 3),
        "recommended_kappa": round(best_k, 4),
        "adopted_offset": ADOPTED_OFFSET,
        "adopted_kappa": round(k_adopted, 4),
        "kappa_before": round(k_raw, 4),
        "n_samples": len(samples),
        "source": "calib_my_review_2026-07-24 (blind_review_comparison, 20 stratified samples)",
        "note": "recommended_offset 为 20 样本数据最优（过拟合，不采用）；生产沿用 adopted_offset=-0.09（三方印证稳健值）。",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已落盘: {OUT}")


if __name__ == "__main__":
    main()
