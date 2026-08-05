"""routers/compute.py 端点测试。

覆盖计算模式列表与切换。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.config import get_compute_mode, set_compute_mode


@pytest.fixture
def client():
    return TestClient(create_app())


class TestComputeModes:
    """GET /api/compute/modes"""

    def test_list_modes(self, client):
        resp = client.get("/api/compute/modes")
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert "modes" in data
        assert len(data["modes"]) > 0


class TestSwitchComputeMode:
    """POST /api/compute/mode"""

    def test_switch_to_valid_mode(self, client):
        original = get_compute_mode()
        try:
            resp = client.post("/api/compute/mode", json={"mode": "speed"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["current"] == "speed"
        finally:
            set_compute_mode(original)

    def test_switch_to_invalid_mode(self, client):
        resp = client.post("/api/compute/mode", json={"mode": "invalid"})
        assert resp.status_code == 400
