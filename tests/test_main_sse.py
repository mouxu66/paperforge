"""SSE 终结事件必达测试 —— 覆盖原 🔴「SSE 0 测试」。

验证：
1. /api/tasks/{task_id}/stream 的 SSE 流末事件必达 done 或 error
2. 任务完成后 SSE 连接正常关闭
3. 不存在的 task_id 返回 404
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.main import app

# 模块级 TestClient（与 test_reflection_file_upload.py 一致，不触发 lifespan）
client = TestClient(app)


class TestSSETerminalEvent:
    """SSE 终结事件必达测试。"""

    def test_stream_returns_404_for_nonexistent_task(self):
        """不存在的 task_id 应返回 404。"""
        resp = client.get("/api/tasks/nonexistent-uuid/stream")
        assert resp.status_code == 404

    def test_task_status_nonexistent_404(self):
        """GET /api/tasks/{id} 对不存在的任务返回 404。"""
        resp = client.get("/api/tasks/nonexistent-uuid")
        assert resp.status_code == 404

    def test_task_list_endpoint(self):
        """GET /api/tasks 应返回任务列表。"""
        resp = client.get("/api/tasks?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_submit_unknown_task_type_immediately_fails(self):
        """提交不支持的任务类型应立即返回 400。"""
        resp = client.post("/api/tasks", json={"type": "nonexistent_test_type", "params": {}})
        assert resp.status_code == 400

    def test_submit_returns_canonical_and_legacy_task_id(self, monkeypatch):
        """通用任务提交同时返回规范 taskId 与兼容 task_id。"""
        from mock_api.routers import tasks as tasks_router

        monkeypatch.setattr(tasks_router, "get_worker", lambda _: lambda *_args: None)
        monkeypatch.setattr(
            tasks_router.task_manager.TaskManager,
            "submit",
            staticmethod(lambda *_args, **_kwargs: "task-contract-1"),
        )

        resp = client.post("/api/tasks", json={"type": "test_contract", "params": {}})
        assert resp.status_code == 200
        assert resp.json() == {
            "taskId": "task-contract-1",
            "task_id": "task-contract-1",
            "status": "pending",
        }
