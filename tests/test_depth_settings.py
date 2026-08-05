"""DEPTH A/B 参数在线调节端点测试。

2026-08-03 修复：端点从 /api/admin/depth/settings（admin 前缀，生产模式 403）
迁移到 /api/depth/settings（普通设置，与 compute/model 同级）。原 admin 鉴权
测试同步改为：生产模式仍可用（回归）+ 全局 token 中间件鉴权。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.config import (
    get_depth_delta_default_max,
    get_depth_delta_default_min,
    get_depth_severity_fatal_weight,
)
from mock_api.runtime_settings import reset_runtime_overrides
from mock_api.settings import reset_settings


@pytest.fixture
def client(monkeypatch):
    """启用鉴权 + 远程访问 + 全局 token，创建 TestClient。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
    monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("PAPERFORGE_API_TOKEN", "test-admin-token")
    reset_settings()
    reset_runtime_overrides()
    return TestClient(create_app())


class TestDepthSettings:
    """/api/depth/settings 读写测试。"""

    @pytest.fixture(autouse=True)
    def _reset_overrides(self):
        reset_runtime_overrides()

    def test_get_settings_returns_defaults(self, client):
        resp = client.get(
            "/api/depth/settings",
            headers={"X-PaperForge-Token": "test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "severity_fallback_threshold" in data
        assert "q5c_claim_severity_factor" in data
        assert "delta_default_min" in data
        assert "delta_default_max" in data

    def test_update_settings_changes_runtime_values(self, client):
        payload = {
            "severity_fatal_weight": 2.5,
            "q5c_claim_severity_factor": 0.1,
            "delta_default_min": -0.12,
            "delta_default_max": 0.18,
        }
        resp = client.put(
            "/api/depth/settings",
            json=payload,
            headers={"X-PaperForge-Token": "test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity_fatal_weight"] == 2.5
        assert data["q5c_claim_severity_factor"] == 0.1
        assert data["delta_default_min"] == -0.12
        assert data["delta_default_max"] == 0.18

        # 运行时覆盖应对后续访问生效
        assert get_depth_severity_fatal_weight() == 2.5
        assert get_depth_delta_default_min() == -0.12
        assert get_depth_delta_default_max() == 0.18

    def test_update_delta_bounds_overrides(self, client):
        payload = {
            "delta_bounds_overrides": {
                "A": {"min": -0.15, "max": 0.2},
                "speed": {"min": -0.05, "max": 0.08},
            }
        }
        resp = client.put(
            "/api/depth/settings",
            json=payload,
            headers={"X-PaperForge-Token": "test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["delta_bounds_overrides"]["A"] == {"min": -0.15, "max": 0.2}

    def test_works_in_production_mode(self, client, monkeypatch):
        """回归：生产模式（ENV=production）下设置端点必须可用。

        原实现挂在 /api/admin 前缀 + require_admin_auth，生产模式一律 403，
        导致「设置 → DEPTH 参数调优」在教师版 exe 中不可用。本测试确保修复有效。
        """
        monkeypatch.setenv("ENV", "production")
        reset_settings()
        resp = client.get(
            "/api/depth/settings",
            headers={"X-PaperForge-Token": "test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "severity_fallback_threshold" in data

    def test_requires_token_when_configured(self, client):
        """配置了全局 token 时，缺 token 的请求被中间件拒绝。"""
        resp = client.get("/api/depth/settings")
        assert resp.status_code in (401, 403)
