"""端到端测试：parse_authors → 引用导出链路（Markdown / LaTeX / CSL / worker / HTTP）作者名正确。

背景（2026-08-03）：papers.authors 曾存在双重编码脏数据（``json.dumps`` 写入
SQLAlchemy JSON 列导致 ``"[\"Cosmin Pohoata\"]"`` 嵌套），旧代码用 ``list(authors)``
读取会逐字符拆分成 ``['[', '"', 'C', ...]``，导致导出参考文献出现 ``[, C et al.``
乱码。本测试模拟库中仍可能存在的脏数据格式，验证**导出链路**（Word/LaTeX 的
数据源）中作者名正确还原，防止回归。

覆盖的导出路径（均消费 parse_authors 后的 references）：
1. export_project_markdown  → Markdown 参考文献文本 + references 列表
2. export_project_to_folder → refs.bib（LaTeX/BibTeX）
3. export_project_csl       → CSL-JSON（Word 渲染面板数据源）
4. workers/export.py        → 后台 worker 的 references dict
5. HTTP 端点                → GET /api/writing/projects/{id}/export/csl
"""

from __future__ import annotations

from mock_api import crud
from mock_api.models import Paper
from mock_api.schemas import ChapterUpdate, ProjectCreate


# ---------------------------------------------------------------------------
# 辅助：种子脏数据论文 + 引用项目
# ---------------------------------------------------------------------------
def _seed_paper(db, paper_id: str, title: str, authors_raw: object, year: int) -> None:
    """写入一篇论文，authors 直接存脏数据原始格式（双重编码字符串）。"""
    db.add(
        Paper(
            id=paper_id,
            title=title,
            authors=authors_raw,
            year=year,
            abstract="",
            category="cs",
            tags=[],
        )
    )


def _seed_project_with_citations(db) -> int:
    """创建写作项目 + 引用两篇脏数据论文的章节，返回 project_id。

    注意：此处使用真实 arXiv id（2607.20422 / 2106.09685），它们恰好也是
    data.SEED_PAPERS 的种子 id —— 仅当本测试**不使用 client fixture** 时安全
    （client 的 lifespan 会 init_db → seed_if_empty 插入种子论文导致 UNIQUE
    冲突）。若未来需要给这些测试加 client，须改用专属 id（参考 HTTP 测试）。
    """
    _seed_paper(db, "2607.20422", "DeepSeek-V3 技术报告", r'"[\"Cosmin Pohoata\"]"', 2026)
    _seed_paper(
        db,
        "2106.09685",
        "LoRA: Low-Rank Adaptation",
        r'"[\"Edward J. Hu\", \"Yelong Shen\", \"Phillip Wallis\"]"',
        2021,
    )
    db.commit()

    proj = crud.create_project(db, ProjectCreate(title="端到端综述"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(
        db,
        intro.id,
        ChapterUpdate(content="本文研究 DeepSeek[@2607.20422] 与 LoRA[@2106.09685] 的对比。"),
    )
    return proj.id


# ---------------------------------------------------------------------------
# 1. Markdown 导出（export_project_markdown）
# ---------------------------------------------------------------------------
def test_export_markdown_references_authors_clean(db_session):
    """双重编码论文导出后，references.authors 与参考文献文本均为正常人名。"""
    proj_id = _seed_project_with_citations(db_session)

    result = crud.export_project_markdown(db_session, proj_id)
    assert result is not None
    content, _filename, refs = result

    # references 结构化字段：作者名还原
    assert refs[0].authors == ["Cosmin Pohoata"]
    assert refs[1].authors == ["Edward J. Hu", "Yelong Shen", "Phillip Wallis"]

    # Markdown 参考文献文本：正常人名 + 无逐字符拆分乱码
    assert "Cosmin Pohoata" in content
    assert "Edward J. Hu, Yelong Shen, Phillip Wallis" in content
    assert "[, C" not in content  # 旧 bug 特征：list() 逐字符拆分
    # 原始 JSON 泄漏检查（注意：必须拆成两个独立断言，不能写成
    # assert "['", '["' not in content —— 那是恒真的二元组 no-op）
    assert "['" not in content
    assert '["' not in content


def test_export_markdown_single_author_quoted_comma(db_session):
    """引号包裹的逗号串格式（'\"Edward J. Hu, Yelong Shen\"'）导出同样正确。"""
    _seed_paper(db_session, "2305.14314", "QLoRA", '"Edward J. Hu, Yelong Shen"', 2023)
    db_session.commit()

    proj = crud.create_project(db_session, ProjectCreate(title="逗号串"))
    intro = crud.get_chapter_tree(db_session, proj.id)[0]
    crud.update_chapter(db_session, intro.id, ChapterUpdate(content="引[@2305.14314]"))

    result = crud.export_project_markdown(db_session, proj.id)
    assert result is not None
    content, _filename, refs = result
    assert refs[0].authors == ["Edward J. Hu", "Yelong Shen"]
    assert "Edward J. Hu, Yelong Shen" in content


# ---------------------------------------------------------------------------
# 2. LaTeX / BibTeX 导出（export_project_to_folder → refs.bib）
# ---------------------------------------------------------------------------
def test_export_folder_bibtex_authors_clean(db_session, monkeypatch, tmp_path):
    """refs.bib 中 author={...} 为正常人名（and 连接），无乱码。"""
    import mock_api.database as db_module

    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    proj_id = _seed_project_with_citations(db_session)

    result = crud.export_project_to_folder(db_session, proj_id, "e2e_bib")
    assert result["success"] is True
    assert "refs.bib" in result["files"]

    bib = (tmp_path / "exports" / "e2e_bib" / "refs.bib").read_text(encoding="utf-8")
    assert "author={Cosmin Pohoata}" in bib
    assert "author={Edward J. Hu and Yelong Shen and Phillip Wallis}" in bib
    assert "[, C" not in bib
    # BibTeX key 用论文 id（点转下划线）
    assert "@article{2607_20422," in bib
    assert "@article{2106_09685," in bib


def test_export_folder_bibtex_missing_authors_fallback(db_session, monkeypatch, tmp_path):
    """无作者论文 → author={Unknown} 兜底（不崩溃）。"""
    import mock_api.database as db_module

    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    _seed_paper(db_session, "p_no_authors", "无作者论文", [], 2020)
    db_session.commit()

    proj = crud.create_project(db_session, ProjectCreate(title="无作者"))
    intro = crud.get_chapter_tree(db_session, proj.id)[0]
    crud.update_chapter(db_session, intro.id, ChapterUpdate(content="引[@p_no_authors]"))

    crud.export_project_to_folder(db_session, proj.id, "e2e_noauth")
    bib = (tmp_path / "exports" / "e2e_noauth" / "refs.bib").read_text(encoding="utf-8")
    assert "author={Unknown}" in bib


# ---------------------------------------------------------------------------
# 3. CSL-JSON 导出（export_project_csl）
# ---------------------------------------------------------------------------
def test_export_csl_authors_clean(db_session):
    """CSL-JSON 的 author family/given 拆分正确。"""
    proj_id = _seed_project_with_citations(db_session)

    items = crud.export_project_csl(db_session, proj_id)
    assert items is not None
    assert len(items) == 2

    # "Cosmin Pohoata" → family=Pohoata, given=Cosmin
    assert items[0]["author"] == [{"family": "Pohoata", "given": "Cosmin"}]
    # "Edward J. Hu" → family=Hu, given="Edward J."
    assert items[1]["author"][0] == {"family": "Hu", "given": "Edward J."}
    assert items[1]["author"][1] == {"family": "Shen", "given": "Yelong"}


# ---------------------------------------------------------------------------
# 4. worker 链路（workers/export.py）
# ---------------------------------------------------------------------------
def test_export_worker_references_authors_clean(db_session, monkeypatch):
    """后台 worker 输出的 references dict 作者名正确（真实 DB + mock TaskManager）。"""
    from mock_api.workers.export import export_worker

    proj_id = _seed_project_with_citations(db_session)

    captured: dict = {}

    monkeypatch.setattr("mock_api.database.SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "mock_api.tasks.TaskManager",
        type(
            "MockTaskManager",
            (),
            {
                "complete": staticmethod(
                    lambda task_id, result: captured.setdefault("result", result)
                ),
                "fail": staticmethod(
                    lambda task_id, error: captured.setdefault("error", error)
                ),
                "update_progress": staticmethod(lambda *a, **k: None),
            },
        ),
    )

    export_worker("task-e2e", {"projectId": proj_id})

    assert "error" not in captured
    refs = captured["result"]["references"]
    assert refs[0]["authors"] == ["Cosmin Pohoata"]
    assert refs[1]["authors"] == ["Edward J. Hu", "Yelong Shen", "Phillip Wallis"]


# ---------------------------------------------------------------------------
# 5. HTTP 端点（GET /api/writing/projects/{id}/export/csl）
# ---------------------------------------------------------------------------
def test_export_csl_endpoint_authors_clean(client, db_session):
    """HTTP 端点返回的 CSL-JSON 作者名正确（routers → crud → DB 全链路）。

    注意：client 的 lifespan 会触发 init_db → seed_if_empty 插入种子论文
    （data.SEED_PAPERS 含真实 arXiv id 如 2106.09685），故此处使用不与
    种子冲突的专属 id，避免 UNIQUE 冲突。
    """
    _seed_paper(db_session, "e2e_double", "DeepSeek-V3 技术报告", r'"[\"Cosmin Pohoata\"]"', 2026)
    _seed_paper(
        db_session,
        "e2e_comma",
        "LoRA: Low-Rank Adaptation",
        '"Edward J. Hu, Yelong Shen"',
        2021,
    )
    db_session.commit()

    proj = crud.create_project(db_session, ProjectCreate(title="端到端综述"))
    intro = crud.get_chapter_tree(db_session, proj.id)[0]
    crud.update_chapter(
        db_session,
        intro.id,
        ChapterUpdate(content="本文研究 DeepSeek[@e2e_double] 与 LoRA[@e2e_comma] 的对比。"),
    )

    resp = client.get(f"/api/writing/projects/{proj.id}/export/csl")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["author"] == [{"family": "Pohoata", "given": "Cosmin"}]
    assert items[1]["author"][0] == {"family": "Hu", "given": "Edward J."}
