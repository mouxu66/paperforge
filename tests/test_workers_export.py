"""workers/export.py 缓存与导出 worker 测试 — 30% → ~80% 覆盖提升。

Mock SessionLocal / TaskManager / crud 依赖，覆盖：
- 缓存命中/未命中/过期
- project_id 缺失 / 项目不存在
- 分块导出进度回调
- 异常回退
"""

from __future__ import annotations

from unittest.mock import Mock

from mock_api.workers.export import (
    CACHE_TTL,
    _get_cached_export,
    _set_cached_export,
    export_worker,
)


# ── helpers ────────────────────────────────────────────────────
def _mock_task_manager():
    """返回 Mock TaskManager 捕获 complete/fail/update_progress 调用。"""
    return {
        "complete": [],
        "fail": [],
        "progress": [],
    }


def _setup_mocks(monkeypatch, task_mock):
    """设置 export worker 所需的所有 mock。"""
    # Mock SessionLocal 返回 mock DB
    mock_db = Mock()
    monkeypatch.setattr(
        "mock_api.database.SessionLocal",
        lambda: mock_db,
    )
    # Mock TaskManager 类方法
    monkeypatch.setattr(
        "mock_api.tasks.TaskManager",
        type(
            "MockTaskManager",
            (),
            {
                "complete": staticmethod(lambda task_id, result: task_mock["complete"].append({"task_id": task_id, "result": result})),
                "fail": staticmethod(lambda task_id, error: task_mock["fail"].append({"task_id": task_id, "error": error})),
                "update_progress": staticmethod(lambda task_id, progress, message: task_mock["progress"].append({"task_id": task_id, "progress": progress, "message": message})),
            },
        ),
    )
    return mock_db


def _flush_export_cache():
    """清空导出缓存（测试隔离）。"""
    from mock_api.workers.export import _cache_lock, _export_cache

    with _cache_lock:
        _export_cache.clear()


# ── cache unit tests ──────────────────────────────────────────

class TestExportCache:
    """_get_cached_export / _set_cached_export 单元测试。"""

    def setup_method(self):
        _flush_export_cache()

    def teardown_method(self):
        _flush_export_cache()

    def test_cache_miss_returns_none(self):
        result = _get_cached_export(1, "2024-01-01T00:00:00")
        assert result is None

    def test_cache_hit_returns_entry(self):
        _set_cached_export(1, "2024-01-01T00:00:00", "content", "file.md", [])
        result = _get_cached_export(1, "2024-01-01T00:00:00")
        assert result is not None
        assert result.content == "content"
        assert result.filename == "file.md"

    def test_cache_invalidated_by_updated_at_change(self):
        _set_cached_export(1, "2024-01-01T00:00:00", "old", "old.md", [])
        result = _get_cached_export(1, "2024-01-02T00:00:00")  # different updated_at
        assert result is None

    def test_cache_invalidated_by_ttl_expiry(self, monkeypatch):
        import time as _time

        _set_cached_export(1, "2024-01-01T00:00:00", "old", "old.md", [])
        # 模拟时间向前推进 CACHE_TTL + 1 秒
        monkeypatch.setattr(
            "mock_api.workers.export.time",
            type("FakeTime", (), {"time": lambda: _time.time() + CACHE_TTL + 1}),
        )
        result = _get_cached_export(1, "2024-01-01T00:00:00")
        assert result is None


# ── export_worker tests ───────────────────────────────────────

class TestExportWorker:
    """export_worker 主函数测试。"""

    def setup_method(self):
        _flush_export_cache()

    def teardown_method(self):
        _flush_export_cache()

    def test_missing_project_id_fails(self, monkeypatch):
        tm = _mock_task_manager()
        _setup_mocks(monkeypatch, tm)
        export_worker("task-1", {})
        assert len(tm["fail"]) == 1
        assert "缺少 projectId" in tm["fail"][0]["error"]

    def test_project_not_found_fails(self, monkeypatch):
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: None,
        )
        _setup_mocks(monkeypatch, tm)
        export_worker("task-2", {"projectId": 99})
        assert len(tm["fail"]) == 1
        assert "项目不存在" in tm["fail"][0]["error"]

    def test_cache_hit_returns_cached_result(self, monkeypatch):
        """缓存命中 → 直接返回，不调用 crud.export_project_markdown。"""
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: "2024-06-01T00:00:00",
        )
        _setup_mocks(monkeypatch, tm)
        # 预填缓存
        _set_cached_export(1, "2024-06-01T00:00:00", "cached content", "cached.md", [])

        export_worker("task-3", {"projectId": 1})
        assert len(tm["complete"]) == 1
        assert tm["complete"][0]["result"]["content"] == "cached content"
        assert tm["complete"][0]["result"]["filename"] == "cached.md"
        assert len(tm["fail"]) == 0

    def test_export_happy_path(self, monkeypatch):
        """导出成功 → complete + 写入缓存。"""
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: "2024-06-01T00:00:00",
        )
        monkeypatch.setattr(
            "mock_api.crud.export_project_markdown",
            lambda db, project_id, progress_cb=None: (
                "exported markdown",
                "report.md",
                [
                    Mock(id="ref1", title="Ref One", authors=["A"], year=2024),
                    Mock(id="ref2", title="Ref Two", authors=["B"], year=2023),
                ],
            ),
        )
        _setup_mocks(monkeypatch, tm)

        export_worker("task-4", {"projectId": 1})

        assert len(tm["complete"]) == 1
        result = tm["complete"][0]["result"]
        assert result["content"] == "exported markdown"
        assert result["filename"] == "report.md"
        assert len(result["references"]) == 2
        assert result["references"][0]["id"] == "ref1"
        assert len(tm["fail"]) == 0

        # 验证缓存已被写入
        cached = _get_cached_export(1, "2024-06-01T00:00:00")
        assert cached is not None
        assert cached.content == "exported markdown"

    def test_export_invokes_progress_callback(self, monkeypatch):
        """分块导出时 progress_cb 更新 TaskManager 进度。"""
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: "2024-06-01T00:00:00",
        )
        # 捕获 progress_cb
        captured_cb = []

        def fake_export(db, project_id, progress_cb=None):
            if progress_cb:
                captured_cb.append(progress_cb)
                progress_cb(1, 3)
                progress_cb(2, 3)
                progress_cb(3, 3)
            return ("content", "file.md", [])

        monkeypatch.setattr(
            "mock_api.crud.export_project_markdown",
            fake_export,
        )
        _setup_mocks(monkeypatch, tm)

        export_worker("task-5", {"projectId": 1})

        assert len(captured_cb) == 1  # progress_cb 被传入
        # 进度上报 3 次 + 初始 "开始导出" = 4 次
        assert len(tm["progress"]) >= 3
        assert len(tm["complete"]) == 1

    def test_export_returns_none_fails(self, monkeypatch):
        """crud.export_project_markdown 返回 None → fail。"""
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: "2024-06-01T00:00:00",
        )
        monkeypatch.setattr(
            "mock_api.crud.export_project_markdown",
            lambda db, project_id, progress_cb=None: None,
        )
        _setup_mocks(monkeypatch, tm)

        export_worker("task-6", {"projectId": 1})
        assert len(tm["fail"]) == 1

    def test_export_exception_caught_and_reported(self, monkeypatch):
        """导出中抛异常 → fail，不崩溃。"""
        tm = _mock_task_manager()
        monkeypatch.setattr(
            "mock_api.crud.get_project_updated_at",
            lambda db, pid: "2024-06-01T00:00:00",
        )
        monkeypatch.setattr(
            "mock_api.crud.export_project_markdown",
            lambda db, project_id, progress_cb=None: (_ for _ in ()).throw(
                RuntimeError("Out of memory")
            ),
        )
        _setup_mocks(monkeypatch, tm)

        export_worker("task-7", {"projectId": 1})
        assert len(tm["fail"]) == 1
        assert "导出失败" in tm["fail"][0]["error"]
        assert "Out of memory" in tm["fail"][0]["error"]
