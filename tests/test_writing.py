"""测试 mock_api/crud.py 中写作模块的 CRUD、模板创建、字数统计、导出引用替换。

覆盖场景：
- 项目 CRUD：create_project（无模板 → 自动创建「引言」章节）、get_projects
- 章节 CRUD：create_chapter、update_chapter（内容变更自动保存历史版本）、get_chapter_tree
- 模板自定义：create_user_template、save_project_as_template、find_user_template、按模板创建项目
- 字数统计：_count_words（中英文 / Markdown 标记剔除）、get_word_count
- 导出引用替换：export_project_markdown 解析 [@id] → [N] 编号 + 参考文献列表
- 版本历史：create_chapter_version FIFO 上限、restore_chapter_version 恢复
"""
from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mock_api.database import Base
from mock_api import models  # noqa: F401  # 注册所有 ORM 映射
from mock_api import crud
from mock_api.schemas import (
    ChapterCreate,
    ChapterUpdate,
    NoteCreate,
    NoteUpdate,
    ProjectCreate,
    UserTemplateCreate,
)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """每个测试用全新的内存数据库，互不干扰。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


# ===========================================================================
# 项目 CRUD
# ===========================================================================
def test_create_project_without_template_creates_intro_chapter(db: Session):
    """无模板创建项目 → 自动生成单个「引言」章节。"""
    payload = ProjectCreate(title="测试论文", keywords=["深度学习", "Transformer"])
    proj = crud.create_project(db, payload)

    assert proj.id > 0
    assert proj.title == "测试论文"
    assert proj.keywords == ["深度学习", "Transformer"]
    assert proj.chapterCount == 1

    tree = crud.get_chapter_tree(db, proj.id)
    assert len(tree) == 1
    assert tree[0].title == "引言"
    assert tree[0].content == ""


def test_create_project_strips_whitespace(db: Session):
    """项目标题去除首尾空白。"""
    payload = ProjectCreate(title="  带空格的标题  ", targetJournal="  Nature  ")
    proj = crud.create_project(db, payload)
    assert proj.title == "带空格的标题"


def test_get_projects_returns_newest_first(db: Session):
    """get_projects 按创建时间倒序返回。"""
    p1 = crud.create_project(db, ProjectCreate(title="第一篇"))
    p2 = crud.create_project(db, ProjectCreate(title="第二篇"))
    rows = crud.get_projects(db)
    assert len(rows) == 2
    assert rows[0].id == p2.id
    assert rows[1].id == p1.id


# ===========================================================================
# 章节 CRUD
# ===========================================================================
def test_create_chapter_appends_to_sibling_end(db: Session):
    """新建章节默认追加到同级末尾（order 自增）。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    c1 = crud.create_chapter(db, proj.id, ChapterCreate(title="方法"))
    c2 = crud.create_chapter(db, proj.id, ChapterCreate(title="结果"))
    assert c1.order == 1
    assert c2.order == 2


def test_create_chapter_with_parent(db: Session):
    """指定 parentId 时挂到父章节下。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    child = crud.create_chapter(
        db, proj.id, ChapterCreate(title="研究背景", parentId=intro.id)
    )
    tree = crud.get_chapter_tree(db, proj.id)
    assert len(tree[0].children) == 1
    assert tree[0].children[0].title == "研究背景"


def test_create_chapter_invalid_project_raises(db: Session):
    """项目不存在 → ValueError。"""
    with pytest.raises(ValueError, match="不存在"):
        crud.create_chapter(db, 9999, ChapterCreate(title="x"))


def test_update_chapter_content_auto_saves_version(db: Session):
    """更新章节内容时自动保存历史版本。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    chapter_id = intro.id

    crud.update_chapter(db, chapter_id, ChapterUpdate(content="第一版内容"))
    versions = crud.get_chapter_versions(db, chapter_id)
    assert len(versions) == 1

    crud.update_chapter(db, chapter_id, ChapterUpdate(content="第二版内容"))
    versions = crud.get_chapter_versions(db, chapter_id)
    assert len(versions) == 2


def test_update_chapter_same_content_no_version(db: Session):
    """内容未变化时不保存历史版本。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(content="相同内容"))
    crud.update_chapter(db, intro.id, ChapterUpdate(content="相同内容"))
    versions = crud.get_chapter_versions(db, intro.id)
    assert len(versions) == 1


def test_update_chapter_title_only(db: Session):
    """仅更新标题不触发版本保存。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(content="内容"))
    crud.update_chapter(db, intro.id, ChapterUpdate(title="新标题"))
    versions = crud.get_chapter_versions(db, intro.id)
    assert len(versions) == 1


# ===========================================================================
# 模板自定义
# ===========================================================================
def test_create_user_template(db: Session):
    """新建用户自定义模板。"""
    from mock_api.schemas import TemplateChapter
    payload = UserTemplateCreate(
        name="我的模板",
        description="用于测试",
        chapters=[TemplateChapter(title="引言", children=[]),
                  TemplateChapter(title="方法", children=[])],
    )
    tmpl = crud.create_user_template(db, payload)
    assert tmpl.id
    assert tmpl.name == "我的模板"
    assert tmpl.description == "用于测试"
    assert len(tmpl.chapters) == 2
    assert tmpl.chapters[0].title == "引言"


def test_save_project_as_template(db: Session):
    """将现有项目大纲保存为模板。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.create_chapter(db, proj.id, ChapterCreate(title="方法", parentId=intro.id))
    crud.create_chapter(db, proj.id, ChapterCreate(title="结果"))

    tmpl = crud.save_project_as_template(db, proj.id, name="导出模板", description="从项目导出")
    assert tmpl.name == "导出模板"
    titles = [c.title for c in tmpl.chapters]
    assert "引言" in titles
    assert "结果" in titles
    intro_ch = next(c for c in tmpl.chapters if c.title == "引言")
    assert len(intro_ch.children) == 1
    assert intro_ch.children[0].title == "方法"


def test_save_project_as_template_nonexistent_returns_none(db: Session):
    """项目不存在 → 返回 None。"""
    assert crud.save_project_as_template(db, 9999, name="x") is None


def test_create_project_with_user_template(db: Session):
    """按用户自定义模板创建项目，章节结构应一致。"""
    from mock_api.schemas import TemplateChapter
    payload = UserTemplateCreate(
        name="T",
        chapters=[
            TemplateChapter(title="第一章", children=[
                TemplateChapter(title="1.1", children=[]),
            ]),
            TemplateChapter(title="第二章", children=[]),
        ],
    )
    tmpl = crud.create_user_template(db, payload)

    proj = crud.create_project(db, ProjectCreate(title="按模板", templateId=tmpl.id))
    assert proj.chapterCount == 3

    tree = crud.get_chapter_tree(db, proj.id)
    assert len(tree) == 2
    assert tree[0].title == "第一章"
    assert len(tree[0].children) == 1
    assert tree[0].children[0].title == "1.1"
    assert tree[1].title == "第二章"


def test_find_user_template_nonexistent_returns_none(db: Session):
    """查找不存在的用户模板 → None。"""
    assert crud.find_user_template(db, "nonexistent-uuid") is None


def test_delete_user_template(db: Session):
    """删除用户模板。"""
    tmpl = crud.create_user_template(
        db, UserTemplateCreate(name="待删除", chapters=[])
    )
    assert crud.delete_user_template(db, tmpl.id) is True
    assert crud.find_user_template(db, tmpl.id) is None
    assert crud.delete_user_template(db, tmpl.id) is False


# ===========================================================================
# 字数统计
# ===========================================================================
def test_count_words_chinese(db: Session):
    """纯中文：按非空白字符计数。"""
    assert crud._count_words("深度学习是机器学习的子领域") == 13


def test_count_words_english(db: Session):
    """英文：按非空白字符计数（非单词数）。"""
    assert crud._count_words("hello world") == 10


def test_count_words_empty(db: Session):
    assert crud._count_words("") == 0
    assert crud._count_words(None) == 0  # type: ignore[arg-type]


def test_count_words_strips_markdown(db: Session):
    """Markdown 标记不计入字数。"""
    text = "# 标题\n**重点**内容[论文](url)[@1706.03762]"
    assert crud._count_words(text) == 8


def test_count_words_strips_code_block(db: Session):
    """代码块不计入字数。"""
    text = "正文\n```\ncode here\n```\n后续"
    assert crud._count_words(text) == 4


def test_get_word_count(db: Session):
    """get_word_count 返回总字数 + 各章节字数。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(content="中文内容"))

    wc = crud.get_word_count(db, proj.id)
    assert wc is not None
    assert wc.total == 4
    assert len(wc.chapters) == 1
    assert wc.chapters[0].wordCount == 4


def test_get_word_count_nonexistent_project(db: Session):
    """项目不存在 → None。"""
    assert crud.get_word_count(db, 9999) is None


# ===========================================================================
# 导出引用替换
# ===========================================================================
def test_export_replaces_citation_markers(db: Session):
    """export_project_markdown 将 [@id] 替换为 [N] 编号。"""
    from mock_api.models import Paper
    db.add(Paper(
        id="1706.03762",
        title="Attention Is All You Need",
        authors=["Vaswani", "Shazeer"],
        year=2017,
        abstract="",
        category="cs",
    ))
    db.commit()

    proj = crud.create_project(db, ProjectCreate(title="我的综述"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(
        content="本文基于 Transformer 架构[@1706.03762]展开讨论。"
    ))

    result = crud.export_project_markdown(db, proj.id)
    assert result is not None
    content, filename, refs = result
    assert "[@1706.03762]" not in content
    assert "[1]" in content
    assert len(refs) == 1
    assert refs[0].id == "1706.03762"
    assert refs[0].title == "Attention Is All You Need"
    assert "Vaswani" in refs[0].authors
    assert "## 参考文献" in content


def test_export_preserves_citation_order(db: Session):
    """多引用按首次出现顺序编号。"""
    from mock_api.models import Paper
    db.add(Paper(id="paper_a", title="论文A", authors=[], year=2020, abstract="", category="x"))
    db.add(Paper(id="paper_b", title="论文B", authors=[], year=2021, abstract="", category="x"))
    db.commit()

    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(
        content="先引[@paper_b]再引[@paper_a]再引[@paper_b]"
    ))

    result = crud.export_project_markdown(db, proj.id)
    assert result is not None
    content, _, refs = result
    assert "[1]" in content
    assert "[2]" in content
    assert refs[0].id == "paper_b"
    assert refs[1].id == "paper_a"
    assert len(refs) == 2


def test_export_missing_paper_kept_as_placeholder(db: Session):
    """引用不存在的论文 → 保留占位条目。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    crud.update_chapter(db, intro.id, ChapterUpdate(content="引用[@ghost_paper]不存在"))

    result = crud.export_project_markdown(db, proj.id)
    assert result is not None
    _, _, refs = result
    assert len(refs) == 1
    assert "未找到" in refs[0].title


def test_export_no_citations(db: Session):
    """无引用时导出正常，references 为空。"""
    proj = crud.create_project(db, ProjectCreate(title="无引用论文"))
    crud.update_chapter(
        db, crud.get_chapter_tree(db, proj.id)[0].id,
        ChapterUpdate(content="这是一段没有引用的正文内容"),
    )
    result = crud.export_project_markdown(db, proj.id)
    assert result is not None
    content, _, refs = result
    assert refs == []
    assert "## 参考文献" not in content


def test_export_nonexistent_project_returns_none(db: Session):
    """项目不存在 → None。"""
    assert crud.export_project_markdown(db, 9999) is None


# ===========================================================================
# 版本历史
# ===========================================================================
def test_create_chapter_version_fifo_eviction(db: Session):
    """超过 10 个版本时自动删除最旧的（FIFO）。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    chapter_id = intro.id

    for i in range(12):
        crud.create_chapter_version(db, chapter_id, f"版本{i}")

    versions = crud.get_chapter_versions(db, chapter_id)
    assert len(versions) == crud.MAX_VERSIONS_PER_CHAPTER
    contents = [crud.get_chapter_version(db, v.id).content for v in versions]
    assert "版本0" not in contents
    assert "版本1" not in contents
    assert "版本11" in contents


def test_get_chapter_version_nonexistent(db: Session):
    """版本不存在 → None。"""
    assert crud.get_chapter_version(db, "nonexistent") is None


def test_restore_chapter_version(db: Session):
    """恢复章节到历史版本：内容替换 + 当前内容另存为新版本。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    chapter_id = intro.id

    # v1: "" → "原始内容"（保存旧内容 ""）
    crud.update_chapter(db, chapter_id, ChapterUpdate(content="原始内容"))
    # v2: "原始内容" → "修改后内容"（保存旧内容 "原始内容"）
    crud.update_chapter(db, chapter_id, ChapterUpdate(content="修改后内容"))

    versions = crud.get_chapter_versions(db, chapter_id)
    assert len(versions) == 2
    # 倒序排列：versions[0] = 最新 = "原始内容"，versions[-1] = 最旧 = ""
    target = versions[0]
    target_detail = crud.get_chapter_version(db, target.id)
    assert target_detail.content == "原始内容"

    # 恢复到 "原始内容" 版本，当前内容 "修改后内容" 另存为新版本
    result = crud.restore_chapter_version(db, chapter_id, target.id)
    assert result is not None
    assert result.content == "原始内容"
    versions_after = crud.get_chapter_versions(db, chapter_id)
    assert len(versions_after) == 3


def test_restore_nonexistent_version_returns_none(db: Session):
    """版本不存在 → None。"""
    proj = crud.create_project(db, ProjectCreate(title="P"))
    intro = crud.get_chapter_tree(db, proj.id)[0]
    assert crud.restore_chapter_version(db, intro.id, "ghost") is None


# ===========================================================================
# 论文笔记 CRUD
# ===========================================================================
def test_note_crud(db: Session):
    """笔记的增删改查 + 项目关联查询。"""
    from mock_api.models import Paper
    db.add(Paper(id="note_test", title="笔记测试论文", authors=[], year=2020,
                 abstract="", category="x"))
    db.commit()

    proj = crud.create_project(db, ProjectCreate(title="笔记项目"))

    n1 = crud.create_note(db, NoteCreate(
        paperId="note_test", projectId=proj.id, content="第一条笔记"
    ))
    assert n1 is not None
    assert n1.content == "第一条笔记"
    assert n1.projectId == proj.id

    n2 = crud.create_note(db, NoteCreate(paperId="note_test", content="无项目笔记"))
    assert n2 is not None

    paper_notes = crud.get_notes_by_paper(db, "note_test")
    assert len(paper_notes) == 2

    proj_notes = crud.get_notes_by_project(db, proj.id)
    assert len(proj_notes) == 1
    assert proj_notes[0].id == n1.id

    updated = crud.update_note(db, n1.id, NoteUpdate(content="更新后的笔记"))
    assert updated is not None
    assert updated.content == "更新后的笔记"

    assert crud.delete_note(db, n1.id) is True
    assert crud.delete_note(db, n1.id) is False
    paper_notes = crud.get_notes_by_paper(db, "note_test")
    assert len(paper_notes) == 1
