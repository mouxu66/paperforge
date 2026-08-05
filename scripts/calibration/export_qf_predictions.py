#!/usr/bin/env python3
"""Export QF figure-consistency predictions from DepthReviewV4 records.

Reads a task file (produced by export_figure_annotation_tasks.py), looks up the
latest DEPTH v4 review for each paper, and writes a JSONL of predictions aligned
by task_id.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import DepthReviewV4  # noqa: E402


def _latest_review_for_paper(db, paper_id: str) -> DepthReviewV4 | None:
    return (
        db.query(DepthReviewV4)
        .filter(DepthReviewV4.paper_id == paper_id, DepthReviewV4.kind == "paper")
        .order_by(DepthReviewV4.created_at.desc())
        .first()
    )


def export_predictions(tasks_path: Path, output_path: Path) -> int:
    tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not tasks:
        print("No tasks found in input file.", file=sys.stderr)
        return 0

    with SessionLocal() as db, output_path.open("w", encoding="utf-8") as f:
        review_cache: dict[str, DepthReviewV4 | None] = {}
        for task in tasks:
            paper_id = task["paper_id"]
            review = review_cache.get(paper_id)
            if review is None and paper_id not in review_cache:
                review = _latest_review_for_paper(db, paper_id)
                review_cache[paper_id] = review
            prediction: dict[str, object] = {
                "task_id": task["task_id"],
                "paper_id": paper_id,
            }
            if review is None or not review.final_verdict:
                prediction["figure_consistency_score"] = 0.5
                prediction["inconsistency_flags"] = []
                prediction["figure_coverage"] = "missing"
            else:
                verdict = review.final_verdict
                prediction["figure_consistency_score"] = float(
                    verdict.get("figure_consistency_score", 0.5)
                )
                prediction["inconsistency_flags"] = list(
                    verdict.get("figure_flags", []) or []
                )
                prediction["figure_coverage"] = verdict.get("figure_coverage", "missing")
            f.write(json.dumps(prediction, ensure_ascii=False) + "\n")

    print(f"Exported predictions for {len(tasks)} tasks to {output_path}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export QF predictions from DepthReviewV4 for a set of annotation tasks.",
    )
    parser.add_argument(
        "--tasks",
        required=True,
        type=str,
        help="Task JSONL file (e.g., annotations/tasks.jsonl).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=str,
        help="Output JSONL path (e.g., annotations/qf_predictions.jsonl).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    tasks_path = Path(args.tasks)
    output_path = Path(args.output)
    if not tasks_path.exists():
        print(f"Task file not found: {tasks_path}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    return export_predictions(tasks_path, output_path)


if __name__ == "__main__":
    sys.exit(main())
