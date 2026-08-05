"""routers/arxiv.py 端点测试。

覆盖 arXiv 检索与导入两个端点的基本契约与异常分支。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.database import get_db


@pytest.fixture
def client(monkeypatch):
    mock_db = Mock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


class TestArxivSearch:
    """POST /api/arxiv/search"""

    def test_search_requires_keyword(self, client, monkeypatch):
        resp = client.post("/api/arxiv/search", json={"keyword": "", "maxResults": 10})
        assert resp.status_code == 400
        assert "检索词" in resp.json()["detail"]

    def test_search_success(self, client, monkeypatch):
        def fake_search(keyword, max_results=20):
            return [
                {
                    "id": "2401.00001",
                    "title": "Test Paper",
                    "authors": ["Alice"],
                    "year": 2024,
                    "abstract": "An abstract",
                }
            ]

        monkeypatch.setattr("mock_api.routers.arxiv.search_arxiv", fake_search)
        resp = client.post("/api/arxiv/search", json={"keyword": "test", "maxResults": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == "2401.00001"

    def test_search_runtime_error_returns_502(self, client, monkeypatch):
        def fake_search(keyword, max_results=20):
            raise RuntimeError("arxiv timeout")

        monkeypatch.setattr("mock_api.routers.arxiv.search_arxiv", fake_search)
        resp = client.post("/api/arxiv/search", json={"keyword": "test"})
        assert resp.status_code == 502


class TestArxivImport:
    """POST /api/arxiv/import"""

    def test_import_requires_papers(self, client):
        resp = client.post("/api/arxiv/import", json={"papers": []})
        assert resp.status_code == 400

    def test_import_success(self, client, monkeypatch):
        imported = []

        def fake_import(db, **kw):
            from unittest.mock import Mock

            paper = Mock()
            paper.id = kw.get("paper_id", "2401.00001")
            paper.title = kw.get("title", "T")
            imported.append(paper.id)
            return paper

        monkeypatch.setattr("mock_api.routers.arxiv.crud.import_external_paper", fake_import)
        resp = client.post(
            "/api/arxiv/import",
            json={
                "papers": [
                    {
                        "id": "2401.00001",
                        "title": "Test Paper",
                        "authors": ["Alice"],
                        "year": 2024,
                        "abstract": "abstract",
                    }
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["successCount"] == 1
        assert data["failCount"] == 0
