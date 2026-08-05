"""routers/zotero.py 端点测试 — 32% → ~80% 覆盖提升。

Mock watchdog_zotero / zotero_import / crud 依赖，用 TestClient 测 3 个端点。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.database import get_db
from mock_api.schemas import Paper


# ── fixtures ───────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    """创建 TestClient，注入 mock DB session。"""
    mock_db = Mock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    return TestClient(app)


# ── GET /api/zotero/watch-status ──────────────────────────────

def test_paper_schema_accepts_zotero_source():
    paper = Paper(
        id="zotero-1",
        title="Zotero paper",
        authors=[],
        year=2024,
        abstract="",
        category="interdisciplinary",
        source="zotero",
    )
    assert paper.source == "zotero"


class TestZoteroWatchStatus:
    """获取 Zotero 监控状态。"""

    def test_returns_status_dict(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.get_watchdog_status",
            lambda: {"enabled": False, "watchDir": ""},
        )
        resp = client.get("/api/zotero/watch-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["watchDir"] == ""

    def test_returns_status_when_watching(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.get_watchdog_status",
            lambda: {"enabled": True, "watchDir": "/tmp/zotero", "filesProcessed": 5},
        )
        resp = client.get("/api/zotero/watch-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["watchDir"] == "/tmp/zotero"


# ── POST /api/zotero/watch-config ─────────────────────────────

class TestZoteroWatchConfig:
    """配置 Zotero 目录监控。"""

    def test_start_watchdog_success(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.start_watchdog",
            lambda watch_dir: True,
        )
        monkeypatch.setattr(
            "mock_api.routers.zotero.get_watchdog_status",
            lambda: {"enabled": True, "watchDir": "/tmp/zotero"},
        )
        resp = client.post(
            "/api/zotero/watch-config",
            json={"enabled": True, "watchDir": "/tmp/zotero"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["watchDir"] == "/tmp/zotero"

    def test_start_watchdog_fails_dir_not_found(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.start_watchdog",
            lambda watch_dir: False,
        )
        resp = client.post(
            "/api/zotero/watch-config",
            json={"enabled": True, "watchDir": "/nonexistent"},
        )
        assert resp.status_code == 400
        assert "无法监控目录" in resp.json()["detail"]

    def test_stop_watchdog(self, client, monkeypatch):
        stop_called = []
        monkeypatch.setattr(
            "mock_api.routers.zotero.stop_watchdog",
            lambda: stop_called.append(True),
        )
        monkeypatch.setattr(
            "mock_api.routers.zotero.get_watchdog_status",
            lambda: {"enabled": False, "watchDir": ""},
        )
        resp = client.post(
            "/api/zotero/watch-config",
            json={"enabled": False, "watchDir": ""},
        )
        assert resp.status_code == 200
        assert len(stop_called) == 1


# ── POST /api/zotero/import ───────────────────────────────────

class TestZoteroImport:
    """Zotero 导入端点。"""

    def test_import_success(self, client, monkeypatch):
        items = [
            {
                "title": "Paper One",
                "authors": ["Alice"],
                "year": 2024,
                "abstract": "Test abstract",
                "doi": "10.1234/test.1",
                "url": "https://example.com/1",
            },
            {
                "title": "Paper Two",
                "authors": ["Bob"],
                "year": 2023,
                "abstract": "Another abstract",
                "doi": "10.1234/test.2",
                "url": "https://example.com/2",
            },
        ]
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: items,
        )
        monkeypatch.setattr(
            "mock_api.routers.zotero.crud.import_external_paper",
            lambda db, **kw: None,
        )
        monkeypatch.setattr(
            "mock_api.routers.zotero.crud.normalize_category",
            lambda cat: "zotero",
        )

        resp = client.post(
            "/api/zotero/import",
            json={"userId": "12345", "apiKey": "secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["successCount"] == 2
        assert data["failCount"] == 0
        assert data["totalFetched"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["title"] == "Paper One"
        assert data["results"][0]["success"] is True

    def test_import_empty_list(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: [],
        )
        resp = client.post(
            "/api/zotero/import",
            json={"userId": "12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["successCount"] == 0
        assert data["totalFetched"] == 0

    def test_import_permission_error_returns_403(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: (_ for _ in ()).throw(
                PermissionError("Invalid API key")
            ),
        )
        resp = client.post(
            "/api/zotero/import",
            json={"userId": "12345", "apiKey": "bad-key"},
        )
        assert resp.status_code == 403
        assert "Invalid API key" in resp.json()["detail"]

    def test_import_value_error_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: (_ for _ in ()).throw(
                ValueError("User not found")
            ),
        )
        resp = client.post(
            "/api/zotero/import",
            json={"userId": "99999"},
        )
        assert resp.status_code == 404
        assert "User not found" in resp.json()["detail"]

    def test_import_generic_error_returns_502(self, client, monkeypatch):
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: (_ for _ in ()).throw(
                RuntimeError("Connection refused")
            ),
        )
        resp = client.post(
            "/api/zotero/import",
            json={"userId": "12345"},
        )
        assert resp.status_code == 502
        assert "Zotero API" in resp.json()["detail"]

    def test_import_handles_partial_failures(self, client, monkeypatch):
        items = [
            {"title": "Good", "authors": ["A"], "year": 2024, "abstract": "", "doi": "d1", "url": ""},
            {"title": "Bad", "authors": ["B"], "year": 2024, "abstract": "", "doi": "d2", "url": ""},
        ]
        monkeypatch.setattr(
            "mock_api.routers.zotero.fetch_zotero_items",
            lambda userId, apiKey: items,
        )
        monkeypatch.setattr(
            "mock_api.routers.zotero.crud.normalize_category",
            lambda cat: "zotero",
        )

        call_count = 0

        def failing_import(db, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("DB write failed")

        monkeypatch.setattr(
            "mock_api.routers.zotero.crud.import_external_paper",
            failing_import,
        )

        resp = client.post(
            "/api/zotero/import",
            json={"userId": "12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["successCount"] == 1
        assert data["failCount"] == 1
        assert len(data["results"]) == 2
        assert data["results"][0]["success"] is True
        assert data["results"][1]["success"] is False
