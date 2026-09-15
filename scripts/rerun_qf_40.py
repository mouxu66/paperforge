#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-run 40 figure papers with QF enabled.

Resumes from papers that haven't been re-run yet.
"""
import os
import sys
import time

os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED", "True")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.1")
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_api.database import init_db, SessionLocal
from mock_api.models import DepthReviewV4, PaperFigure
from mock_api.depth_tasks import run_depth_review_sync
from sqlalchemy import func


def main():
    init_db()
    db = SessionLocal()

    # Get all papers with PaperFigure records
    pfigs = db.query(func.distinct(PaperFigure.paper_id)).all()
    all_pids = set(r[0] for r in pfigs)

    # Find which ones already have recent QF (non-neutral or neutral with QF score)
    recent_qf = set()
    for pid in all_pids:
        latest = db.query(DepthReviewV4).filter(
            DepthReviewV4.paper_id == pid
        ).order_by(DepthReviewV4.created_at.desc()).first()
        if latest:
            fv = latest.final_verdict or {}
            if fv.get("figure_consistency_score") is not None:
                recent_qf.add(pid)

    todo = sorted(all_pids - recent_qf)
    db.close()

    print(f"Figure papers: {len(all_pids)}, Already have QF: {len(recent_qf)}, Todo: {len(todo)}")

    if not todo:
        print("All done!")
        return

    success = 0
    failed = 0
    for i, pid in enumerate(todo, 1):
        t0 = time.time()
        try:
            run_depth_review_sync(pid)
            elapsed = time.time() - t0
            success += 1
            print(f"[{i}/{len(todo)}] OK {pid} ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            failed += 1
            print(f"[{i}/{len(todo)}] FAIL {pid}: {str(e)[:60]} ({elapsed:.1f}s)")

    print(f"\nDone: {success} ok, {failed} failed out of {len(todo)}")


if __name__ == "__main__":
    main()
