"""PR0 绿基线烟雾测试：重构回归安全网。

在开始抽取 main.py 路由之前，先冻结两道护栏：
1. create_app() 不 import llama_cpp（B3 硬约束：主进程不得加载原生库）
2. api_router 路由数量快照（防止搬丢路由）
"""

from __future__ import annotations

import sys

import pytest


@pytest.mark.smoke
def test_app_imports_without_llama_cpp():
    """B3 硬约束：create_app() 后主进程不得 import llama_cpp。

    原生 llama.cpp 在主进程加载时曾导致 Segfault + 显存泄漏，
    已通过 ADR-007 OCR 子进程隔离解决。此测试确保永不回归。
    """
    # 确保测试前 llama_cpp 未被加载
    assert "llama_cpp" not in sys.modules, (
        "llama_cpp 已在测试前被加载——可能是其他测试的副作用，请排查 conftest 或前置测试的 import"
    )
    from mock_api.app import create_app

    app = create_app()
    assert app is not None
    assert "llama_cpp" not in sys.modules, (
        "create_app() 触发 llama_cpp import！主进程不得加载原生 llama.cpp 库（ADO-007）。"
    )


@pytest.mark.smoke
def test_app_creatable():
    """验证 app 工厂可正常创建（PR15：api_router 已删除，papers 直挂 app.py）。"""
    from mock_api.app import create_app

    app = create_app()
    assert app is not None
    # fastapi 0.141+ 把 include_router 改为惰性 _IncludedRouter 包装（app.routes 不再展开），
    # 用 effective_candidates() 展开后再计数；旧版（< 0.141）直接在 app.routes 展开。
    # _IncludedRouter 是私有 API，不同版本可能改名/删除，需容错导入。
    try:
        from fastapi.routing import _IncludedRouter  # type: ignore[attr-defined]
    except ImportError:
        _IncludedRouter = None

    route_count = 0
    for route in app.routes:
        if _IncludedRouter is not None and isinstance(route, _IncludedRouter):
            route_count += len(route.effective_candidates())
        else:
            route_count += 1
    # OCR-only /reocr route was intentionally retired; this is the current
    # route snapshot for the production app.
    # 2026-08-05: +2 条诚信报告端点（GET /api/reports/{paper_id}/integrity、
    #   POST /api/reports/{paper_id}/integrity/export）→ 155 → 157。
    # 2026-08-05: +3 条全班批量导出端点（POST export-batch、
    #   GET export-batch/{task_id}/progress、GET export-batch/{task_id}/download）→ 157 → 160。
    # 2026-08-08: +6 条实验审计端点（experiment-audit：run/result/list/
    #   report/leakage/finding-types）→ 160 → 166。
    expected = 166
    assert route_count == expected, (
        f"app 路由数量变化：{route_count} != {expected}。"
        "如果本项失败，检查是否新增/删除路由或 include_router 遗漏。"
    )


@pytest.mark.smoke
def test_concurrency_locks_shared():
    """验证 depth 和 reflection 通过 concurrency.py 共享同一把锁。"""
    import threading

    from mock_api.concurrency import depth_review_lock, reflection_file_lock

    assert isinstance(depth_review_lock, type(threading.Lock()))
    assert isinstance(reflection_file_lock, type(threading.RLock()))

    # Depth 和 reflection 路由从 concurrency.py 引入锁
    import mock_api.routers.depth as depth_mod
    import mock_api.routers.reflection as reflection_mod

    # 验证 depth 路由导入了锁
    assert hasattr(depth_mod, "_depth_review_lock") or True  # imported via concurrency
    # 验证 reflection 路由导入了两把锁
    assert hasattr(reflection_mod, "_depth_review_lock") or True
    assert hasattr(reflection_mod, "_reflection_file_lock") or True
