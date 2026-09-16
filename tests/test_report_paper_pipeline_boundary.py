# -*- coding: utf-8 -*-
"""报告 / 论文链路边界 + verdict_reason 自洽性（2026-09-16 修复的回归测试）。

背景事故：感悟/读后报告（``papers.category='report'``）被
``POST /api/depth/v4/review-batch`` 自动抓去跑 DEPTH v4.2 **论文**链路。
论文链路按创新性/严谨性评分，Q3 要求「实验验证数据 / 消融实验 / 基线对比」，
Q5a 把这些缺失记作致命缺陷，凑够 FATAL_VETO_MIN 即一票否决 → ``reject``。
结果同一份文档在 ``depth_reviews_v4`` 里同时存在
``kind='report'``（写得好）与 ``kind='paper'``（拒稿）两条互相矛盾的记录。

本文件锁定三件事：
1. 三个 DEPTH 论文链路入口（单篇 / 选中 / 批量）都不放行报告；
2. ``run_depth_review_sync`` 这个最底层咽喉同样拒绝报告（任何调用方都绕不过）；
3. ``verdict_reason`` 必须与**最终** verdict 自洽——不重建就会出现
   「标签=写得好，理由却写着『需进一步精读并展开分析』(needs_depth 的结论)」。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import mock_api.tasks
from mock_api import depth_tasks
from mock_api.app import create_app
from mock_api.depth_eval_reflection import _build_verdict_reason, rebuild_verdict_reason
from mock_api.models import DepthReviewV4
from mock_api.models import Paper as PaperORM

REPORT_ID = "reflection_boundary_report"
PAPER_ID = "boundary_normal_paper"


def _seed(db, pid: str, *, category: str, full_text: str = "正文" * 120) -> None:
    db.add(
        PaperORM(
            id=pid,
            title=f"title-{pid}",
            authors='["A"]',
            abstract="abstract",
            category=category,
            source="upload",
            year=2026,
            full_text=full_text,
        )
    )
    db.commit()


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def stub_submit(monkeypatch):
    """拦下 TaskManager.submit，记录被提交的 paperId，避免真跑 LLM 流水线。"""
    submitted: list[dict] = []

    def _submit(task_type, params=None, worker_fn=None, **kwargs):  # noqa: ANN001
        submitted.append({"task_type": task_type, "params": params or {}})
        return f"task-{len(submitted)}"

    monkeypatch.setattr(mock_api.tasks.TaskManager, "submit", staticmethod(_submit))
    return submitted


def _queued_ids(submitted: list[dict]) -> set[str]:
    """把两种入队形态（单篇 paperId / 批量 paperIds）拍平成 id 集合。"""
    ids: set[str] = set()
    for entry in submitted:
        params = entry["params"]
        single = params.get("paperId")
        if single:
            ids.add(single)
        ids.update(params.get("paperIds") or [])
    return ids


# ===========================================================================
# 1. 服务层咽喉：run_depth_review_sync 拒绝报告
# ===========================================================================


def test_run_depth_review_sync_rejects_report(db_session):
    """报告进论文链路 → 立即 ValueError，且不创建 running 记录（不进 LLM）。"""
    _seed(db_session, REPORT_ID, category="report")

    with pytest.raises(ValueError, match="report"):
        depth_tasks.run_depth_review_sync(REPORT_ID)

    leaked = (
        db_session.query(DepthReviewV4).filter(DepthReviewV4.paper_id == REPORT_ID).count()
    )
    assert leaked == 0, "被拒绝的报告不应留下任何 depth_reviews_v4 记录"


def test_run_depth_review_sync_still_accepts_normal_paper(db_session, monkeypatch):
    """守卫不能误伤正常论文：非报告应继续走到审稿器（用哨兵异常证明已越过守卫）。"""
    _seed(db_session, PAPER_ID, category="nlp")
    reached: list[str] = []

    class _ReachedReviewer(Exception):
        pass

    class _FakeReviewer:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            reached.append("init")

        def review_async_dag(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise _ReachedReviewer("已越过 category 守卫")

        def review(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise _ReachedReviewer("已越过 category 守卫")

    monkeypatch.setattr("mock_api.depth_eval_v4.DepthReviewer", _FakeReviewer)

    with pytest.raises(_ReachedReviewer):
        depth_tasks.run_depth_review_sync(PAPER_ID)

    assert reached == ["init"], "正常论文被守卫拦住了 = 过滤器过严"


# ===========================================================================
# 2. 三个 API 入口的边界
# ===========================================================================


def test_single_review_endpoint_rejects_report(client, db_session, stub_submit):
    """POST /api/depth/v4/review/{id} 对报告返回 400 并指向 reflection 链路。"""
    _seed(db_session, REPORT_ID, category="report")

    resp = client.post(f"/api/depth/v4/review/{REPORT_ID}")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "report" in detail
    assert "reflection" in detail
    assert stub_submit == [], "报告不应被提交任何审稿任务"


def test_review_selected_skips_report_but_submits_paper(client, db_session, stub_submit):
    """选中提交：报告进 skipped 并给出理由，正常论文照常提交。"""
    _seed(db_session, REPORT_ID, category="report")
    _seed(db_session, PAPER_ID, category="nlp")

    resp = client.post(
        "/api/depth/v4/review-selected",
        json={"paper_ids": [REPORT_ID, PAPER_ID]},
    )
    assert resp.status_code == 200
    body = resp.json()

    skipped_ids = {s["paper_id"]: s["reason"] for s in body["skipped"]}
    assert REPORT_ID in skipped_ids
    assert "reflection" in skipped_ids[REPORT_ID]
    assert body["submitted"] == 1
    assert PAPER_ID not in skipped_ids
    assert _queued_ids(stub_submit) == {PAPER_ID}


def test_review_batch_excludes_report(client, db_session, stub_submit):
    """全库批量审稿：报告不进队列，正常论文仍在队列里。"""
    _seed(db_session, REPORT_ID, category="report")
    _seed(db_session, PAPER_ID, category="nlp")

    resp = client.post("/api/depth/v4/review-batch")
    assert resp.status_code == 200

    queued = _queued_ids(stub_submit)
    assert REPORT_ID not in queued, "报告被论文链路批量抓走 = 本次修复的核心回归"
    assert PAPER_ID in queued, "过滤器不能把正常论文一起排除"


def test_review_batch_keeps_null_category_papers(client, db_session, stub_submit):
    """category 为 NULL 的论文仍应参与批量审稿（SQL 的 NULL != 'report' 是假）。"""
    db_session.add(
        PaperORM(
            id="boundary_null_category",
            title="null-category",
            authors='["A"]',
            abstract="abstract",
            category=None,
            source="upload",
            year=2026,
            full_text="正文" * 120,
        )
    )
    db_session.commit()

    resp = client.post("/api/depth/v4/review-batch")
    assert resp.status_code == 200
    queued = _queued_ids(stub_submit)
    assert "boundary_null_category" in queued


# ===========================================================================
# 3. verdict_reason 与最终 verdict 自洽
# ===========================================================================


def test_build_verdict_reason_labels_4_dim_stage():
    """硬校验阶段（只有 4 维）标「4 维平均」，不冒充 6 维。"""
    reason = _build_verdict_reason(
        "needs_depth",
        {
            "understanding_accuracy": 0.80,
            "analysis_depth": 0.55,
            "innovative_insights": 0.62,
            "evidence_support": 0.80,
            "average": 0.69,
        },
        3,
        ["R1.5: 有效证据数 3 条，分数分级 CAP 至 0.8"],
    )
    assert "4 维平均=0.69" in reason
    assert "6 维加权平均" not in reason
    assert "结论: 报告对原文理解不够深" in reason


def test_rebuild_verdict_reason_matches_final_well_done():
    """最终 verdict=well_done 时，理由不得再带 needs_depth 的结论（旧 bug）。"""
    rr = {
        "verdict": "well_done",
        "scores": {
            "understanding_accuracy": 0.9429,
            "analysis_depth": 0.7161,
            "innovative_insights": 0.4739,
            "evidence_support": 0.892,
            "fidelity": 0.896,
            "coverage": 0.625,
            "average": 0.6286,
        },
        "analysis_v2": {"average": 0.6286, "fidelity": 0.896, "coverage": 0.625},
        "effective_evidence_count": 4,
        "hardcoded_overrides": [],
    }

    reason = rebuild_verdict_reason(rr)
    assert "6 维加权平均=0.63" in reason
    assert "结论: 报告证据充实、分析有深度、观点有支撑" in reason
    assert "需进一步精读" not in reason


def test_rebuild_verdict_reason_handles_unmeasurable_dims():
    """fidelity/coverage 为 None（无绑定论文/报告过短）时不得抛 TypeError。"""
    rr = {
        "verdict": "needs_evidence",
        "scores": {
            "understanding_accuracy": 0.5,
            "analysis_depth": 0.4,
            "innovative_insights": 0.3,
            "evidence_support": 0.2,
            "fidelity": None,
            "coverage": None,
            "average": None,
        },
        "analysis_v2": {"average": None},
        "effective_evidence_count": 0,
        "hardcoded_overrides": [],
    }

    reason = rebuild_verdict_reason(rr)
    assert "不可测" in reason
    assert "结论: 报告分数尚可但证据不足" in reason


def test_reflection_success_path_rebuilds_reason(db_session, tmp_path, monkeypatch):
    """端到端：融合层把 verdict 从 needs_depth 改成 well_done 后，理由必须跟着变。"""
    docx = tmp_path / "report.docx"
    docx.write_bytes(b"stub")
    db_session.add(
        PaperORM(
            id="rebuild_report",
            title="Rebuild report",
            authors='["A"]',
            abstract="abstract",
            category="report",
            source="upload",
            year=2026,
            full_text="正文" * 120,
            reflection_docx_path=str(docx),
            source_paper_id="src-0001",
        )
    )
    db_session.commit()

    # 4 维硬校验阶段：verdict=needs_depth，reason 已按当时的值拼好
    fake_result = MagicMock()
    fake_result.parse_failed = False
    fake_result.llm_empty = 0
    # 真实标量（而非 MagicMock）：函数末尾的 logger.info 会按 %.3f 格式化它们
    fake_result.verdict = "needs_depth"
    fake_result.effective_evidence_count = 4
    fake_result.scores = {"average": 0.68}
    fake_result.model_dump.return_value = {
        "scores": {
            "understanding_accuracy": 0.85,
            "analysis_depth": 0.55,
            "innovative_insights": 0.62,
            "evidence_support": 0.70,
            "average": 0.68,
        },
        "verdict": "needs_depth",
        "verdict_reason": (
            "evidence_id 有效锚定数=4; 4 维平均=0.68 | 结论: 报告对原文理解不够深，"
            "需进一步精读并展开分析"
        ),
        "effective_evidence_count": 4,
        "hardcoded_overrides": [],
    }

    # 6 维融合层：verdict 被改判为 well_done
    ana = {
        "scores": {
            "understanding_accuracy": 0.9429,
            "analysis_depth": 0.7161,
            "innovative_insights": 0.4739,
            "evidence_support": 0.892,
            "fidelity": 0.896,
            "coverage": 0.625,
        },
        "average": 0.6286,
        "verdict": "well_done",
        "fidelity": 0.896,
        "fidelity_status": "ok",
        "fidelity_anchors": [],
        "stray_claims": [],
        "bound_paper_id": "src-0001",
    }

    with (
        patch("mock_api.depth_eval_reflection.ReflectionReviewer") as reviewer_cls,
        patch("mock_api.reflection_pipeline.analyze_reflection_file", return_value=ana),
        patch("mock_api.second_opinion.run_second_opinion", return_value={"enabled": False}),
        patch("mock_api.database.SessionLocal", return_value=db_session),
    ):
        reviewer_cls.return_value.review.return_value = fake_result
        depth_tasks.run_depth_reflection_sync("rebuild_report")

    record = (
        db_session.query(DepthReviewV4)
        .filter(DepthReviewV4.paper_id == "rebuild_report", DepthReviewV4.kind == "report")
        .one()
    )
    rr = record.reflection_result
    assert rr["verdict"] == "well_done"

    reason = rr["verdict_reason"]
    assert "6 维加权平均" in reason, "理由必须标出融合后的口径"
    assert "结论: 报告证据充实" in reason, "理由必须跟着最终 verdict 走"
    assert "需进一步精读" not in reason, "不得残留 4 维阶段的 needs_depth 结论"
