#!/usr/bin/env python3
"""Export figure annotation tasks for the DEPTH QF human-labeling experiment.

This script queries the local PaperForge SQLite database for figures stored in
``paper_figures`` and writes a JSONL file that human annotators can label.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from mock_api.crud.figures import get_figures_by_paper  # noqa: E402
from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import DepthReviewV4, Paper  # noqa: E402

LABEL_CATEGORIES = [
    "consistent",
    "minor_inconsistency",
    "major_inconsistency",
    "cannot_judge",
]

FLAG_OPTIONS = [
    "value_mismatch",
    "axis_label_error",
    "missing_baseline",
    "caption_contradiction",
    "sample_size_omission",
    "significance_exaggeration",
    "wrong_figure_type",
    "visual_distortion",
    "ocr_noise",
    "other",
]


def _extract_caption(page_text: str, figure_index: int) -> tuple[str, list[str]]:
    """Best-effort caption extraction from the full text.

    Returns the most likely caption for this figure and a list of candidate
    captions. Captions are captured across multiple lines until the next
    figure label, a blank line, or a heading-like line is encountered.
    """
    candidates: list[tuple[int, str]] = []
    if not page_text:
        return "", []

    # Match "Figure N: ..." and capture the caption body until the next figure
    # label, a blank line, or the start of a new paragraph/sentence.
    pattern = re.compile(
        r"(?:Figure|Fig)\.?\s*(\d+)\s*[:\s)]+"
        r"(.{10,500}?(?="
        r"(?:Figure|Fig)\.?\s*\d+|"
        r"\n\n|"
        r"\n[A-Z]|"
        r"$"
        r"))",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(page_text):
        number = int(match.group(1))
        caption = " ".join(line.strip() for line in match.group(2).splitlines() if line.strip())
        if caption and len(caption) >= 10:
            candidates.append((number, caption))

    if not candidates:
        return "", []

    # Prefer a caption whose figure number matches the XObject-derived index.
    for number, caption in candidates:
        if number == figure_index + 1:
            return caption, [c for _, c in candidates]

    # Fallback to the first found caption.
    return candidates[0][1], [c for _, c in candidates]


def _find_source_text_spans(full_text: str, figure_index: int) -> list[str]:
    """Return paragraphs in full_text that reference this figure."""
    if not full_text:
        return []
    figure_label = rf"(?:Figure|Fig\.?)\s*{figure_index + 1}"
    paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
    matched = [
        p
        for p in paragraphs
        if re.search(figure_label, p, re.IGNORECASE)
    ]
    # Keep only the first two references to keep prompts concise.
    return matched[:2]


def _analyzed_paper_ids(db) -> set[str]:
    """Return paper_ids whose latest DEPTH review has figure_coverage == 'analyzed'."""
    from sqlalchemy import desc, func

    subq = (
        db.query(
            DepthReviewV4.paper_id,
            func.row_number()
            .over(partition_by=DepthReviewV4.paper_id, order_by=desc(DepthReviewV4.created_at))
            .label("rn"),
        )
        .filter(DepthReviewV4.kind == "paper")
        .subquery()
    )
    latest = (
        db.query(DepthReviewV4)
        .join(subq, DepthReviewV4.paper_id == subq.c.paper_id)
        .filter(subq.c.rn == 1)
        .all()
    )
    return {
        review.paper_id
        for review in latest
        if (review.final_verdict or {}).get("figure_coverage") == "analyzed"
    }


def export_tasks(
    paper_ids: list[str] | None,
    output_path: Path,
    include_context: bool = True,
    only_analyzed: bool = False,
) -> int:
    """Export annotation tasks to ``output_path``."""
    if output_path.exists():
        raise FileExistsError(f"Output file already exists: {output_path}")

    with SessionLocal() as db:
        analyzed_ids: set[str] | None = None
        if only_analyzed:
            analyzed_ids = _analyzed_paper_ids(db)
            if not analyzed_ids:
                print("No papers with figure_coverage='analyzed' found.")
                with output_path.open("w", encoding="utf-8") as f:
                    pass  # empty file
                return 0

        query = db.query(Paper)
        if paper_ids:
            query = query.filter(Paper.id.in_(paper_ids))  # type: ignore[attr-defined]

        papers = query.all()
        if only_analyzed and analyzed_ids is not None:
            papers = [p for p in papers if p.id in analyzed_ids]

        if not papers:
            print("No papers found matching the criteria.")
            return 0
        task_id = 0
        written = 0

        with output_path.open("w", encoding="utf-8") as f:
            for paper in papers:
                figures = get_figures_by_paper(db, paper.id)
                for fig in figures:
                    task_id += 1
                    caption = ""
                    caption_candidates: list[str] = []
                    source_spans: list[str] = []
                    if include_context and paper.full_text:
                        # Page-level text is not stored; approximate with full text.
                        caption, caption_candidates = _extract_caption(
                            paper.full_text, fig.figure_index
                        )
                        source_spans = _find_source_text_spans(
                            paper.full_text, fig.figure_index
                        )

                    task = {
                        "task_id": f"fig_{task_id:05d}",
                        "paper_id": paper.id,
                        "paper_title": paper.title,
                        "page": fig.page,
                        "figure_index": fig.figure_index,
                        "figure_path": fig.figure_path,
                        "figure_type_hint": "unknown",
                        "ocr_text": fig.ocr_text or "",
                        "qwen_summary": fig.qwen_summary or "",
                        "context_paragraphs": source_spans,
                        "caption": caption,
                        "caption_candidates": caption_candidates,
                        "label_options": LABEL_CATEGORIES,
                        "flag_options": FLAG_OPTIONS,
                    }
                    f.write(json.dumps(task, ensure_ascii=False) + "\n")
                    written += 1

    print(f"Exported {written} tasks to {output_path}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export figure annotation tasks for human labeling.",
    )
    parser.add_argument(
        "--paper-ids",
        type=str,
        default=None,
        help="JSON file containing a list of paper IDs to annotate. If omitted, all papers are exported.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="annotations/tasks.jsonl",
        help="Output JSONL path (default: annotations/tasks.jsonl).",
    )
    parser.add_argument(
        "--include-context",
        action="store_true",
        default=True,
        help="Include caption and source text spans from the paper full text.",
    )
    parser.add_argument(
        "--only-analyzed",
        action="store_true",
        default=False,
        help="Only export papers whose latest DEPTH review has figure_coverage='analyzed'.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    init_db()

    paper_ids: list[str] | None = None
    if args.paper_ids:
        with open(args.paper_ids, encoding="utf-8") as f:
            paper_ids = json.load(f)
        if not isinstance(paper_ids, list):
            raise TypeError("--paper-ids file must contain a JSON list")

    return export_tasks(
        paper_ids=paper_ids,
        output_path=output_path,
        include_context=args.include_context,
        only_analyzed=args.only_analyzed,
    )


if __name__ == "__main__":
    sys.exit(main())
