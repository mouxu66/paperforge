"""影子校准预研：GLM 独立评分 → 分位数(单调)映射 → 留一交叉验证。

目的（非部署）：回答"GLM 的粗排序信号能否通过校准映射产生足够可靠的连续分，
还是只能用于本地分歧时提示人工(哨兵)"。

数据流：
  1. 加载 16 篇金标（calib_set_20 中仍在库、且有 full_text 的论文）。
  2. 用「已修 prompt 锚定」的 GLM 独立评分（不触发任何 override / rescue），得 cloud_raw。
  3. 单调映射：对每篇 i 留一(LOO)，用其余 15 篇拟合 monotone piecewise map
     (np.interp: cloud -> gold)，预测第 i 篇的 mapped_score。
  4. 计算 GLM raw / GLM mapped(LOO) / 本地无覆盖 三者的 MAE / Cohen κ / Pearson r。
  5. 落盘 JSON，供报告与散点图使用。

用法：
  .venv/Scripts/python.exe -u scripts/calibration/glm_shadow_eval.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mock_api import depth_calibration as dc  # noqa: E402
from mock_api import second_opinion as so  # noqa: E402
from mock_api.database import SessionLocal  # noqa: E402
from mock_api.models import DepthReviewV4  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GOLD_JSON = os.path.join(ROOT, "calib_papers", "runs", "calib_set_20.json")
PRIOR_RUN = os.path.join(ROOT, "deliverables", "cloud_hybrid_vs_gold_20260817_213822.json")
DB = os.path.join(ROOT, "mock_api", "paperforge_mock.db")
OUT_JSON = os.path.join(ROOT, "deliverables", "glm_shadow_eval_20260817.json")


def to_verdict(s: float) -> str:
    return dc.offset_corrected_verdict(float(s), 0.0)


def cohen_kappa(a, b):
    a = list(a)
    b = list(b)
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labs = sorted(set(a) | set(b))
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def pearson(x, y):
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx = sum((xi - mx) ** 2 for xi in x)
    vy = sum((yi - my) ** 2 for yi in y)
    if vx == 0 or vy == 0:
        return float("nan")
    return cov / (vx ** 0.5 * vy ** 0.5)


def mae(x, y):
    return sum(abs(a - b) for a, b in zip(x, y)) / len(x)


def monotone_map_predict(train_cloud, train_gold, target_cloud):
    """用训练集拟合 monotone piecewise-linear map cloud->gold，预测 target。
    处理高位堆叠/并列：np.interp 在平坦区返回平坦值。"""
    order = np.argsort(train_cloud)
    sc = np.array(train_cloud)[order]
    sg = np.array(train_gold)[order]
    return float(np.interp(float(target_cloud), sc, sg))


def main():
    # ---- 1) gold ----
    set20 = json.load(open(GOLD_JSON, encoding="utf-8"))
    gold = {s["paper_id"]: (float(s["expert_scores"]["final"]), s["expert_verdict"]) for s in set20}

    # ---- 2) 16 篇金标 present + full_text ----
    con = sqlite3.connect(DB)
    cur = con.cursor()
    ph = ",".join("?" * len(gold))
    cur.execute(f"SELECT id, full_text FROM papers WHERE id IN ({ph})", list(gold.keys()))
    rows = cur.fetchall()
    con.close()
    present = {r[0]: (r[1] or "") for r in rows if len(r[1] or "") > 50}
    print(f"present with text: {len(present)}/{len(gold)}")

    # ---- 本地无覆盖分（同 16 篇，来自上次 cloud_hybrid 运行 DB 记录）----
    prior = json.load(open(PRIOR_RUN, encoding="utf-8"))
    local_by_pid = {}
    db = SessionLocal()
    for rp in prior["per_paper"]:
        pid = rp["pid"]
        rec = db.query(DepthReviewV4).filter(DepthReviewV4.id == rp["rid"]).first()
        if not rec:
            continue
        fv = rec.final_verdict or {}
        cc = fv.get("cross_check") or {}
        local_by_pid[pid] = float(cc.get("original_score") or fv.get("calibrated_score_raw") or fv.get("calibrated_score"))
    db.close()

    # ---- 3) GLM 独立评分（修 prompt 后）----
    samples = []
    for pid, txt in present.items():
        o = so.get_second_opinion(txt[:6000], kind="paper")
        if not o.get("enabled") or o.get("score") is None:
            print(f"  SKIP {pid}: {o.get('skipped')}")
            continue
        gf, gv = gold[pid]
        cs = float(o["score"])
        cv = to_verdict(cs)
        ls = local_by_pid.get(pid)
        samples.append({
            "pid": pid,
            "gold_score": gf,
            "gold_verdict": gv,
            "cloud_raw_score": cs,
            "cloud_raw_verdict": cv,
            "local_score": ls,
        })
        print(f"  {pid}: gold={gf:.3f}({gv}) cloud={cs:.3f}({cv}) local={ls}")

    n = len(samples)
    gold_v = [s["gold_verdict"] for s in samples]
    gold_s = [s["gold_score"] for s in samples]

    # ---- 4) LOO 单调映射 ----
    for i, s in enumerate(samples):
        others = [x for j, x in enumerate(samples) if j != i]
        tc = [x["cloud_raw_score"] for x in others]
        tg = [x["gold_score"] for x in others]
        mapped = monotone_map_predict(tc, tg, s["cloud_raw_score"])
        s["mapped_score"] = mapped
        s["mapped_verdict"] = to_verdict(mapped)

    cloud_raw_s = [s["cloud_raw_score"] for s in samples]
    cloud_raw_v = [s["cloud_raw_verdict"] for s in samples]
    mapped_s = [s["mapped_score"] for s in samples]
    mapped_v = [s["mapped_verdict"] for s in samples]
    local_s = [s["local_score"] for s in samples if s["local_score"] is not None]
    local_v = [to_verdict(x) for x in local_s]

    metrics = {
        "glm_raw": {
            "mae": round(mae(cloud_raw_s, gold_s), 4),
            "kappa": round(cohen_kappa(cloud_raw_v, gold_v), 4),
            "pearson": round(pearson(cloud_raw_s, gold_s), 4),
        },
        "glm_mapped_loo": {
            "mae": round(mae(mapped_s, gold_s), 4),
            "kappa": round(cohen_kappa(mapped_v, gold_v), 4),
            "pearson": round(pearson(mapped_s, gold_s), 4),
        },
        "local_no_override": {
            "mae": round(mae(local_s, gold_s), 4) if local_s else None,
            "kappa": round(cohen_kappa(local_v, gold_v), 4) if local_v else None,
            "pearson": round(pearson(local_s, gold_s), 4) if local_s else None,
        },
    }

    # ---- 5) 全量拟合映射曲线（用于绘图，非 LOO）----
    sc = np.array(cloud_raw_s)
    sg = np.array(gold_s)
    order = np.argsort(sc)
    map_curve = {
        "cloud_sorted": [round(float(x), 4) for x in sc[order]],
        "gold_sorted": [round(float(x), 4) for x in sg[order]],
    }

    # 分布堆叠统计
    from collections import Counter
    buckets = Counter()
    for c in cloud_raw_s:
        if c < 0.4:
            buckets["<0.4 (reject)"] += 1
        elif c < 0.7:
            buckets["0.4-0.7 (minor)"] += 1
        elif c < 0.8:
            buckets["0.7-0.8 (major)"] += 1
        else:
            buckets[">=0.8 (accept)"] += 1

    out = {
        "n": n,
        "metrics": metrics,
        "cloud_distribution": dict(buckets),
        "map_curve": map_curve,
        "samples": samples,
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n=== METRICS ===")
    for k, v in metrics.items():
        print(f"{k:20} MAE={v['mae']}  κ={v['kappa']}  r={v['pearson']}")
    print("cloud distribution:", dict(buckets))
    print(f"written -> {OUT_JSON}")


if __name__ == "__main__":
    main()
