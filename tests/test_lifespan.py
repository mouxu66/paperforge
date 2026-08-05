"""Lifespan 启动集成测试 —— 验证 create_app() 生命周期正确触发。

覆盖：
- init_db / 表创建
- /api/health/ready 返回 200
- 调度器已启动
- 幽灵任务清理不抛异常
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.scheduler import is_scheduler_running
from mock_api.settings import reset_settings


@pytest.mark.critical
class TestLifespanStartup:
    """使用 `with TestClient(create_app()) as c:` 触发 lifespan。"""

    def test_health_ready_returns_200_with_lifespan(self, monkeypatch):
        """触发 lifespan 后，/api/health/ready 应返回 200。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
        reset_settings()

        with TestClient(create_app()) as client:
            resp = client.get("/api/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "papers" in data

    def test_health_live_always_public(self, monkeypatch):
        """健康检查 live 端点始终公开，不受鉴权影响。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        reset_settings()

        with TestClient(create_app()) as client:
            resp = client.get("/api/health/live")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    def test_scheduler_started_after_lifespan(self, monkeypatch):
        """lifespan 启动后调度器应处于运行状态。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
        reset_settings()

        with TestClient(create_app()) as _client:
            assert is_scheduler_running(), "调度器应在 lifespan 启动后运行"

    def test_sse_main_loop_is_bound_after_lifespan(self, monkeypatch):
        """lifespan 启动后应绑定 SSE 主事件循环。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
        reset_settings()

        import mock_api.tasks as tasks_module

        # 确保测试前未绑定，并在测试后恢复
        original_loop = tasks_module._main_loop
        tasks_module._main_loop = None
        try:
            with TestClient(create_app()) as _client:
                pass  # lifespan 触发 bind_main_event_loop

            assert tasks_module._main_loop is not None, "SSE 主事件循环应在 lifespan 后绑定"
        finally:
            tasks_module._main_loop = original_loop

    def test_ghost_tasks_are_cleaned_up_on_startup(self, monkeypatch):
        """lifespan 启动时应将过久的 running/pending 任务标记为 failed。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
        reset_settings()

        from datetime import datetime, timedelta

        from mock_api.database import SessionLocal
        from mock_api.models import Task as TaskORM

        task_id = f"ghost-task-{uuid.uuid4().hex[:8]}"
        stale_time = datetime.now() - timedelta(minutes=20)
        db = SessionLocal()
        try:
            task = TaskORM(
                id=task_id,
                type="depth_review",
                status="running",
                progress=50,
                progress_message="running...",
                created_at=stale_time,
                updated_at=stale_time,
            )
            db.add(task)
            db.commit()
        finally:
            db.close()

        try:
            with TestClient(create_app()) as _client:
                pass  # lifespan 触发 _cleanup_ghost_tasks

            db2 = SessionLocal()
            try:
                row = db2.execute(
                    text("SELECT status, error FROM tasks WHERE id = :tid"),
                    {"tid": task_id},
                ).first()
                assert row is not None
                assert row[0] == "failed"
                assert "服务重启导致中断" in (row[1] or "")
            finally:
                db2.close()
        finally:
            # 清理测试数据，避免污染默认库
            db3 = SessionLocal()
            try:
                db3.execute(text("DELETE FROM tasks WHERE id = :tid"), {"tid": task_id})
                db3.commit()
            finally:
                db3.close()
