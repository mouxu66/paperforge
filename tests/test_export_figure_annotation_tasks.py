"""Tests for figure annotation export scripts."""

from __future__ import annotations

import json

from scripts.export_figure_annotation_tasks import _extract_caption, _find_source_text_spans
from scripts.export_qf_predictions import _latest_review_for_paper


class TestExtractCaption:
    def test_exact_match(self) -> None:
        text = "Some intro.\nFigure 1: Accuracy over epochs.\nMore text."
        caption, candidates = _extract_caption(text, figure_index=0)
        assert caption == "Accuracy over epochs."
        assert candidates == ["Accuracy over epochs."]

    def test_fig_dot(self) -> None:
        text = "See Fig. 2: Baseline vs proposed."
        caption, _ = _extract_caption(text, figure_index=1)
        assert "Baseline vs proposed" in caption

    def test_fallback_when_index_mismatch(self) -> None:
        text = "Figure 3: Third figure.\nFigure 5: Fifth figure."
        # XObject index 0 expects Figure 1, so neither candidate matches; fallback to first.
        caption, candidates = _extract_caption(text, figure_index=0)
        assert caption == "Third figure."
        assert "Fifth figure." in candidates

    def test_no_caption(self) -> None:
        text = "There is no figure caption here."
        caption, candidates = _extract_caption(text, figure_index=0)
        assert caption == ""
        assert candidates == []


class TestFindSourceTextSpans:
    def test_finds_reference(self) -> None:
        text = "As shown in Figure 1, accuracy improves.\nAnother paragraph."
        spans = _find_source_text_spans(text, figure_index=0)
        assert any("Figure 1" in span for span in spans)

    def test_empty_text(self) -> None:
        assert _find_source_text_spans("", figure_index=0) == []


class TestQFExportWithDB:
    def test_latest_review_lookup(self, db_session) -> None:
        from mock_api.models import DepthReviewV4

        review = DepthReviewV4(
            paper_id="paper_001",
            kind="paper",
            final_verdict={
                "figure_consistency_score": 0.3,
                "figure_flags": ["value_mismatch"],
                "figure_coverage": "analyzed",
            },
        )
        db_session.add(review)
        db_session.commit()

        found = _latest_review_for_paper(db_session, "paper_001")
        assert found is not None
        assert found.final_verdict["figure_consistency_score"] == 0.3  # type: ignore[index]


class TestExportTasksE2E:
    def test_export_tasks_main(self, tmp_path, db_session) -> None:
        from mock_api.models import Paper, PaperFigure
        from scripts.export_figure_annotation_tasks import main as export_main

        paper = Paper(
            id="paper_e2e_001",
            title="E2E Paper",
            authors=[],
            abstract="",
            full_text="See Figure 1. Figure 1: accuracy curve.",
        )
        db_session.add(paper)
        db_session.flush()

        fig = PaperFigure(
            paper_id=paper.id,
            page=1,
            figure_index=0,
            figure_path="uploads/figures/paper_e2e_001/p1_i0.png",
            ocr_text="accuracy",
        )
        db_session.add(fig)
        db_session.commit()

        output_path = tmp_path / "tasks.jsonl"
        rc = export_main(["--output", str(output_path), "--include-context"])
        assert rc == 0
        assert output_path.exists()

        tasks = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(t["paper_id"] == "paper_e2e_001" and t["caption"] == "accuracy curve." for t in tasks)

    def test_only_analyzed_excludes_missing(self, tmp_path, db_session) -> None:
        from mock_api.models import DepthReviewV4, Paper, PaperFigure
        from scripts.export_figure_annotation_tasks import main as export_main

        paper = Paper(id="paper_missing", title="Missing", authors=[], abstract="")
        db_session.add(paper)
        db_session.flush()
        fig = PaperFigure(
            paper_id=paper.id,
            page=1,
            figure_index=0,
            figure_path="x.png",
            ocr_text="",
        )
        db_session.add(fig)
        review = DepthReviewV4(
            paper_id=paper.id,
            kind="paper",
            final_verdict={"figure_coverage": "missing"},
        )
        db_session.add(review)
        db_session.commit()

        output_path = tmp_path / "tasks.jsonl"
        rc = export_main(["--output", str(output_path), "--only-analyzed"])
        assert rc == 0
        tasks = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert not any(t["paper_id"] == "paper_missing" for t in tasks)

    def test_export_qf_predictions_main(self, tmp_path, db_session) -> None:
        from mock_api.models import DepthReviewV4, Paper
        from scripts.export_qf_predictions import main as qf_main

        paper = Paper(id="paper_qf", title="QF Paper", authors=[], abstract="")
        db_session.add(paper)
        db_session.flush()
        review = DepthReviewV4(
            paper_id=paper.id,
            kind="paper",
            final_verdict={
                "figure_consistency_score": 0.2,
                "figure_flags": ["value_mismatch"],
                "figure_coverage": "analyzed",
            },
        )
        db_session.add(review)
        db_session.commit()

        tasks_path = tmp_path / "tasks.jsonl"
        tasks_path.write_text(
            json.dumps({"task_id": "fig_00001", "paper_id": "paper_qf"}) + "\n",
            encoding="utf-8",
        )
        output_path = tmp_path / "qf_predictions.jsonl"
        rc = qf_main(["--tasks", str(tasks_path), "--output", str(output_path)])
        assert rc == 0

        predictions = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(predictions) == 1
        assert predictions[0]["figure_consistency_score"] == 0.2
        assert predictions[0]["inconsistency_flags"] == ["value_mismatch"]
