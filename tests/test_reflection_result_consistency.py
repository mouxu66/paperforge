"""Reflection LLM failure semantics across persistence and read models."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from mock_api import depth_tasks
from mock_api.integrity_report import build_integrity_report
from mock_api.models import DepthReviewV4
from mock_api.models import Paper as PaperORM
from mock_api.routers.reflection import reflection_get_result


def _seed_paper(db, paper_id: str = "failed_report") -> None:
    db.add(
        PaperORM(
            id=paper_id,
            title="Failed report",
            category="report",
            source="upload",
            full_text="This is a sufficiently long reflection report.",
        )
    )
    db.commit()


def test_failed_reflection_persists_untrusted_result(db_session):
    _seed_paper(db_session)
    fake_result = MagicMock()
    fake_result.parse_failed = True
    fake_result.llm_empty = 0
    fake_result.model_dump.return_value = {
        "parse_failed": True,
        "scores": {"average": 0.3},
        "verdict": "needs_evidence",
    }

    with patch("mock_api.depth_eval_reflection.ReflectionReviewer") as reviewer_cls:
        reviewer_cls.return_value.review.return_value = fake_result
        with (
            patch("mock_api.database.SessionLocal", return_value=db_session),
            pytest.raises(depth_tasks.DepthReviewInvalidError),
        ):
            depth_tasks.run_depth_reflection_sync("failed_report")

    record = (
        db_session.query(DepthReviewV4)
        .filter(DepthReviewV4.paper_id == "failed_report", DepthReviewV4.kind == "report")
        .one()
    )
    assert record.status == "failed"
    assert record.reflection_result["llm_failed"] is True
    assert record.reflection_result["scores"] == {}
    assert record.reflection_result["verdict"] == "llm_failed"

    report = build_integrity_report(db_session, "failed_report")
    assert report["scoring"]["trusted"] is False
    assert report["scoring"]["average"] is None
    assert report["scoring"]["verdict"] == "llm_failed"


def test_failed_reflection_result_endpoint_hides_old_scores(db_session):
    _seed_paper(db_session, "failed_endpoint")
    record = DepthReviewV4(
        paper_id="failed_endpoint",
        kind="report",
        status="failed",
        reflection_result={"scores": {"average": 0.99}, "verdict": "well_done"},
    )
    db_session.add(record)
    db_session.commit()

    result = asyncio.run(reflection_get_result("failed_endpoint", db_session))
    assert result["result"]["scores"] == {}
    assert result["result"]["verdict"] == "llm_failed"
    assert result["result"]["fidelity"] is None
