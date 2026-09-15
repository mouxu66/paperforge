"""云端混合 DEPTH vs 盲评金标 严格复算（head-to-head）。

目的：
  在 20 篇盲评金标样本上，重跑「云端混合版 DEPTH」（本地 8080 主评审 + GLM 云端覆盖），
  复算与人工盲评金标的 Cohen κ / MAE / Pearson r，并与「纯本地模型」基线并排对比。

数据来源（本仓既有，非合成）：
  - calib_papers/runs/calib_set_20.json : 20 篇分层抽样盲评金标
                                           （paper_id / expert_scores.final / expert_verdict）
  - calib_papers/runs/calib_pool_415.json : 415 篇 DEPTH 全量原始分（pid -> score，偏移前）
  - 活跃 DB（mock_api/paperforge_mock.db）: 真实运行 DEPTH 流水线，写 depth_reviews_v4

口径说明：
  - 20 篇中有 4 篇 report_* pid 已不在 papers 表（被删），故实际复跑 16 篇交集。
  - 云端混合分取自 DepthReviewV4.final_verdict.calibrated_score（已含 -0.09 偏移，
    云端覆盖时被 resolved_score 替换）。verdict 用 offset_corrected_verdict(score, 0.0) 推导。
  - 纯本地基线取自 calib_pool_415 同 16 篇原始分 + 外部 -0.09 偏移
    （与 blind_review_comparison_2026-07-24.md 算得 κ=0.375 的算法一致），
    作为「我单纯的本地模型」参照。历史 20 篇纯本地 κ=0.375 / MAE=0.171 / Pearson=0.820 作为参考锚点。
  - 局限：云端混合的本地主评审（8080）与当年 calib_pool 生成时并非同一 8080 状态，
    故「云端 - 纯本地」差值包含 8080 漂移，结论以 κ/MAE 绝对值 + 与历史锚点对比为准。

用法：
  python scripts/calibration/recompute_cloud_hybrid_gold.py --n 1     # 探针 1 篇
  python scripts/calibration/recompute_cloud_hybrid_gold.py --n 16    # 全量（后台跑）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
# 批量确定性评估：禁用 figure 触发（避免 OCR 同进程 segfault 拖垮进程）
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("ENV", "development")

SET20 = ROOT / "calib_papers" / "runs" / "calib_set_20.json"
POOL = ROOT / "calib_papers" / "runs" / "calib_pool_415.json"
ADOPTED_OFFSET = -0.09


def _cohen_kappa(a, b):
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labs = sorted(set(a) | set(b))
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def _pearson(x, y):
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx = sum((xi - mx) ** 2 for xi in x)
    vy = sum((yi - my) ** 2 for yi in y)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx ** 0.5 * vy ** 0.5)


def _clamp01(v):
    return max(0.0, min(1.0, float(v)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=16, help="复跑篇数（交集内，默认 16）")
    ap.add_argument("--out", default=None, help="结果 json 输出路径")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else ROOT / "deliverables" / f"cloud_hybrid_vs_gold_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    # ── 加载金标 + 纯本地池 ──
    gold = json.load(open(SET20, encoding="utf-8"))
    gmap = {d["paper_id"]: (d["expert_scores"].get("final"), d["expert_verdict"]) for d in gold}
    pool = json.load(open(POOL, encoding="utf-8"))
    pm = {p["pid"]: p["score"] for p in pool}

    # ── DB 取交集 ──
    from mock_api.database import init_db, SessionLocal
    from mock_api.models import DepthReviewV4, Paper

    init_db()
    db = SessionLocal()
    found = [pid for pid in gmap if db.query(Paper).filter(Paper.id == pid).first() is not None]
    db.close()
    found = found[: args.n]

    import mock_api.depth_tasks as depth_tasks
    from mock_api import depth_calibration as dc

    print(f"[harness] 金标 {len(gmap)} 篇；库内交集 {len(found)} 篇；本次复跑 {len(found)} 篇", flush=True)

    results = []
    for i, pid in enumerate(found, 1):
        t0 = time.time()
        try:
            rid = depth_tasks.run_depth_review_sync(pid)
            db = SessionLocal()
            rec = db.query(DepthReviewV4).filter(DepthReviewV4.id == rid).first()
            fv = (rec.final_verdict or {}) if rec else {}
            cscore = fv.get("calibrated_score")
            cross = fv.get("cross_check") or {}
            db.close()
            if cscore is None:
                results.append({"pid": pid, "rid": rid, "error": "no calibrated_score",
                                "secs": round(time.time() - t0, 1)})
            else:
                cscore = _clamp01(cscore)
                verdict = dc.offset_corrected_verdict(cscore, 0.0)
                results.append({
                    "pid": pid, "rid": rid,
                    "cloud_score": cscore, "cloud_verdict": verdict,
                    "so_enabled": cross.get("enabled"),
                    "so_corrected": cross.get("corrected"),
                    "so_flag": cross.get("flag"),
                    "so_provider": cross.get("second_provider"),
                    "secs": round(time.time() - t0, 1),
                })
        except Exception as e:
            results.append({"pid": pid, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})
        # 实时落盘，防中途崩溃丢结果
        json.dump({"n_total": len(found), "done": len(results), "items": results},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        ok = sum(1 for r in results if "error" not in r)
        print(f"  [{i}/{len(found)}] {pid} -> {('err:'+results[-1]['error']) if 'error' in results[-1] else ('score=%.3f verdict=%s so_corr=%s' % (results[-1]['cloud_score'], results[-1]['cloud_verdict'], results[-1]['so_corrected']))}  ({results[-1]['secs']}s)", flush=True)

    # ── 指标 ──
    gold_v, gold_s, cloud_v, cloud_s, pure_v, pure_s = [], [], [], [], [], []
    for r in results:
        if "error" in r:
            continue
        pid = r["pid"]
        gfinal, gverdict = gmap[pid]
        gold_v.append(gverdict)
        gold_s.append(float(gfinal))
        cloud_v.append(r["cloud_verdict"])
        cloud_s.append(r["cloud_score"])
        raw = pm.get(pid)
        if raw is None:
            continue
        pscore = _clamp01(float(raw) + ADOPTED_OFFSET)
        pverdict = dc.offset_corrected_verdict(float(raw), ADOPTED_OFFSET)
        pure_v.append(pverdict)
        pure_s.append(pscore)

    def _mse_block(vs, gs):
        n = len(vs)
        mae = (sum(abs(a - b) for a, b in zip(vs, gs)) / n) if n else 0.0
        return n, mae

    n_c, mae_c = _mse_block(cloud_s, gold_s)
    n_p, mae_p = _mse_block(pure_s, gold_s)
    metrics = {
        "n_cloud_hybrid": n_c,
        "n_pure_local": n_p,
        "kappa_cloud_hybrid": round(_cohen_kappa(cloud_v, gold_v), 4),
        "kappa_pure_local": round(_cohen_kappa(pure_v, gold_v), 4),
        "mae_cloud_hybrid": round(mae_c, 4),
        "mae_pure_local": round(mae_p, 4),
        "pearson_cloud_hybrid": round(_pearson(cloud_s, gold_s), 4),
        "pearson_pure_local": round(_pearson(pure_s, gold_s), 4),
        "reference_historical_20_pure_local": {"kappa": 0.375, "mae": 0.171, "pearson": 0.820},
        "adopted_offset": ADOPTED_OFFSET,
        "n_gold_total": len(gmap),
        "n_db_intersection": len(found),
        "n_missing_from_db": len(gmap) - len(found),
        "missing_pids": [pid for pid in gmap if pid not in set(found)],
    }

    payload = {"metrics": metrics, "per_paper": results, "gold": {k: list(v) for k, v in gmap.items()}}
    json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n===== 指标（16 篇交集）=====", flush=True)
    print(f"  云端混合 κ={metrics['kappa_cloud_hybrid']:.3f}  MAE={metrics['mae_cloud_hybrid']:.3f}  r={metrics['pearson_cloud_hybrid']:.3f}  (n={n_c})", flush=True)
    print(f"  纯本地   κ={metrics['kappa_pure_local']:.3f}  MAE={metrics['mae_pure_local']:.3f}  r={metrics['pearson_pure_local']:.3f}  (n={n_p})", flush=True)
    print(f"  历史20篇纯本地参考: κ=0.375 MAE=0.171 r=0.820", flush=True)
    print(f"  results -> {out}", flush=True)


if __name__ == "__main__":
    main()
