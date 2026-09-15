#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run DEPTH v4.2 evaluation on papers with Ornith-1.5-9B.

Incrementally saves results to CSV so progress is not lost on interruption.
"""
import asyncio
import csv
import os
import sys
import time

sys.path.insert(0, ".")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")

from mock_api.database import init_db, SessionLocal
from mock_api.models import Paper
from mock_api.depth_eval_v4 import DepthReviewer

OUT_CSV = "deliverables/ornith_depth_41.csv"
TARGET = 41


def load_done():
    if not os.path.exists(OUT_CSV):
        return set()
    with open(OUT_CSV, encoding="utf-8") as f:
        try:
            return {r["paper_id"] for r in csv.DictReader(f)}
        except Exception:
            return set()


def append_row(row):
    fresh = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        keys = list(row.keys())
        w = csv.DictWriter(f, fieldnames=keys)
        if fresh:
            w.writeheader()
        w.writerow(row)


def main():
    init_db()
    db = SessionLocal()

    papers = (
        db.query(Paper)
        .filter(Paper.full_text.isnot(None), Paper.full_text != "")
        .all()
    )
    papers = [p for p in papers if len(p.full_text or "") > 5000]

    done = load_done()
    todo = [p for p in papers if p.id not in done][:TARGET]
    total = len(done) + len(todo)
    print(f"Done: {len(done)}, Todo: {len(todo)}, Total target: {TARGET}")

    if not todo:
        print("All done!")
        return

    reviewer = DepthReviewer(compute_mode="speed")

    for i, p in enumerate(todo):
        t0 = time.time()
        try:
            result = asyncio.run(
                reviewer.review_async_dag(
                    paper_id=p.id,
                    title=p.title,
                    full_text=p.full_text[:16000],
                    abstract=p.abstract or "",
                )
            )
            d = result.model_dump()
            elapsed = time.time() - t0
            # Flatten to CSV-friendly
            row = {
                "paper_id": p.id,
                "title": p.title[:80],
                "novelty_score": d.get("novelty_score", ""),
                "rigor_score": d.get("rigor_score", ""),
                "influence_score": d.get("influence_score", ""),
                "reproducibility_score": d.get("reproducibility_score", ""),
                "base_score": d.get("base_score", ""),
                "calibrated_score": d.get("calibrated_score", ""),
                "delta": d.get("delta", ""),
                "llm_verdict": d.get("llm_verdict", ""),
                "final_verdict": d.get("final_verdict", ""),
                "time_s": round(elapsed, 1),
            }
            append_row(row)
            print(
                f"[{len(done)+i+1:2d}/{TARGET}] {p.id[:15]:15s} "
                f"base={d.get('base_score', 0):.3f} "
                f"cal={d.get('calibrated_score', 0):.3f} "
                f"verdict={d.get('final_verdict', '?')} "
                f"{elapsed:.0f}s"
            )
        except Exception as e:
            elapsed = time.time() - t0
            row = {
                "paper_id": p.id,
                "title": p.title[:80],
                "novelty_score": "",
                "rigor_score": "",
                "influence_score": "",
                "reproducibility_score": "",
                "base_score": "",
                "calibrated_score": "",
                "delta": "",
                "llm_verdict": "",
                "final_verdict": f"ERROR:{str(e)[:40]}",
                "time_s": round(elapsed, 1),
            }
            append_row(row)
            print(f"[{len(done)+i+1:2d}/{TARGET}] {p.id[:15]:15s} ERROR: {str(e)[:60]}")

    db.close()

    # Print summary
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        scores = [float(r["calibrated_score"]) for r in rows if r.get("calibrated_score")]
        verdicts = {}
        for r in rows:
            v = r.get("final_verdict", "?")
            verdicts[v] = verdicts.get(v, 0) + 1
        print(f"\n=== SUMMARY ({len(rows)} papers) ===")
        if scores:
            print(f"  mean={sum(scores)/len(scores):.3f}  min={min(scores):.3f}  max={max(scores):.3f}")
        print(f"  verdicts: {verdicts}")


if __name__ == "__main__":
    main()
