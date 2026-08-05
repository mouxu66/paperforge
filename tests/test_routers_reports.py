"""routers/reports.py 端点测试。

覆盖报告分析与提交记录列表。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestAnalyzeReport:
    """POST /api/reports/analyze"""

    def test_analyze_requires_docx(self, client):
        resp = client.post(
            "/api/reports/analyze",
            files={"file": ("report.txt", BytesIO(b"text"), "text/plain")},
        )
        assert resp.status_code == 400

    def test_analyze_success(self, client, monkeypatch):
        def fake_analyze(path, db, source_paper_id=None):
            return {
                "paper_title": "Report",
                "full_text": "reflection text",
                "student_id": "123",
                "student_name": "Alice",
            }

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.analyze_reflection_file",
            fake_analyze,
        )

        resp = client.post(
            "/api/reports/analyze",
            files={
                "file": (
                    "report.docx",
                    BytesIO(b"fake docx bytes"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert resp.status_code == 200


class TestReportSubmissions:
    """GET /api/reports/submissions"""

    def test_list_submissions(self, client):
        resp = client.get("/api/reports/submissions")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] == 0
