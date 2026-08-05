"""routers/chat.py 端点测试。

覆盖问答、生成和语义搜索端点的基本契约。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
    return TestClient(create_app())


class TestAsk:
    """POST /api/ask"""

    def test_ask_requires_question(self, client):
        resp = client.post("/api/ask", json={"question": "", "paperIds": []})
        assert resp.status_code == 400

    def test_ask_success(self, client, monkeypatch, fake_llm):
        from mock_api import crud

        monkeypatch.setattr(
            crud,
            "retrieve_context",
            lambda db, question, paper_ids=None, top_k=3: [],
        )
        resp = client.post("/api/ask", json={"question": "hello", "paperIds": []})
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "references" in data

    def test_ask_llm_unconfigured(self, client, monkeypatch):
        from mock_api import llm

        def raise_no_provider(*args, **kwargs):
            raise RuntimeError("no provider")

        monkeypatch.setattr("mock_api.llm.factory.LLMFactory.get_provider", raise_no_provider)
        monkeypatch.setattr(
            "mock_api.crud.retrieve_context",
            lambda db, question, paper_ids=None, top_k=3: [],
        )
        resp = client.post("/api/ask", json={"question": "hello", "paperIds": []})
        assert resp.status_code in (500, 502)


class TestGenerate:
    """POST /api/generate"""

    def test_generate_requires_topic(self, client):
        resp = client.post("/api/generate", json={"topic": "", "paperIds": []})
        assert resp.status_code == 400

    def test_generate_success(self, client, monkeypatch, fake_llm):
        from mock_api import crud

        monkeypatch.setattr(
            crud,
            "retrieve_context",
            lambda db, topic, paper_ids=None, top_k=5: [],
        )
        resp = client.post("/api/generate", json={"topic": "survey", "paperIds": []})
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data


class TestSemanticSearch:
    """POST /api/search/semantic"""

    def test_semantic_search_empty_query(self, client):
        resp = client.post("/api/search/semantic", json={"question": ""})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_semantic_search_with_results(self, client, monkeypatch, fake_llm):
        from mock_api import crud

        monkeypatch.setattr(
            crud,
            "hybrid_search_papers",
            lambda db, query, top_k=10, paper_ids=None: [],
        )
        resp = client.post("/api/search/semantic", json={"question": "test"})
        assert resp.status_code == 200
