#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run DEPTH on the 16 gold standard papers using Ornith-1.5-9B,
then update calib_pool_415.json and recompute gold_offset.json."""
import asyncio
import csv
import json
import os
import sys
import time

sys.path.insert(0, ".")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")

from mock_api.database import init_db, SessionLocal
from mock_api.models import Paper
from mock_api.depth_eval_v4 import DepthReviewer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVIEW_SET = os.path.join(ROOT, "calib_papers", "runs", "calib_set_20.json")
POOL = os.path.join(ROOT, "calib_papers", "runs", "calib_pool_415.json")
OUT_POOL = os.path.join(ROOT, "calib_papers", "runs", "calib_pool_415_ornith.json")
OUT_OFFSET = os.path.join(ROOT, "calib_papers", "runs", "gold_offset_ornith.json")
PROGRESS = os.path.join(ROOT, "calib_papers", "runs", "ornith_gold_progress.json")
CSV_OUT = os.path.join(ROOT, "deliverables", "ornith_gold_16.csv")


def load_progress():
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(progress):
    with open(PROGRESS, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def main():
    init_db()
    db = SessionLocal()

    # Load gold standard
    review = json.load(open(REVIEW_SET, encoding="utf-8"))
    gold_pids = [r["paper_id"] for r in review]

    # Find papers in DB
    valid = []
    for pid in gold_pids:
        paper = db.query(Paper).filter(Paper.id == pid).first()
        if paper and paper.full_text and len(paper.full_text) > 1000:
            valid.append((pid, paper))
        else:
            print(f"  SKIP {pid} (not in DB or no full_text)")

    print(f"Gold standard: {len(gold_pids)} total, {len(valid)} valid\n")

    progress = load_progress()
    reviewer = DepthReviewer(compute_mode="speed")
    # 强制贪心 @0.0 off：PAPERFORGE_DEPTH_TEMPERATURE=0 因 depth_eval_v4:1249 的哨兵
    # bug（0.0 被视为"沿用 preset"）不生效，故直接覆盖实例温度。consensus 节点
    # 默认 CONSENSUS_TEMPERATURE=0.0，亦为贪心。
    reviewer._temperature = 0.0

    for i, (pid, paper) in enumerate(valid):
        if pid in progress:
            old = progress[pid]
            print(f"  [{i+1}/{len(valid)}] {pid} -> CACHED base={old['base_score']:.3f}")
            continue

        print(f"  [{i+1}/{len(valid)}] {pid} running...", end="", flush=True)
        t0 = time.time()
        try:
            result = asyncio.run(
                reviewer.review_async_dag(
                    paper_id=paper.id,
                    title=paper.title,
                    full_text=paper.full_text[:16000],
                    abstract=paper.abstract or "",
                )
            )
            d = result.model_dump()
            elapsed = time.time() - t0
            entry = {
                "base_score": d.get("base_score", 0),
                "calibrated_score": d.get("calibrated_score", 0),
                "novelty_score": d.get("novelty_score", 0),
                "rigor_score": d.get("rigor_score", 0),
                "influence_score": d.get("influence_score", 0),
                "final_verdict": d.get("final_verdict", ""),
            }
            progress[pid] = entry
            save_progress(progress)
            print(f" base={entry['base_score']:.3f} cal={entry['calibrated_score']:.3f} verdict={entry['final_verdict']} ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            print(f" ERROR: {e} ({elapsed:.0f}s)")
            progress[pid] = {"base_score": 0.5, "calibrated_score": 0.5, "final_verdict": "error"}
            save_progress(progress)

    db.close()

    # === Update pool ===
    print("\n=== Updating pool ===")
    pool = json.load(open(POOL, encoding="utf-8"))
    new_pool = []
    replaced = 0
    for p in pool:
        entry = dict(p)
        if p["pid"] in progress:
            ornith = progress[p["pid"]]
            entry["score"] = ornith["base_score"]
            replaced += 1
        new_pool.append(entry)

    with open(OUT_POOL, "w", encoding="utf-8") as f:
        json.dump(new_pool, f, ensure_ascii=False, indent=2)
    print(f"  Saved: {OUT_POOL} ({replaced} scores replaced)")

    # === Recompute gold offset ===
    print("\n=== Recomputing gold offset ===")
    sys.path.insert(0, ROOT)
    import mock_api.depth_calibration as dc

    samples = []
    for d in review:
        if d["paper_id"] in progress:
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
    depth_raw = [progress[p]["base_score"] for p in pids]
    my_v = [s.expert_verdict for s in samples]

    def _kappa(a, b):
        n = len(a)
        if n == 0:
            return 0.0
        po = sum(1 for x, y in zip(a, b) if x == y) / n
        labs = sorted(set(a) | set(b))
        pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
        return (po - pa) / (1 - pa) if pa < 1 else 1.0

    def score_fn():
        return depth_raw

    best_o, best_k = dc.auto_offset_from_calibration(samples, score_fn)

    # Fine-grained search
    for delta in [i / 200.0 for i in range(-60, 61)]:
        k = _kappa([dc.offset_corrected_verdict(s, delta) for s in depth_raw], my_v)
        if k > best_k:
            best_k = k
            best_o = delta

    k_raw = _kappa([dc.offset_corrected_verdict(s, 0.0) for s in depth_raw], my_v)
    OLD_ADOPTED = -0.09
    k_old = _kappa([dc.offset_corrected_verdict(s, OLD_ADOPTED) for s in depth_raw], my_v)

    print(f"  Samples: {len(samples)}/{len(review)}")
    print(f"\n  {'PID':<30} {'Base':>8} {'Expert':>15} {'Old(-0.09)':>12} {'New({:+.2f})'.format(best_o):>12}")
    print(f"  {'-'*30} {'-'*8} {'-'*15} {'-'*12} {'-'*12}")
    for pid in pids:
        base = progress[pid]["base_score"]
        expert = [s.expert_verdict for s in samples if s.paper_id == pid][0]
        old_v = dc.offset_corrected_verdict(base, OLD_ADOPTED)
        new_v = dc.offset_corrected_verdict(base, best_o)
        print(f"  {pid:<30} {base:>8.3f} {expert:>15} {old_v:>12} {new_v:>12}")

    print(f"\n  Raw (no offset)   kappa = {k_raw:.3f}")
    print(f"  Old offset -0.09  kappa = {k_old:.3f}")
    print(f"  New offset {best_o:+.2f}  kappa = {best_k:.3f}")

    payload = {
        "model": "Ornith-1.5-9B-Q4_K_M",
        "recommended_offset": round(best_o, 3),
        "recommended_kappa": round(best_k, 4),
        "adopted_offset": round(best_o, 3),
        "kappa_before_raw": round(k_raw, 4),
        "kappa_before_old_offset": round(k_old, 4),
        "n_samples": len(samples),
        "old_model_offset": OLD_ADOPTED,
        "note": f"Recalibrated for Ornith-1.5-9B. Optimal offset={best_o:+.3f} (kappa={best_k:.3f}) vs old -0.09 (kappa={k_old:.3f})",
    }

    with open(OUT_OFFSET, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n  Saved: {OUT_OFFSET}")

    # Also save to CSV for easy viewing
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pid", "base_score", "expert_verdict", "old_verdict", "new_verdict", "match"])
        for pid in pids:
            base = progress[pid]["base_score"]
            expert = [s.expert_verdict for s in samples if s.paper_id == pid][0]
            old_v = dc.offset_corrected_verdict(base, OLD_ADOPTED)
            new_v = dc.offset_corrected_verdict(base, best_o)
            w.writerow([pid, f"{base:.3f}", expert, old_v, new_v, "Y" if new_v == expert else "N"])
    print(f"  CSV: {CSV_OUT}")


if __name__ == "__main__":
    main()
