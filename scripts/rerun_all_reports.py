"""批量重评全部感悟报告（带原论文绑定，逐篇落盘）。

用法：
    PAPERFORGE_LOCAL_ML=1 python scripts/rerun_all_reports.py
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.reflection_pipeline import analyze_reflection_file
from mock_api.database import SessionLocal
from mock_api.models import Paper as PaperORM

UPLOADS = ROOT / "mock_api" / "uploads"
OUT_CSV = ROOT / "deliverables" / "rerun_all_41.csv"
FIELDS = [
    "sid", "file", "avg", "verdict",
    "ua", "ad", "ii", "es",
    "fidelity", "coverage",
    "ev_count", "overrides",
    "bound", "paper_chars", "report_chars",
    "llm_failed", "elapsed_s",
]


def write_row(row: dict):
    """Append one row to CSV immediately."""
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    exists = OUT_CSV.exists()
    with open(OUT_CSV, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


def main():
    files = sorted(UPLOADS.glob("reflection_*.docx"))
    total = len(files)
    print(f"Found {total} report files\n", flush=True)

    db = SessionLocal()

    for i, fpath in enumerate(files, 1):
        m = re.search(r"(\d{12})", fpath.name)
        sid = m.group(1) if m else "???"

        src_pid = None
        if sid != "???":
            paper = db.query(PaperORM).filter(PaperORM.id.like(f"%{sid}%")).first()
            src_pid = getattr(paper, "source_paper_id", None) if paper else None

        t0 = time.time()
        try:
            result = analyze_reflection_file(str(fpath), db, source_paper_id=src_pid)
            elapsed = time.time() - t0
            s = result.get("scores", {})
            avg = result.get("average")
            verdict = result.get("verdict", "")
            ov = result.get("hardcoded_overrides", [])
            bound = result.get("bound_paper_id", "")
            pc = result.get("paper_chars", 0)
            rc = result.get("report_chars", 0)
            llm_fail = result.get("llm_failed", False)

            row = {
                "sid": sid, "file": fpath.name,
                "avg": round(avg, 4) if avg is not None else None,
                "verdict": verdict,
                "ua": round(s.get("understanding_accuracy") or 0, 3),
                "ad": round(s.get("analysis_depth") or 0, 3),
                "ii": round(s.get("innovative_insights") or 0, 3),
                "es": round(s.get("evidence_support") or 0, 3),
                "fidelity": round(s.get("fidelity") or 0, 3),
                "coverage": round(s.get("coverage") or 0, 3),
                "ev_count": result.get("effective_evidence_count", 0),
                "overrides": "; ".join(ov) if ov else "",
                "bound": bound or "",
                "paper_chars": pc,
                "report_chars": rc,
                "llm_failed": llm_fail,
                "elapsed_s": round(elapsed, 1),
            }
            print(f"[{i}/{total}] {sid}  {elapsed:.0f}s  avg={avg}  verdict={verdict}  bound={bound}  pc={pc}  rc={rc}", flush=True)
            write_row(row)
        except Exception as e:
            elapsed = time.time() - t0
            print(f"[{i}/{total}] {sid}  {elapsed:.0f}s  ERROR: {e}", flush=True)
            row = {
                "sid": sid, "file": fpath.name,
                "avg": None, "verdict": "ERROR",
                "ua": 0, "ad": 0, "ii": 0, "es": 0,
                "fidelity": 0, "coverage": 0,
                "ev_count": 0, "overrides": str(e)[:200],
                "bound": "", "paper_chars": 0, "report_chars": 0,
                "llm_failed": True, "elapsed_s": round(elapsed, 1),
            }
            write_row(row)

    db.close()
    print(f"\nDone! Saved: {OUT_CSV}")


if __name__ == "__main__":
    main()
