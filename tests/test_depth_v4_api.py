"""DEPTH v4.2 API 契约测试。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.database import get_db
from mock_api.settings import reset_settings


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """关闭鉴权中间件，确保 API 契约测试隔离于全局 auth 配置。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
    reset_settings()


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, record):
        self._record = record

    def query(self, model):
        return _FakeQuery(self._record)


def _make_record(*, calibrated_score: float, status: str = "completed"):
    now = datetime.now()
    return SimpleNamespace(
        id="review-1",
        paper_id="paper-1",
        status=status,
        q0_result={"has_substance": True, "expectation": 0.7},
        q1_result={"type": "B", "secondary_type": "none", "confidence": 0.85},
        evidence_pool=[{"id": "E1", "content": "x", "section": "Intro", "keywords": ["x"]}],
        q2_result={"novelty_score": 0.8, "hotspot_alignment_score": 0.75, "core_contribution": "x"},
        q3_result={"rigor_score": 0.7, "missing_items": []},
        q4_result={"influence_score": 0.75, "reproducibility_score": 0.72},
        q5a_result={"critique_points": []},
        q5b_result={"defense_points": []},
        q5c_result={"reasoning": "ok", "calibrated_score": calibrated_score, "delta": 0.0, "delta_missing": False, "llm_verdict": "accept"},
        final_verdict={
            "final_verdict": "accept",
            "calibrated_score": calibrated_score,
            "override_reason": "",
            "llm_verdict": "accept",
            "base_score": 0.8,
            "weights": {"beta": 0.25, "gamma": 0.45, "delta": 0.15, "epsilon": 0.15},
            "evidence_checks": {"Q2": True},
            "node_score_stds": {"Q2:novelty_score": 0.02, "Q5c": 0.01},
        },
        error_message=None,
        created_at=now,
        completed_at=now,
    )


class TestDepthV4ResultEndpoint:
    """GET /api/depth/v4/result/{paper_id} 口径测试。"""

    @pytest.fixture(autouse=True)
    def _app_overrides(self, client):
        self.client = client
        self.app = client.app
        yield
        self.app.dependency_overrides.clear()
    """GET /api/depth/v4/result/{paper_id} 口径测试。"""

    def test_v4_final_score_conversion(self):
        """适配层应将 calibrated_score(0~1) 换算为 final_score(0~100)。"""
        record = _make_record(calibrated_score=0.853)
        self.app.dependency_overrides[get_db] = lambda: _FakeDB(record)
        resp = self.client.get("/api/depth/v4/result/paper-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["final_score"] == 85.3
        assert data["node_score_stds"]["Q5c"] == 0.01

    def test_v4_result_not_found(self):
        """无审稿记录时返回 404。"""
        self.app.dependency_overrides[get_db] = lambda: _FakeDB(None)
        resp = self.client.get("/api/depth/v4/result/no-record")
        assert resp.status_code == 404
