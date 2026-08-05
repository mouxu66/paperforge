"""测试 mock_api/scheduler.py — arXiv 定时拉取调度器。

覆盖场景：
- run_fetch_once：空关键词跳过、正常拉取入库、已存在跳过、search_arxiv 失败记录 failed
- start_scheduler：无关键词不启动、幂等（重复调用不重建）、apscheduler 未装降级
- shutdown_scheduler：未启动时安全（None 幂等）、已启动后置 None

外部依赖（search_arxiv、crud、SessionLocal）通过 patch 模拟，避免网络与数据库。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mock_api import scheduler
from mock_api.settings import reset_settings


# ---------------------------------------------------------------------------
# 公共夹具：每个测试后重置全局 _scheduler，避免测试间状态泄漏
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """每个测试前重置 scheduler 全局状态。"""
    scheduler._scheduler = None
    yield
    scheduler._scheduler = None


# ===========================================================================
# run_fetch_once
# ===========================================================================
@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_empty_keywords_returns_zero_summary(mock_session):
    """空关键词 → 直接返回零计数汇总，不创建数据库会话。"""
    mock_db = MagicMock()
    mock_session.return_value = mock_db

    result = scheduler.run_fetch_once(keywords=[])

    assert result["added"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert result["details"] == []
    assert result["keywords"] == []
    # 空关键词在打开数据库会话前即返回，SessionLocal 不应被调用
    mock_session.assert_not_called()


@patch("mock_api.crud.import_external_paper")
@patch("mock_api.crud.get_paper", return_value=None)
@patch("mock_api.arxiv_crawler.search_arxiv")
@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_success_imports_new_papers(
    mock_session, mock_search, mock_get_paper, mock_import
):
    """正常拉取：新论文入库，added 计数正确。"""
    mock_db = MagicMock()
    mock_session.return_value = mock_db
    mock_search.return_value = [
        {
            "id": "2401.00001",
            "title": "Paper A",
            "authors": ["Alice"],
            "year": 2024,
            "abstract": "abs A",
            "pdf_url": "http://arxiv.org/pdf/2401.00001",
            "source": "arxiv",
            "category": "arxiv",
            "tags": ["LLM"],
        }
    ]
    mock_get_paper.return_value = None  # 不存在 → 导入

    result = scheduler.run_fetch_once(keywords=["LLM"])

    assert result["added"] == 1
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert result["details"][0]["keyword"] == "LLM"
    assert result["details"][0]["added"] == 1
    mock_import.assert_called_once()
    mock_db.close.assert_called_once()


@patch("mock_api.crud.import_external_paper")
@patch("mock_api.crud.get_paper")
@patch("mock_api.arxiv_crawler.search_arxiv")
@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_skips_existing_papers(
    mock_session, mock_search, mock_get_paper, mock_import
):
    """已入库论文：get_paper 返回非 None → 跳过，skipped 计数 +1。"""
    mock_session.return_value = MagicMock()
    mock_search.return_value = [{"id": "p1", "title": "T"}]
    mock_get_paper.return_value = MagicMock()  # 已存在

    result = scheduler.run_fetch_once(keywords=["kw"])

    assert result["added"] == 0
    assert result["skipped"] == 1
    assert result["failed"] == 0
    mock_import.assert_not_called()


@patch("mock_api.crud.import_external_paper", side_effect=Exception("DB write error"))
@patch("mock_api.crud.get_paper", return_value=None)
@patch("mock_api.arxiv_crawler.search_arxiv")
@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_import_failure_recorded(
    mock_session, mock_search, mock_get_paper, mock_import
):
    """入库异常：记录到 failed，不中断其他论文处理。"""
    mock_session.return_value = MagicMock()
    mock_search.return_value = [
        {"id": "p1", "title": "T1"},
        {"id": "p2", "title": "T2"},
    ]

    result = scheduler.run_fetch_once(keywords=["kw"])

    assert result["added"] == 0
    assert result["failed"] == 2
    assert result["details"][0]["failed"] == 2


@patch("mock_api.arxiv_crawler.search_arxiv", side_effect=ConnectionError("network"))
@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_search_failure_continues_other_keywords(mock_session, mock_search):
    """search_arxiv 抛异常：该关键词失败，继续处理后续关键词。"""
    mock_session.return_value = MagicMock()

    result = scheduler.run_fetch_once(keywords=["kw1", "kw2"])

    # 两个关键词的 search 均失败 → details 有两条
    assert len(result["details"]) == 2
    # search 失败不产生 added/skipped
    assert result["added"] == 0


@patch("mock_api.database.SessionLocal")
def test_run_fetch_once_includes_timestamp(mock_session):
    """返回结果包含 fetched_at 时间戳。"""
    mock_session.return_value = MagicMock()
    result = scheduler.run_fetch_once(keywords=[])
    assert "fetched_at" in result
    assert len(result["fetched_at"]) > 0


# ===========================================================================
# start_scheduler
# ===========================================================================
def test_start_scheduler_no_keywords_starts_reflection_only(monkeypatch):
    """未配置 arXiv 关键词 → 调度器仍启动（reflection 补识别任务恒注册），
    但只注册 1 个 job（无 arXiv 拉取），且不创建真实后台线程。"""
    fake_bg = MagicMock()
    fake_bg.start = MagicMock()
    monkeypatch.setenv("PAPERFORGE_ARXIV_AUTO_FETCH_KEYWORDS", "")
    reset_settings()
    with patch("apscheduler.schedulers.background.BackgroundScheduler", return_value=fake_bg):
        scheduler.start_scheduler()
    assert scheduler._scheduler is fake_bg
    fake_bg.add_job.assert_called_once()  # 仅 reflection 补识别，无 arxiv 拉取
    fake_bg.start.assert_called_once()


def test_start_scheduler_idempotent_when_already_running(monkeypatch):
    """已启动 → 直接返回，不重复创建。"""
    fake_scheduler = MagicMock()
    scheduler._scheduler = fake_scheduler

    monkeypatch.setenv("PAPERFORGE_ARXIV_AUTO_FETCH_KEYWORDS", "LLM")
    reset_settings()
    scheduler.start_scheduler()

    # 不应再次 add_job / start
    fake_scheduler.add_job.assert_not_called()


def test_start_scheduler_apscheduler_missing_degrades_gracefully(monkeypatch):
    """apscheduler 未安装 → 记录日志，_scheduler 保持 None。"""
    import builtins

    real_import = builtins.__import__

    def _no_apscheduler(name, *args, **kwargs):
        if "apscheduler" in name:
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv("PAPERFORGE_ARXIV_AUTO_FETCH_KEYWORDS", "LLM")
    reset_settings()
    with patch("builtins.__import__", side_effect=_no_apscheduler):
        scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_start_scheduler_with_keywords_creates_scheduler(monkeypatch):
    """配置关键词 + apscheduler 可用 → 创建调度器并 start。"""
    fake_bg = MagicMock()
    fake_bg.start = MagicMock()

    monkeypatch.setenv("PAPERFORGE_ARXIV_AUTO_FETCH_KEYWORDS", "LLM")
    reset_settings()
    with patch("apscheduler.schedulers.background.BackgroundScheduler", return_value=fake_bg):
        scheduler.start_scheduler()

    assert scheduler._scheduler is fake_bg
    # 7/8 新增 reflection 原论文补识别任务（恒注册）+ arXiv 拉取任务（有关键词时注册）= 2 个 job
    assert fake_bg.add_job.call_count == 2
    fake_bg.start.assert_called_once()


# ===========================================================================
# shutdown_scheduler
# ===========================================================================
def test_shutdown_scheduler_when_not_started_is_safe():
    """未启动（None）→ 安全返回，不抛异常。"""
    scheduler._scheduler = None
    scheduler.shutdown_scheduler()  # 不应抛异常
    assert scheduler._scheduler is None


def test_shutdown_scheduler_calls_shutdown_and_resets():
    """已启动 → 调用 shutdown(wait=False)，并重置 _scheduler 为 None。"""
    fake_scheduler = MagicMock()
    scheduler._scheduler = fake_scheduler

    scheduler.shutdown_scheduler()

    fake_scheduler.shutdown.assert_called_once_with(wait=False)
    assert scheduler._scheduler is None


def test_shutdown_scheduler_swallows_shutdown_exception():
    """shutdown 抛异常 → 被捕获，_scheduler 仍被重置为 None。"""
    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = RuntimeError("already stopped")
    scheduler._scheduler = fake_scheduler

    scheduler.shutdown_scheduler()  # 不应抛异常

    assert scheduler._scheduler is None
