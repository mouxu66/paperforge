#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retry 40 UNKNOWN papers (completed reviews with empty verdict).

These papers had reviews that completed but the verdict derivation step
failed or was skipped. This script re-runs them using run_depth_review_sync.

Usage:
    python scripts/retry_unknown_40.py [--dry-run]
"""
import argparse
import os
import sys
import time

# Ensure figure evidence is enabled for QF
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED", "True")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.1")
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_api.database import init_db, SessionLocal
from mock_api.models import DepthReviewV4


def main():
    ap = argparse.ArgumentParser(description="Retry 40 UNKNOWN papers")
    ap.add_argument("--dry-run", action="store_true", help="Just list papers, don't run")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()

    # Find UNKNOWN papers: latest review has no score in final_verdict
    from sqlalchemy import func

    # Get all unique paper_ids with reviews
    all_pids = db.query(func.distinct(DepthReviewV4.paper_id)).all()
    all_pids = [r[0] for r in all_pids]

    unknown_pids = []
    for pid in all_pids:
        latest = db.query(DepthReviewV4).filter(
            DepthReviewV4.paper_id == pid
        ).order_by(DepthReviewV4.created_at.desc()).first()

        if latest and latest.status == "completed":
            fv = latest.final_verdict or {}
            has_score = bool(fv.get("calibrated_score") is not None or fv.get("base_score") is not None)
            if not has_score:
                unknown_pids.append(pid)

    db.close()

    print(f"Found {len(unknown_pids)} UNKNOWN papers to retry")

    if args.dry_run:
        for pid in unknown_pids:
            print(f"  {pid}")
        return

    from mock_api.depth_tasks import run_depth_review_sync

    success = 0
    failed = 0
    for i, pid in enumerate(unknown_pids, 1):
        t0 = time.time()
        try:
            run_depth_review_sync(pid)
            elapsed = time.time() - t0
            success += 1
            print(f"[{i}/{len(unknown_pids)}] OK {pid} ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            failed += 1
            print(f"[{i}/{len(unknown_pids)}] FAIL {pid}: {str(e)[:60]} ({elapsed:.1f}s)")

    print(f"\nDone: {success} ok, {failed} failed out of {len(unknown_pids)}")


if __name__ == "__main__":
    main()
