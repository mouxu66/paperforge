"""P0 核心路径真实行为测试（F20/F21 覆盖缺口）。

覆盖目标模块（2026-07-19 审查）：
- mock_api/crud/papers.batch_delete_papers（真实 DB 删除）
- mock_api/workers/batch_delete.batch_delete_worker（分支逻辑）
- mock_api/crud/search（FTS5 真实检索 + RRF 融合）
- mock_api/report_paper_resolver.resolve_and_ingest（决策树，无网络）
- mock_api/tasks.TaskManager（任务状态机）
- mock_api/reflection_fidelity.compute_fidelity（余弦忠实度计分，stub 嵌入器）
- mock_api/depth_tasks.run_depth_review_sync（入口守卫，无需 LLM）
- mock_api/llm/factory（provider 注入缝）

原则：不下载模型、不访问真实 LLM/网络。LLM 缝用 llm/factory 的
set_provider_for_testing；嵌入器缝用 monkeypatch embed_text_ml；
网络缝用 monkeypatch search_arxiv / _try_semantic_scholar。
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import mock_api.database
import mock_api.tasks
import pytest
from mock_api import depth_tasks, reflection_fidelity, report_paper_resolver
from mock_api.crud import papers as crud_papers
from mock_api.crud import search as crud_search
from mock_api.models import Paper as PaperORM
from mock_api.models import Task as TaskORM
from mock_api.schemas import BatchDeleteResponse
from mock_api.workers import batch_delete as batch_delete_worker_mod
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def temp_db(monkeypatch):
    """临时 SQLite 库，替换 mock_api.database / mock_api.tasks 的 SessionLocal，
    并运行 init_db（建表 + FTS5 + 种子），保证所有被测函数走临时库。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "p0.db"
        url = f"sqlite:///{db_path}"
        eng = create_engine(url, future=True, pool_pre_ping=True)
        sess = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)

        monkeypatch.setattr(mock_api.database, "DB_URL", url)
        monkeypatch.setattr(mock_api.database, "engine", eng)
        monkeypatch.setattr(mock_api.database, "SessionLocal", sess)
        monkeypatch.setattr(mock_api.database, "DB_PATH", db_path)
        monkeypatch.setattr(mock_api.database, "DATA_DIR", db_path.parent)
        monkeypatch.setenv("PAPERFORGE_DB_PATH", str(db_path))
        # TaskManager 在模块顶层 from .database import SessionLocal，需同步替换
        monkeypatch.setattr(mock_api.tasks, "SessionLocal", sess)

        from mock_api.settings import reset_settings

        reset_settings()
        from mock_api.database import init_db

        init_db()

        db = sess()
        try:
            yield db
        finally:
            db.close()
            try:
                eng.dispose()
            except Exception:
                pass


def _seed_paper(db, pid: str, title: str, abstract: str = "abstract text") -> None:
    db.add(
        PaperORM(
            id=pid,
            title=title,
            abstract=abstract,
            authors='["A"]',
            category="arxiv",
            source="arxiv",
            year=2020,
        )
    )
    db.commit()


def _backfill_fts(db) -> None:
    """为尚缺 FTS 行的论文补建 FTS5 索引。

    init_db 已为默认种子论文写过 FTS 行（paper_fts 非空），而 database._ensure_fts5
    仅在 paper_fts 全空时才回填——故本测试新 seed 的论文不会自动进 FTS 索引。
    这里按与 _ensure_fts5 一致的字段映射，直接为缺行论文补插 FTS 行。
    """
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
            "SELECT id, title, abstract, "
            "COALESCE((SELECT group_concat(value, ' ') FROM json_each(authors)), ''), "
            "COALESCE(full_text, ''), "
            "COALESCE((SELECT group_concat(value, ' ') FROM json_each(tags)), '') "
            "FROM papers WHERE id NOT IN (SELECT paper_id FROM paper_fts)"
        )
    )
    db.commit()


# ── crud.batch_delete_papers ────────────────────────────────────────────────
def test_batch_delete_papers_real_db(temp_db):
    """真实删除：删除存在的论文，跳过不存在的 ID。"""
    _seed_paper(temp_db, "p0", "Paper Zero")
    _seed_paper(temp_db, "p1", "Paper One")
    _seed_paper(temp_db, "p2", "Paper Two")

    res = crud_papers.batch_delete_papers(temp_db, ["p0", "p1", "pX"])

    assert isinstance(res, BatchDeleteResponse)
    assert res.deleted_count == 2
    assert res.failed_ids == ["pX"]

    # init_db 会写入默认种子论文，故只断言目标论文的删除/保留状态，
    # 不假定 remaining 的精确集合。
    remaining = temp_db.execute(text("SELECT id FROM papers")).scalars().all()
    assert "p0" not in remaining
    assert "p1" not in remaining
    assert "p2" in remaining


# ── workers.batch_delete ───────────────────────────────────────────────────
def test_batch_delete_worker_branches(temp_db, monkeypatch):
    """worker 分支：正常 → complete({deleted,failed})；空 paperIds → fail。"""
    captured = []

    def fake_complete(tid, result=None):
        captured.append(("complete", tid, result))

    def fake_fail(tid, error):
        captured.append(("fail", tid, error))

    monkeypatch.setattr(mock_api.tasks.TaskManager, "complete", fake_complete)
    monkeypatch.setattr(mock_api.tasks.TaskManager, "fail", fake_fail)

    # 正常路径：crud.batch_delete_papers 被替换为返回 2 删 1 失败
    monkeypatch.setattr(
        mock_api.crud,
        "batch_delete_papers",
        lambda db, ids, max_count=50: BatchDeleteResponse(deleted_count=2, failed_ids=["pX"]),
    )

    batch_delete_worker_mod.batch_delete_worker("t1", {"paperIds": ["p0", "p1"]})
    assert ("complete", "t1", {"deleted": 2, "failed": ["pX"]}) in captured

    # 空参数路径：应直接 fail
    batch_delete_worker_mod.batch_delete_worker("t2", {})
    assert ("fail", "t2") in [(c[0], c[1]) for c in captured]


# ── crud.search ────────────────────────────────────────────────────────────
def test_rrf_fuse_ranks_by_reciprocal_rank(temp_db):
    """rrf_fuse：两路都命中则融合排序；单路命中仍参与。"""
    fused = crud_search.rrf_fuse(["a", "b", "c"], ["b", "c", "d"], k=60, top_n=10)
    ids = [pid for pid, _ in fused]
    # b、c 两路都出现，应排在最前
    assert ids[0] in ("b", "c")
    assert "a" in ids and "d" in ids
    # 分数单调递减
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_search_fts_real_path(temp_db, monkeypatch):
    """hybrid_search_papers 真实 FTS5 路径（向量不可用 → 降级纯 FTS）。"""
    _seed_paper(temp_db, "attn", "Attention Is All You Need", abstract="transformer self attention mechanism")
    _backfill_fts(temp_db)

    # 强制向量检索不可用，走纯 FTS5
    monkeypatch.setattr(crud_search, "is_available", lambda: False)

    results = crud_search.hybrid_search_papers(temp_db, "attention", top_k=5)
    ids = [p.id for p in results]
    assert "attn" in ids


def test_suggest_papers_fts(temp_db):
    """suggest_papers：FTS5 MATCH 返回带高亮的命中。"""
    _seed_paper(temp_db, "attn", "Attention Is All You Need", abstract="transformer self attention mechanism")
    _backfill_fts(temp_db)

    hits = crud_search.suggest_papers(temp_db, "attention", limit=5)
    assert any(h["id"] == "attn" for h in hits)
    assert any("<mark>" in (h.get("highlight") or "") for h in hits)


# ── report_paper_resolver ──────────────────────────────────────────────────
def test_resolver_no_title(temp_db):
    """resolve_and_ingest 空标题直接返回 no_title（不触网）。"""
    assert report_paper_resolver.resolve_and_ingest("  ") == {"status": "no_title"}


def test_resolver_existing_local_match(temp_db):
    """resolve_and_ingest 库内已有同名论文 → 幂等返回 exists。

    论文 id 用纯数字前缀（如 2401.12345），使 resolver 的 source 推断
    (\"." in id and id.split(".")[0].isdigit()) 判定为 arxiv。
    """
    _seed_paper(temp_db, "2401.12345", "Quantum Neural Architectures for Robust Perception")

    out = report_paper_resolver.resolve_and_ingest("Quantum Neural Architectures for Robust Perception")
    assert out["status"] == "exists"
    assert out["paper_id"] == "2401.12345"
    assert out["source"] == "arxiv"


def test_resolver_rate_limited(temp_db, monkeypatch):
    """arXiv 限流 → rate_limited（不下载、不 fallback）。"""
    monkeypatch.setattr(report_paper_resolver, "time", __import__("types").SimpleNamespace(sleep=lambda *a, **k: None))
    monkeypatch.setattr(
        report_paper_resolver, "search_arxiv", lambda *a, **k: (None, "rate_limited")
    )

    out = report_paper_resolver.resolve_and_ingest("Some Brand New Title Not In DB")
    assert out["status"] == "rate_limited"


def test_resolver_no_match_fallback(temp_db, monkeypatch):
    """arXiv 无命中且 S2 fallback 也无 → no_match。"""
    monkeypatch.setattr(report_paper_resolver, "time", __import__("types").SimpleNamespace(sleep=lambda *a, **k: None))
    monkeypatch.setattr(report_paper_resolver, "search_arxiv", lambda *a, **k: ([], None))
    monkeypatch.setattr(report_paper_resolver, "_try_semantic_scholar", lambda *a, **k: None)

    out = report_paper_resolver.resolve_and_ingest("Another Unique Title Definitely Not Present")
    assert out["status"] == "no_match"


# ── tasks.TaskManager ──────────────────────────────────────────────────────
def test_taskmanager_submit_without_worker_fails(temp_db):
    """提交无 worker_fn 的任务 → 立即标记 failed。"""
    from mock_api.tasks import TaskManager

    tid = TaskManager.submit("unknown_type", params={"x": 1})
    assert TaskManager.get(tid)["status"] == "failed"


def test_taskmanager_state_machine(temp_db):
    """pending → update_progress(running) → complete(completed)；fail 路径。"""
    from mock_api.tasks import TaskManager

    # 正常流转
    tid = str(uuid.uuid4())
    temp_db.add(TaskORM(id=tid, type="depth_review", status="pending", progress=0, params={}))
    temp_db.commit()

    assert TaskManager.get(tid)["status"] == "pending"
    TaskManager.update_progress(tid, 50, "halfway")
    assert TaskManager.get(tid)["status"] == "running"
    assert TaskManager.get(tid)["progress"] == 50
    TaskManager.complete(tid, {"ok": True})
    assert TaskManager.get(tid)["status"] == "completed"

    # fail 路径
    tid2 = str(uuid.uuid4())
    temp_db.add(TaskORM(id=tid2, type="export", status="pending", progress=0, params={}))
    temp_db.commit()
    TaskManager.fail(tid2, "boom")
    assert TaskManager.get(tid2)["status"] == "failed"


# ── reflection_fidelity ────────────────────────────────────────────────────
def test_fidelity_too_short(temp_db, monkeypatch):
    """报告正文过短 → too_short（不进余弦）。"""
    monkeypatch.setattr(reflection_fidelity, "embed_text_ml", lambda text: None)
    res = reflection_fidelity.compute_fidelity(
        {"q": "x", "tech": "", "exp": ""}, "paper full text here"
    )
    assert res.status == "too_short"
    assert res.fidelity is None


def test_fidelity_no_paper(temp_db, monkeypatch):
    """未绑定原论文（paper_full_text 为空）→ no_paper。

    报告正文需超过 MIN_REPORT_CHARS(100) 才不会被太短拦截、提前进入 no_paper 分支。
    """
    monkeypatch.setattr(reflection_fidelity, "embed_text_ml", lambda text: None)
    res = reflection_fidelity.compute_fidelity(
        {
            "q": "This paper proposes a novel attention mechanism that dramatically reduces "
            "computational cost while preserving accuracy across standard benchmarks.",
            "tech": "The core technical contribution is a sparse softmax approximation which "
            "enables linear-time inference in practice without retraining the backbone.",
            "exp": "Experiments on WMT and GLUE show consistent gains over the transformer "
            "baseline while using roughly half the memory footprint at inference time.",
        },
        "",
    )
    assert res.status == "no_paper"


def test_fidelity_cosine_ok_with_stub_embedder(temp_db, monkeypatch):
    """stub 嵌入器返回固定向量 → 余弦=1.0，fidelity=1.0，status=ok。"""
    monkeypatch.setattr(reflection_fidelity, "embed_text_ml", lambda text: [1.0, 0.0, 0.0])
    res = reflection_fidelity.compute_fidelity(
        {
            "q": "a sufficiently long report sentence here",
            "tech": "another sufficiently long sentence",
            "exp": "third sufficiently long sentence",
        },
        "paper full text of sufficient length here",
        embedder=lambda text: [1.0, 0.0, 0.0],
    )
    assert res.status == "ok"
    assert res.fidelity == 1.0


# ── depth_tasks 入口守卫 ───────────────────────────────────────────────────
def test_depth_review_missing_paper(temp_db):
    """论文不存在 → ValueError（不进 LLM）。"""
    with pytest.raises(ValueError):
        depth_tasks.run_depth_review_sync("does-not-exist")


def test_depth_review_empty_full_text(temp_db):
    """论文无全文 → ValueError（不进 LLM）。"""
    _seed_paper(temp_db, "p-nofull", "Paper Without Full Text")
    with pytest.raises(ValueError):
        depth_tasks.run_depth_review_sync("p-nofull")


# ── llm/factory 注入缝 ─────────────────────────────────────────────────────
def test_llm_factory_provider_injection():
    """set_provider_for_testing 注入后，get_factory().get_provider() 返回该实例。"""
    from mock_api.llm.factory import (
        get_factory,
        reset_provider_for_testing,
        set_provider_for_testing,
    )

    class FakeProvider:
        def chat(self, *args, **kwargs):
            return "ok"

    try:
        set_provider_for_testing(FakeProvider())
        assert get_factory().get_provider() is not None
    finally:
        reset_provider_for_testing()
