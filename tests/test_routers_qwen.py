"""routers/qwen.py 端点测试。

覆盖 Qwen/llama-server 状态查询端点。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app


class TestQwenStatus:
    """GET /api/qwen/status"""

    def test_qwen_status(self, monkeypatch):
        mock_mgr = Mock()
        mock_mgr._managed = True
        mock_mgr.is_ready.return_value = True

        mock_sched = Mock()
        mock_sched.get_status.return_value = {
            "kind": "ready",
            "eta_seconds": 0,
            "state": "idle",
        }

        monkeypatch.setattr(
            "mock_api.llama_server_manager.get_llama_server_manager",
            lambda: mock_mgr,
        )
        monkeypatch.setattr(
            "mock_api.vram_scheduler.get_vram_scheduler",
            lambda: mock_sched,
        )

        from fastapi.testclient import TestClient
        from mock_api.app import create_app

        client = TestClient(create_app())
        resp = client.get("/api/qwen/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["managed"] is True
        assert data["ready"] is True
        assert data["vram_exclusive"] is True
        assert data["event"]["kind"] == "ready"

    def test_qwen_events_can_list_and_clear(self, monkeypatch):
        mock_sched = Mock()
        mock_sched.get_events.return_value = [
            {"id": "vram-1", "kind": "ready", "message": "ready", "ts": 1.0}
        ]

        monkeypatch.setattr(
            "mock_api.vram_scheduler.get_vram_scheduler",
            lambda: mock_sched,
        )

        client = TestClient(create_app())
        resp = client.get("/api/qwen/events?limit=10")
        assert resp.status_code == 200
        assert resp.json() == {"events": mock_sched.get_events.return_value}
        mock_sched.get_events.assert_called_once_with(10)

        resp = client.post("/api/qwen/events/clear")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        mock_sched.clear_events.assert_called_once_with()
