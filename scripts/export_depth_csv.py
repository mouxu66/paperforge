#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export DEPTH v4 review results to CSV.

Reads all DepthReviewV4 records from the database and writes a comprehensive CSV
with scores, verdicts, QF data, and metadata.

Usage:
    python scripts/export_depth_csv.py [--output PATH] [--status OK|FAIL|ALL]
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_api.database import init_db, SessionLocal
from mock_api.models import DepthReviewV4


def main():
    ap = argparse.ArgumentParser(description="Export DEPTH v4 results to CSV")
    ap.add_argument("--output", default="deliverables/depth_full_results.csv")
    ap.add_argument("--status", choices=["OK", "FAIL", "ALL"], default="ALL",
                    help="Filter by status: OK=completed with verdict, FAIL=failed, ALL=everything")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        rows = db.query(DepthReviewV4).order_by(DepthReviewV4.paper_id, DepthReviewV4.created_at).all()
    finally:
        db.close()

    # Deduplicate: keep latest review per paper_id
    latest = {}
    for r in rows:
        latest[r.paper_id] = r

    # Filter
    records = []
    for pid, r in latest.items():
        fv = r.final_verdict or {}
        has_verdict = bool(fv.get("calibrated_score") is not None or fv.get("base_score") is not None)
        is_failed = r.status == "failed" or (not has_verdict and r.error_message)

        if args.status == "OK" and not has_verdict:
            continue
        if args.status == "FAIL" and not is_failed:
            continue

        records.append((pid, r, fv, has_verdict, is_failed))

    # Build CSV rows
    fieldnames = [
        "paper_id", "title", "status", "compute_mode", "created_at",
        "base_score", "calibrated_score", "calibrated_score_raw", "offset_applied",
        "final_verdict", "llm_verdict",
        "novelty_score", "rigor_score", "influence_score", "reproducibility_score",
        "objective_score", "figure_consistency_score", "figure_flags", "figure_evidence_count",
        "qf_reasoning", "score_uncertainty", "error_message", "full_text_length",
    ]

    out_rows = []
    for pid, r, fv, has_verdict, is_failed in records:
        # Try to get title from the paper
        title = fv.get("title", "")
        if not title:
            # Attempt to get from node_stds or metadata
            title = fv.get("override_reason", "")[:80] if fv.get("override_reason") else ""

        # Extract dimension scores from node_score_stds if available
        node_stds = fv.get("node_score_stds", {})
        weights = fv.get("weights", {})

        row = {
            "paper_id": pid,
            "title": title,
            "status": r.status or ("failed" if is_failed else "completed"),
            "compute_mode": r.compute_mode or "",
            "created_at": str(r.created_at) if r.created_at else "",
            "base_score": fv.get("base_score", ""),
            "calibrated_score": fv.get("calibrated_score", ""),
            "calibrated_score_raw": fv.get("calibrated_score_raw", ""),
            "offset_applied": fv.get("offset_applied", ""),
            "final_verdict": fv.get("final_verdict", ""),
            "llm_verdict": fv.get("llm_verdict", ""),
            "novelty_score": node_stds.get("Q1", {}).get("novelty_score", ""),
            "rigor_score": node_stds.get("Q3", {}).get("rigor_score", ""),
            "influence_score": node_stds.get("Q4", {}).get("influence_score", ""),
            "reproducibility_score": node_stds.get("Q4", {}).get("reproducibility_score", ""),
            "objective_score": fv.get("objective_score", ""),
            "figure_consistency_score": fv.get("figure_consistency_score", ""),
            "figure_flags": "|".join(fv.get("figure_flags", [])) if isinstance(fv.get("figure_flags"), list) else fv.get("figure_flags", ""),
            "figure_evidence_count": fv.get("figure_evidence_count", ""),
            "qf_reasoning": (fv.get("qf_reasoning") or "")[:200],
            "score_uncertainty": fv.get("score_uncertainty", ""),
            "error_message": r.error_message or "",
            "full_text_length": fv.get("full_text_length", ""),
        }
        out_rows.append(row)

    # Write CSV
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    # Summary
    scores = [float(r["calibrated_score"]) for r in out_rows if r["calibrated_score"]]
    verdicts = {}
    for r in out_rows:
        v = r["final_verdict"] or ("FAILED" if r["status"] == "failed" else "UNKNOWN")
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"Exported {len(out_rows)} records to {args.output}")
    if scores:
        print(f"  Score: mean={sum(scores)/len(scores):.3f} min={min(scores):.3f} max={max(scores):.3f}")
    print(f"  Verdicts: {json.dumps(verdicts, ensure_ascii=False)}")

    # QF stats
    qf_non_neutral = sum(1 for r in out_rows if r["figure_consistency_score"] and r["figure_consistency_score"] != "" and abs(float(r["figure_consistency_score"]) - 0.5) > 1e-6)
    qf_total = sum(1 for r in out_rows if r["figure_consistency_score"] and r["figure_consistency_score"] != "")
    print(f"  QF: {qf_non_neutral}/{qf_total} non-neutral")


if __name__ == "__main__":
    main()
