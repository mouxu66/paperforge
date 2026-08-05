"""routers/models.py 端点测试。

覆盖 LLM 模型配置 CRUD 与当前模型查询。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


class TestListModels:
    """GET /api/models"""

    def test_list_models(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestCreateModel:
    """POST /api/models"""

    def test_create_model(self, client):
        payload = {
            "displayName": "Test Model",
            "apiUrl": "http://localhost:8080/v1",
            "apiKey": "sk-test",
            "modelId": "test-model",
            "enabled": True,
        }
        resp = client.post("/api/models", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["displayName"] == "Test Model"
        assert data["modelId"] == "test-model"

    def test_create_model_missing_fields(self, client):
        resp = client.post("/api/models", json={"displayName": "", "apiUrl": "", "modelId": ""})
        assert resp.status_code == 400


class TestCurrentModel:
    """GET /api/model/current"""

    def test_get_current_model(self, client):
        resp = client.get("/api/model/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert "available" in data
