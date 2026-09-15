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


def _make_text_dense_vector_pdf() -> bytes:
    """构造一个「摘要页 + 一张真实矢量图」的 PDF。

    大背景矢量矩形内含密集正文（模拟摘要/正文被 cluster_drawings 聚成簇），
    右下角另有一张真实矢量图（无文字）。
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    # 摘要/正文背景矩形：占大面积的矢量簇，内含密集正文
    page.draw_rect(
        fitz.Rect(50, 50, 450, 600),
        color=(0.9, 0.9, 0.9),
        fill=(0.98, 0.98, 0.98),
        width=1,
    )
    abstract = (
        "We propose a novel framework for image classification. Our model uses a "
        "hierarchical feature extractor combined with a lightweight attention module. "
        "We train on a large dataset with many epochs. Extensive experiments show "
        "that our method achieves strong accuracy and outperforms many strong "
        "baselines. Ablation studies demonstrate each component contributes. We "
        "report standard deviation across seeds and confirm stability of the results."
    )
    words = abstract.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > 80:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    y = 70
    for ln in lines:
        page.insert_text((60, y), ln, fontsize=11)
        y += 14
    # 一张真实矢量图（右下角，无文字）
    page.draw_rect(
        fitz.Rect(60, 620, 200, 680),
        color=(0, 0, 0.8),
        fill=(0.9, 0.9, 1.0),
        width=2,
    )
    page.insert_text((60, 690), "Figure 1: real vector chart", fontsize=10)

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_vector_cluster_text_stats_distinguishes_text_from_figure() -> None:
    """单元：文本密集簇应返回高词数/高 ink，无文字簇应返回 (0, 0.0)。"""
    import fitz

    from mock_api.pdf_parser import _vector_cluster_text_stats

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    page.draw_rect(
        fitz.Rect(50, 50, 450, 600),
        color=(0.9, 0.9, 0.9),
        fill=(0.98, 0.98, 0.98),
        width=1,
    )
    line = "one two three four five six seven eight nine ten "
    y = 70
    for _ in range(6):
        page.insert_text((60, y), line, fontsize=11)
        y += 14

    n_words, ink = _vector_cluster_text_stats(page, fitz.Rect(50, 50, 450, 600))
    assert n_words >= 50
    assert ink > 0.0

    n_empty, ink_empty = _vector_cluster_text_stats(page, fitz.Rect(60, 620, 200, 680))
    assert n_empty == 0
    assert ink_empty == 0.0
    doc.close()


def _make_banner_vector_pdf() -> bytes:
    """构造一个「顶部装饰横幅 + 右下角真实矢量图」的 PDF。

    横幅横跨 >80% 页宽、高度 <5% 页高（刊头/logo 形态）；真实图在右下角。
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    # 顶部装饰横幅：横跨整页宽、极矮
    page.draw_rect(
        fitz.Rect(10, 10, 490, 30),
        color=(0, 0, 0),
        fill=(0.9, 0.9, 0.9),
        width=1,
    )
    # 真实矢量图（右下角，无文字）
    page.draw_rect(
        fitz.Rect(300, 600, 480, 690),
        color=(0, 0, 0.8),
        fill=(0.9, 0.9, 1.0),
        width=2,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_is_decorative_banner() -> None:
    """单元：横跨 >80% 页宽且高度 <5% 页高才是装饰横幅。"""
    import fitz

    from mock_api.pdf_parser import _is_decorative_banner

    # 宽且矮 → 横幅
    assert _is_decorative_banner(fitz.Rect(10, 10, 490, 30), 500, 700) is True
    # 宽但不矮 → 真实图表，不是横幅
    assert _is_decorative_banner(fitz.Rect(10, 10, 490, 400), 500, 700) is False
    # 矮但不宽 → 小 logo，不是整页横幅
    assert _is_decorative_banner(fitz.Rect(10, 10, 100, 30), 500, 700) is False
    # 非法页尺寸 → fail-open 不拦截
    assert _is_decorative_banner(fitz.Rect(10, 10, 490, 30), 0, 700) is False


def test_extract_figures_skips_decorative_banner(tmp_path, monkeypatch) -> None:
    """回归：封面页顶部装饰横幅不应被抽成 figure；真实图仍应抽出。"""
    from mock_api import pdf_parser
    from mock_api.pdf_parser import extract_figures_for_paper

    monkeypatch.setattr(pdf_parser, "_get_uploads_dir", lambda: tmp_path)
    pdf_bytes = _make_banner_vector_pdf()
    figs = extract_figures_for_paper(pdf_bytes, "test-banner", skip_ocr=True)

    vector_figs = [f for f in figs if f.get("source") == "vector"]
    # 横幅被过滤，只剩右下角真实矢量图
    assert len(vector_figs) == 1, f"expected only the real figure, got {len(vector_figs)}"
    bbox = vector_figs[0]["bbox"]
    # 命中的是右下角真图（y0=600），而非顶部横幅（y0=10）
    assert bbox[1] >= 600


def test_extract_figures_skips_text_dense_vector_cluster(tmp_path, monkeypatch) -> None:
    """回归：摘要页/矢量文字块不应被抽成 figure；真实矢量图仍应抽出。"""
    from mock_api import pdf_parser
    from mock_api.pdf_parser import extract_figures_for_paper

    monkeypatch.setattr(pdf_parser, "_get_uploads_dir", lambda: tmp_path)
    pdf_bytes = _make_text_dense_vector_pdf()
    figs = extract_figures_for_paper(pdf_bytes, "test-text-cluster", skip_ocr=True)

    vector_figs = [f for f in figs if f.get("source") == "vector"]
    # 只剩真实矢量图；文本密集簇被过滤
    assert len(vector_figs) == 1, f"expected only the real figure, got {len(vector_figs)}"
    bbox = vector_figs[0]["bbox"]
    # 命中的是右下角真实图（y0=620），而非摘要文本块（y0=50）
    assert bbox[1] >= 600
