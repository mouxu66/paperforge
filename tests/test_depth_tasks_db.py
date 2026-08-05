"""测试 mock_api/depth_tasks.py 对数据库的真实写入路径。

覆盖：
- run_depth_review_sync：创建 running 记录 → 调用 DepthReviewer → 写入各节点结果 → completed
- run_depth_reflection_sync：创建 report 记录 → 调用 ReflectionReviewer → 写入 reflection_result → completed
- 无效结果闸口：证据池为空 + 评分全默认 → failed / DepthReviewInvalidError
- 降级开关：PAPERFORGE_DISABLE_INVALID_GATE=1 时无效结果返回 record_id
- 异常路径：论文不存在、无全文、DB 记录状态回写 failed

所有 LLM 调用均 mock DepthReviewer / ReflectionReviewer，不访问真实模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from mock_api import depth_tasks
from mock_api.models import DepthReviewV4, Paper


# ---------------------------------------------------------------------------
# Mock result helpers
# ---------------------------------------------------------------------------
@dataclass
class _FakeV4Result:
    paper_id: str = "p1"
    has_substance: bool = True
    expectation: float = 0.5
    q0_reasoning: str = "q0"
    q0_evidence: list = field(default_factory=list)
    paper_type: str = "B"
    secondary_type: str = "none"
    confidence: float = 0.6
    q1_reasoning: str = "q1"
    novelty_score: float = 0.7
    hotspot_alignment_score: float = 0.6
    core_contribution: str = "cc"
    q2_reasoning: str = "q2"
    evidence_ids: dict = field(default_factory=dict)
    evidence_checks: dict = field(default_factory=dict)
    rigor_score: float = 0.7
    missing_items: list = field(default_factory=list)
    q3_reasoning: str = "q3"
    influence_score: float = 0.6
    reproducibility_score: float = 0.5
    q4_reasoning: str = "q4"
    critique_points: list = field(default_factory=list)
    defense_points: list = field(default_factory=list)
    chair_reasoning: str = "chair"
    calibrated_score: float = 0.65
    delta: float = 0.0
    delta_missing: list = field(default_factory=list)
    llm_verdict: str = "minor_revision"
    final_verdict: str = "minor_revision"
    override_reason: str = ""
    base_score: float = 0.6
    weights: dict = field(default_factory=dict)
    evidence_pool: list = field(default_factory=list)
    node_score_stds: dict = field(default_factory=dict)

    def model_dump(self):
        return {
            "paper_id": self.paper_id,
            "has_substance": self.has_substance,
            "expectation": self.expectation,
            "q0_reasoning": self.q0_reasoning,
            "q0_evidence": self.q0_evidence,
            "paper_type": self.paper_type,
            "secondary_type": self.secondary_type,
            "confidence": self.confidence,
            "q1_reasoning": self.q1_reasoning,
            "novelty_score": self.novelty_score,
            "hotspot_alignment_score": self.hotspot_alignment_score,
            "core_contribution": self.core_contribution,
            "q2_reasoning": self.q2_reasoning,
            "evidence_ids": self.evidence_ids,
            "evidence_checks": self.evidence_checks,
            "rigor_score": self.rigor_score,
            "missing_items": self.missing_items,
            "q3_reasoning": self.q3_reasoning,
            "influence_score": self.influence_score,
            "reproducibility_score": self.reproducibility_score,
            "q4_reasoning": self.q4_reasoning,
            "critique_points": self.critique_points,
            "defense_points": self.defense_points,
            "chair_reasoning": self.chair_reasoning,
            "calibrated_score": self.calibrated_score,
            "delta": self.delta,
            "delta_missing": self.delta_missing,
            "llm_verdict": self.llm_verdict,
            "final_verdict": self.final_verdict,
            "override_reason": self.override_reason,
            "base_score": self.base_score,
            "weights": self.weights,
            "evidence_pool": self.evidence_pool,
            "node_score_stds": self.node_score_stds,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_paper(db, paper_id: str, full_text: str = "full text", title: str = "Title"):
    paper = Paper(
        id=paper_id,
        title=title,
        abstract="abstract",
        full_text=full_text,
        authors="[]",
        category="arxiv",
        source="arxiv",
        year=2020,
    )
    db.add(paper)
    db.commit()
    return paper


# ---------------------------------------------------------------------------
# run_depth_review_sync
# ---------------------------------------------------------------------------
def test_run_depth_review_sync_writes_db(db_session):
    """正常路径：创建 running 记录，审稿完成后写入 completed 及节点结果。"""
    _seed_paper(db_session, "p1", full_text="some full text")

    fake_result = _FakeV4Result(
        paper_id="p1",
        evidence_pool=[{"id": "E1", "text": "evidence"}],
        critique_points=[{"point": "p", "severity": "minor"}],
    )

    with patch("mock_api.depth_eval_v4.DepthReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()
        # review_async_dag 是 async 方法，asyncio.run 需要 coroutine
        async def _async_dag(*args, **kwargs):
            return fake_result
        mock_reviewer.review_async_dag = _async_dag
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with patch("mock_api.database.SessionLocal", return_value=db_session):
            record_id = depth_tasks.run_depth_review_sync("p1")

    assert record_id is not None
    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.id == record_id).first()
    assert record is not None
    assert record.status == "completed"
    assert record.version == "v4.2"
    assert record.q2_result["novelty_score"] == 0.7
    assert record.final_verdict["final_verdict"] == "minor_revision"


def test_run_depth_review_sync_falls_back_to_serial(db_session):
    """DAG 失败时回退串行 review()。"""
    _seed_paper(db_session, "p1", full_text="some full text")
    fake_result = _FakeV4Result(paper_id="p1")

    with patch("mock_api.depth_eval_v4.DepthReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()

        async def _async_dag(*args, **kwargs):
            raise RuntimeError("DAG failed")

        mock_reviewer.review_async_dag = _async_dag
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with patch("mock_api.database.SessionLocal", return_value=db_session):
            record_id = depth_tasks.run_depth_review_sync("p1")

    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.id == record_id).first()
    assert record.status == "completed"
    assert mock_reviewer.review.called


def test_run_depth_review_sync_invalid_gate_raises(db_session, monkeypatch):
    """无效结果（空证据池 + 全默认分）且非 fast 模式 → DepthReviewInvalidError。"""
    _seed_paper(db_session, "p1", full_text="some full text")
    monkeypatch.setenv("PAPERFORGE_DISABLE_INVALID_GATE", "0")

    fake_result = _FakeV4Result(
        paper_id="p1",
        evidence_pool=[],
        critique_points=[],
        novelty_score=0.5,
        rigor_score=0.5,
        influence_score=0.5,
        reproducibility_score=0.5,
        calibrated_score=0.5,
    )

    with patch("mock_api.depth_eval_v4.DepthReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()

        async def _async_dag(*args, **kwargs):
            return fake_result

        mock_reviewer.review_async_dag = _async_dag
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with (
            patch("mock_api.database.SessionLocal", return_value=db_session),
            pytest.raises(depth_tasks.DepthReviewInvalidError),
        ):
            depth_tasks.run_depth_review_sync("p1")

    # 记录应被标为 failed
    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.paper_id == "p1").first()
    assert record.status == "failed"


def test_run_depth_review_sync_invalid_gate_disabled_returns_id(db_session):
    """降级开关启用时无效结果返回 record_id。"""
    _seed_paper(db_session, "p1", full_text="some full text")

    fake_result = _FakeV4Result(
        paper_id="p1",
        evidence_pool=[],
        critique_points=[],
        novelty_score=0.5,
        rigor_score=0.5,
        influence_score=0.5,
        reproducibility_score=0.5,
        calibrated_score=0.5,
    )

    with patch("mock_api.depth_eval_v4.DepthReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()
        # review_async_dag 是 async 方法，asyncio.run 需要 coroutine
        async def _async_dag(*args, **kwargs):
            return fake_result
        mock_reviewer.review_async_dag = _async_dag
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with (
            patch("mock_api.depth_tasks._is_invalid_gate_disabled", return_value=True),
            patch("mock_api.database.SessionLocal", return_value=db_session),
        ):
            record_id = depth_tasks.run_depth_review_sync("p1")

    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.id == record_id).first()
    assert record.status == "failed"


def test_run_depth_review_sync_missing_paper(db_session):
    """论文不存在 → ValueError。"""
    with (
        patch("mock_api.database.SessionLocal", return_value=db_session),
        pytest.raises(ValueError, match="不存在"),
    ):
        depth_tasks.run_depth_review_sync("no-such-paper")


def test_run_depth_review_sync_empty_full_text(db_session):
    """论文无全文 → ValueError。"""
    _seed_paper(db_session, "p-empty", full_text="")
    with (
        patch("mock_api.database.SessionLocal", return_value=db_session),
        pytest.raises(ValueError, match="full_text"),
    ):
        depth_tasks.run_depth_review_sync("p-empty")


# ---------------------------------------------------------------------------
# run_depth_reflection_sync
# ---------------------------------------------------------------------------
def test_run_depth_reflection_sync_writes_db(db_session):
    """reflection 评审正常写入 reflection_result 并 completed。"""
    _seed_paper(db_session, "r1", full_text="this is a reflection report content")

    fake_result = MagicMock()
    fake_result.parse_failed = False
    fake_result.model_dump.return_value = {
        "scores": {"understanding_accuracy": 0.8, "average": 0.75},
        "verdict": "well_done",
        "effective_evidence_count": 3,
    }
    fake_result.verdict = "well_done"
    fake_result.scores = {"average": 0.75}
    fake_result.effective_evidence_count = 3

    with patch("mock_api.depth_eval_reflection.ReflectionReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with patch("mock_api.database.SessionLocal", return_value=db_session):
            record_id = depth_tasks.run_depth_reflection_sync("r1")

    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.id == record_id).first()
    assert record is not None
    assert record.kind == "report"
    assert record.version == "reflection.v1"
    assert record.status == "completed"
    assert record.reflection_result["scores"]["average"] == 0.75


def test_run_depth_reflection_sync_parse_failed(db_session):
    """reflection parse_failed → 记录 failed 并抛 DepthReviewInvalidError。"""
    _seed_paper(db_session, "r2", full_text="this is a reflection report content")

    fake_result = MagicMock()
    fake_result.parse_failed = True
    fake_result.model_dump.return_value = {"parse_failed": True}

    with patch("mock_api.depth_eval_reflection.ReflectionReviewer") as mock_reviewer_cls:
        mock_reviewer = MagicMock()
        mock_reviewer.review.return_value = fake_result
        mock_reviewer_cls.return_value = mock_reviewer

        with (
            patch("mock_api.database.SessionLocal", return_value=db_session),
            pytest.raises(depth_tasks.DepthReviewInvalidError),
        ):
            depth_tasks.run_depth_reflection_sync("r2")

    record = db_session.query(DepthReviewV4).filter(DepthReviewV4.paper_id == "r2").first()
    assert record.status == "failed"


def test_run_depth_reflection_sync_missing_paper(db_session):
    """报告不存在 → ValueError。"""
    with (
        patch("mock_api.database.SessionLocal", return_value=db_session),
        pytest.raises(ValueError, match="不存在"),
    ):
        depth_tasks.run_depth_reflection_sync("no-such-report")


# ---------------------------------------------------------------------------
# submit_depth_job / get_job_status
# ---------------------------------------------------------------------------
def test_submit_depth_job_and_get_status():
    """submit_depth_job 通过 TaskManager 提交并查询状态。"""
    from mock_api import tasks as task_module

    fake_worker = MagicMock()
    fake_get_return = {
        "id": "task-id-123",
        "status": "completed",
        "progress": 100,
        "result": {"total": 2, "results": []},
    }

    with (
        patch("mock_api.workers.get_worker", return_value=fake_worker),
        patch.object(task_module.TaskManager, "submit", return_value="task-id-123"),
        patch.object(task_module.TaskManager, "get", return_value=fake_get_return),
    ):
        task_id = depth_tasks.submit_depth_job(["p1", "p2"])
        assert task_id == "task-id-123"
        fake_worker.assert_not_called()  # 只提交不执行

        status = depth_tasks.get_job_status("task-id-123")
        assert status["status"] == "completed"
        assert status["progress"]["total"] == 2
