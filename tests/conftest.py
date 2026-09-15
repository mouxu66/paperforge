"""Shared pytest configuration and fixtures for PaperForge tests.

Provides an isolated in-memory SQLite database for HTTP/DB tests so that
state does not leak between tests or depend on the development database file
(``mock_api/paperforge_mock.db``).

The database module is patched in ``pytest_configure`` (which runs before any
test module is imported) so that ``app.py`` and route modules pick up the
in-memory engine when they are imported by test modules.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import pytest
from mock_api import database as db_module
from mock_api import models  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def pytest_configure(config: pytest.Config) -> None:
    """Configure an isolated in-memory database before test modules are imported.

    ``mock_api.database`` creates its ``engine`` and ``SessionLocal`` at module
    import time.  We import it here, replace those module-level objects with
    an in-memory engine (using ``StaticPool`` so all sessions share the same
    SQLite connection), and create all tables.  Because this runs during pytest
    initialization, test modules that later import ``mock_api.app`` will get
    the patched database.
    """
    # Build an in-memory engine that survives across threads/connections.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    # Patch the database module so every consumer (get_db, app.py, lifespan, etc.)
    # uses the isolated in-memory engine.
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal
    db_module.DB_URL = "sqlite:///:memory:"
    db_module.DB_PATH = Path(tempfile.mkdtemp(prefix="paperforge_test_")) / "paperforge_test.db"
    db_module.DATA_DIR = db_module.DB_PATH.parent

    # Ensure schema exists.
    db_module.Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_in_memory_db() -> None:
    """Reset the in-memory database before each test.

    上传接口会提交 daemon worker；在销毁 SQLite schema 前给这些测试任务一个
    短暂的机会完成，避免后台 Session 与 ``drop_all`` 竞争导致偶发 table lock。
    """
    from mock_api.database import Base, engine

    _wait_for_test_workers()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    _wait_for_test_workers()


@pytest.fixture(autouse=True)
def _seed_test_llm_config(request, _reset_in_memory_db):
    """为带 @pytest.mark.real_llm 的集成测试向隔离内存库注入一条指向本地 8080 的启用模型配置。

    本地 llama-server 已在 127.0.0.1:8080 运行（Ornstein-V2 Q4_K_M）。测试用 DB 被 patch 为
    内存 SQLite 且每用例 drop_all 清空（_reset_in_memory_db 已先执行），故在此重新插入配置，
    使 get_provider() 能取到可用 provider 并真实调用本地模型（而非 fake_llm）。

    仅对显式标记 real_llm 的测试生效，避免污染不依赖真实 LLM 的 400+ 用例
    （否则会让原本 fail-open 走规则回退的逻辑改走真模型而变慢/超时）。
    teardown 时重置 factory 单例，防止 8080 provider 泄漏到后续非 real_llm 测试。
    """
    if not request.node.get_closest_marker("real_llm"):
        yield
        return
    from mock_api.database import SessionLocal
    from mock_api.models import LLMConfig
    from mock_api.llm.factory import reset_provider_for_testing

    db = SessionLocal()
    try:
        db.add(
            LLMConfig(
                display_name="Test Ornstein-V2 (local 8080)",
                api_url="http://127.0.0.1:8080/v1",
                api_key="",
                model_id="ornstein-v2-Q4_K_M.gguf",
                enabled=True,
            )
        )
        db.commit()
    finally:
        db.close()
    yield
    reset_provider_for_testing()


def _wait_for_test_workers(timeout: float = 0.5) -> None:
    """等待 PaperForge 测试期间启动的后台 worker 退出；生产不使用此 fixture。"""
    deadline = time.monotonic() + timeout
    current = threading.current_thread()
    while time.monotonic() < deadline:
        active = [
            thread
            for thread in threading.enumerate()
            if thread is not current
            and thread.name.startswith(
                ("task-", "reflection-", "timeout-", "export-", "integrity-batch-")
            )
            and thread.is_alive()
        ]
        if not active:
            return
        time.sleep(0.01)


@pytest.fixture
def client():
    """Create a ``TestClient`` with lifespan triggered.

    The client uses the patched in-memory database and a fresh settings cache.
    """
    from fastapi.testclient import TestClient
    from mock_api.app import create_app
    from mock_api.settings import reset_settings

    reset_settings()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def db_session():
    """Return a fresh SQLAlchemy session bound to the in-memory database."""
    from mock_api.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()




@pytest.fixture
def fake_llm():
    """注入假 LLM Provider，避免触网调用真实 LLM 服务。

    供 routers 单测使用：所有走 get_factory().get_provider() 的代码
    调用 chat 时返回固定响应。
    """
    from mock_api.llm.factory import (
        reset_provider_for_testing,
        set_provider_for_testing,
    )

    class FakeProvider:
        def chat(self, *args, **kwargs):
            from mock_api.llm import ChatResult

            return ChatResult(content="ok", model="fake", provider="fake", usage={})

        def chat_stream(self, *args, **kwargs):
            yield "ok"

    set_provider_for_testing(FakeProvider())
    yield
    reset_provider_for_testing()
