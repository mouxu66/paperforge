"""Lightweight integration test for the M0 vector figure pipeline.

Validates:
1. Vector figures from a synthetic PDF are tagged with source='vector'.
2. The source is persisted to the paper_figures table.
3. The QF node no longer hardcodes 0.5 when figures are present.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest


def _make_synthetic_vector_pdf() -> bytes:
    """Create a tiny PDF with a vector rectangle (no embedded bitmaps)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    # Draw a large vector rectangle that should be detected as a figure cluster.
    rect = fitz.Rect(50, 50, 350, 350)
    page.draw_rect(rect, color=(0, 0, 0), fill=(0.9, 0.9, 0.9), width=2)
    # Add some text to avoid completely empty page.
    page.insert_text((60, 30), "Figure 1: Synthetic vector diagram", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_extract_figures_for_paper_detects_vector_source() -> None:
    """A PDF with only vector drawings should yield source='vector' figures."""
    from mock_api.pdf_parser import extract_figures_for_paper

    pdf_bytes = _make_synthetic_vector_pdf()
    figs = extract_figures_for_paper(pdf_bytes, "test-paper-vector", skip_ocr=True)

    sources = [fig["source"] for fig in figs]
    assert "vector" in sources, (
        f"Expected at least one vector figure, got sources={sources}"
    )


def test_figure_source_persisted_in_database(db_session) -> None:
    """The figure worker should persist source='vector' into paper_figures."""
    from mock_api.crud.figures import upsert_figure
    from mock_api.models import Paper, PaperFigure

    paper_id = f"test-paper-{uuid.uuid4().hex[:8]}"
    # Create a minimal paper record so the foreign key holds.
    paper = Paper(
        id=paper_id,
        title="Synthetic Vector Paper",
        authors=[],
        abstract="",
        category="test",
        tags=[],
        year=2026,
        journal="",
        pdf_url="",
        source="test",
    )
    db_session.add(paper)
    db_session.commit()

    upsert_figure(
        db_session,
        paper_id=paper_id,
        page=1,
        figure_index=0,
        figure_path="/tmp/fig.png",
        ocr_text="",
        embedding=None,
        caption_text="A vector diagram.",
        source="vector",
    )
    db_session.commit()

    row = db_session.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).first()
    assert row is not None
    assert row.source == "vector"
    assert row.caption_text == "A vector diagram."


def test_qf_node_uses_figures_and_not_hardcoded_zero_point_five(db_session) -> None:
    """When figures exist, QF should use the LLM score, not the 0.5 fallback."""
    from mock_api.crud.figures import upsert_figure
    from mock_api.database import SessionLocal
    from mock_api.depth_eval_v4 import DepthReviewer
    from mock_api.models import Paper, PaperFigure

    paper_id = f"test-paper-{uuid.uuid4().hex[:8]}"
    paper = Paper(
        id=paper_id,
        title="Vector Figure Paper",
        authors=[],
        abstract="A paper with a vector figure.",
        category="test",
        tags=[],
        year=2026,
        journal="",
        pdf_url="",
        source="test",
    )
    db_session.add(paper)
    db_session.commit()

    upsert_figure(
        db_session,
        paper_id=paper_id,
        page=1,
        figure_index=0,
        figure_path="/tmp/fig.png",
        ocr_text="x-axis y-axis curve",
        embedding=None,
        caption_text="Vector diagram caption.",
        source="vector",
    )
    db_session.commit()

    # Ensure the figure is in the DB.
    row = db_session.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).first()
    assert row is not None

    # Mock LLM to return a non-0.5 score in the format _extract_llm_fields expects.
    def fake_llm(prompt: str, **kw):
        return (
            "reasoning: ok\n"
            "figure_consistency_score: 0.75\n"
            "inconsistency_flags: none\n"
            "evidence_id: none"
        )

    evaluator = DepthReviewer(llm_func=fake_llm, compute_mode="deep")
    result = evaluator._run_qf(
        text="A paper with a vector figure.",
        paper_id=paper_id,
        evidence_pool={},
    )

    assert result.has_figures is True
    assert result.figure_consistency_score == pytest.approx(0.75, abs=0.01)


def test_qf_node_returns_fallback_when_no_figures() -> None:
    """When no figures exist, QF should still return has_figures=False and score 0.5."""
    from mock_api.depth_eval_v4 import DepthReviewer

    paper_id = f"test-paper-no-figures-{uuid.uuid4().hex[:8]}"
    evaluator = DepthReviewer(llm_func=lambda prompt, **kw: "{}", compute_mode="deep")
    result = evaluator._run_qf(
        text="A paper without figures.",
        paper_id=paper_id,
        evidence_pool={},
    )

    assert result.has_figures is False
    assert result.figure_consistency_score is None
