# -*- coding: utf-8 -*-
"""``GET /api/depth/v4/scores`` 的字段契约（2026-09-16 修复的回归测试）。

背景事故
--------
论文列表卡片原本只拿 ``novelty_score``（Q2 单维度的**创新分**）当主数字显示，
而判决是由 ``calibrated_score``（**综合分**）+ 致命缺陷条数决定的。两者不同源，
于是出现「卡片显示 50 却大修、显示 65 却拒稿」——用户据此认定系统算错分数。
实测那 5 篇：综合分 0.480 大修 / 0.330 拒稿 / 0.380 拒稿 / 0.477 拒稿 / 0.655 小修，
按综合分是单调的，只是卡片显示错了字段。

本文件锁定接口契约：
1. 必须返回 ``calibrated_score``（判决依据，卡片主数字）；
2. 必须返回 ``fatal_count``（解释「分数不低却拒稿」= 一票否决）；
3. ``novelty_score`` 保留但只作参考；
4. 未评审的论文不出现；同一篇取最新一条已完成记录。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mock_api.app import create_app
from mock_api.models import DepthReviewV4
from mock_api.models import Paper as PaperORM

SCORES_URL = "/api/depth/v4/scores"


def _seed_paper(db, pid: str, *, category: str = "engineering") -> None:
    db.add(
        PaperORM(
            id=pid,
            title=f"title-{pid}",
            authors='["A"]',
            abstract="abstract",
            category=category,
            source="arxiv",
            year=2026,
            full_text="正文" * 120,
        )
    )
    db.commit()


def _seed_review(
    db,
    pid: str,
    *,
    verdict: str,
    calibrated: float | None,
    novelty: float | None,
    fatals: int = 0,
    minors: int = 0,
    created_at: datetime | None = None,
    status: str = "completed",
    review_id: str | None = None,
) -> str:
    points: list[dict] = []
    points += [{"point": f"fatal-{i}", "severity": "fatal"} for i in range(fatals)]
    points += [{"point": f"minor-{i}", "severity": "minor"} for i in range(minors)]
    rid = review_id or f"rev-{pid}-{verdict}-{fatals}"
    db.add(
        DepthReviewV4(
            id=rid,
            paper_id=pid,
            status=status,
            version="v4.2",
            kind="paper",
            created_at=created_at or datetime(2026, 8, 21, 14, 0, 0),
            completed_at=created_at or datetime(2026, 8, 21, 14, 5, 0),
            q2_result={"novelty_score": novelty},
            q5a_result={"critique_points": points},
            final_verdict=(
                {"final_verdict": verdict, "calibrated_score": calibrated}
                if verdict is not None
                else None
            ),
        )
    )
    db.commit()
    return rid


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_payload_exposes_calibrated_score_and_fatal_count(db_session, client):
    """核心契约：卡片主数字要的 calibrated_score 与解释拒稿的 fatal_count 都在。"""
    _seed_paper(db_session, "2307.07633")
    _seed_review(
        db_session,
        "2307.07633",
        verdict="major_revision",
        calibrated=0.48,
        novelty=0.5,
        minors=3,
    )

    resp = client.get(SCORES_URL, params={"paper_ids": "2307.07633"})
    assert resp.status_code == 200
    entry = resp.json()["scores"]["2307.07633"]

    assert entry["verdict"] == "major_revision"
    assert entry["calibrated_score"] == pytest.approx(0.48)
    assert entry["novelty_score"] == pytest.approx(0.5)
    assert entry["fatal_count"] == 0, "只有 minor 时 fatal_count 必须为 0"


def test_fatal_count_counts_only_fatal_severity(db_session, client):
    """fatal_count 只数 severity='fatal'（minor 不算，否则会误报一票否决）。"""
    _seed_paper(db_session, "p-reject")
    _seed_review(
        db_session,
        "p-reject",
        verdict="reject",
        calibrated=0.33,
        novelty=0.65,
        fatals=3,
        minors=2,
    )

    entry = client.get(SCORES_URL, params={"paper_ids": "p-reject"}).json()["scores"]["p-reject"]
    assert entry["fatal_count"] == 3
    # 复现用户截图：创新分 65 却拒稿，原因是综合分 0.33 + 3 条致命缺陷
    assert entry["novelty_score"] == pytest.approx(0.65)
    assert entry["calibrated_score"] == pytest.approx(0.33)


def test_same_novelty_can_have_different_verdict(db_session, client):
    """锁定「创新分相同、判决不同」这一真实情形，避免后人再当成 bug。"""
    _seed_paper(db_session, "p-a")
    _seed_paper(db_session, "p-b")
    # 两篇创新分都是 0.50，综合分不同 → 判决不同
    _seed_review(db_session, "p-a", verdict="major_revision", calibrated=0.48, novelty=0.5)
    _seed_review(db_session, "p-b", verdict="reject", calibrated=0.38, novelty=0.5, fatals=2)

    scores = client.get(SCORES_URL, params={"paper_ids": "p-a,p-b"}).json()["scores"]
    assert scores["p-a"]["novelty_score"] == scores["p-b"]["novelty_score"] == pytest.approx(0.5)
    assert scores["p-a"]["verdict"] == "major_revision"
    assert scores["p-b"]["verdict"] == "reject"
    # 判决顺序与综合分顺序一致（单调），矛盾只存在于被显示错的 novelty 上
    assert scores["p-a"]["calibrated_score"] > scores["p-b"]["calibrated_score"]


def test_latest_completed_record_wins(db_session, client):
    """同一篇多次审稿时取最新一条已完成记录（与列表其它地方口径一致）。"""
    _seed_paper(db_session, "p-multi")
    _seed_review(
        db_session,
        "p-multi",
        verdict="major_revision",
        calibrated=0.43,
        novelty=0.55,
        created_at=datetime(2026, 8, 21, 14, 0, 0),
        review_id="old",
    )
    _seed_review(
        db_session,
        "p-multi",
        verdict="reject",
        calibrated=0.38,
        novelty=0.55,
        fatals=2,
        created_at=datetime(2026, 8, 21, 22, 0, 0),
        review_id="new",
    )

    entry = client.get(SCORES_URL, params={"paper_ids": "p-multi"}).json()["scores"]["p-multi"]
    assert entry["verdict"] == "reject"
    assert entry["fatal_count"] == 2


def test_failed_record_is_ignored_and_unreviewed_paper_absent(db_session, client):
    """failed 记录不参与；未评审的论文不出现在返回里。"""
    _seed_paper(db_session, "p-failed")
    _seed_paper(db_session, "p-untouched")
    _seed_review(
        db_session,
        "p-failed",
        verdict=None,
        calibrated=None,
        novelty=None,
        status="failed",
    )

    scores = client.get(SCORES_URL, params={"paper_ids": "p-failed,p-untouched"}).json()["scores"]
    assert scores == {}


def test_empty_ids_returns_empty_scores(client):
    """空 paper_ids 不应报错（列表首屏无数据时会传空串）。"""
    assert client.get(SCORES_URL, params={"paper_ids": ""}).json() == {"scores": {}}


def test_missing_calibrated_score_does_not_crash(db_session, client):
    """历史记录可能没有 calibrated_score：字段返回 None，不能 500。"""
    _seed_paper(db_session, "p-legacy")
    _seed_review(
        db_session,
        "p-legacy",
        verdict="minor_revision",
        calibrated=None,
        novelty=0.62,
    )

    entry = client.get(SCORES_URL, params={"paper_ids": "p-legacy"}).json()["scores"]["p-legacy"]
    assert entry["calibrated_score"] is None
    assert entry["novelty_score"] == pytest.approx(0.62)
    assert entry["fatal_count"] == 0


def test_scope_is_limited_to_requested_ids(db_session, client):
    """只返回请求的 id，且顺序无关（前端按 paperId 查 Map）。"""
    _seed_paper(db_session, "p-in")
    _seed_paper(db_session, "p-out")
    _seed_review(db_session, "p-in", verdict="accept", calibrated=0.81, novelty=0.9)
    _seed_review(db_session, "p-out", verdict="reject", calibrated=0.3, novelty=0.4, fatals=2)

    scores = client.get(SCORES_URL, params={"paper_ids": "p-in"}).json()["scores"]
    assert set(scores) == {"p-in"}


def test_reason_consistency_documented_via_verdict_floor(db_session, client):
    """边界：综合分恰好落在 minor 档下界(0.65) 的论文应判 minor——与判决口径同源。

    仅作数据快照，确保接口透传的是**最终** calibrated_score（不是 raw 分）。
    """
    _seed_paper(db_session, "2006.03647")
    _seed_review(
        db_session,
        "2006.03647",
        verdict="minor_revision",
        calibrated=0.655,
        novelty=0.62,
        minors=3,
    )

    entry = client.get(SCORES_URL, params={"paper_ids": "2006.03647"}).json()["scores"]["2006.03647"]
    assert entry["calibrated_score"] == pytest.approx(0.655)
    assert entry["fatal_count"] == 0
    assert entry["verdict"] == "minor_revision"


def test_two_requests_are_independent(db_session, client):
    """批量查询按逗号切分并去空白，避免前端拼接出 'a, b' 时漏查。"""
    _seed_paper(db_session, "p-x")
    _seed_paper(db_session, "p-y")
    _seed_review(db_session, "p-x", verdict="accept", calibrated=0.8, novelty=0.8)
    _seed_review(db_session, "p-y", verdict="reject", calibrated=0.3, novelty=0.3, fatals=2)

    scores = client.get(SCORES_URL, params={"paper_ids": " p-x , p-y "}).json()["scores"]
    assert set(scores) == {"p-x", "p-y"}
    assert scores["p-y"]["fatal_count"] == 2
