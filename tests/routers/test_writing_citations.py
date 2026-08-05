"""学术诚信 validate_chapter_citations 单元测试。

覆盖引用校验：有效引用、伪造引用、空引用、不存在章节。
"""

from __future__ import annotations

import pytest
from mock_api.crud.writing import validate_chapter_citations
from mock_api.models import Chapter as ChapterORM
from mock_api.models import Paper as PaperORM
from mock_api.models import WritingProject as WritingProjectORM


@pytest.fixture
def db_with_papers(db_session):
    """创建一个带论文库的 session，包含两篇真实论文。"""
    db = db_session
    db.add(
        PaperORM(
            id="2401.12345",
            title="Test Paper A",
            authors=["Alice", "Bob"],
            year=2024,
            abstract="Test abstract A",
        )
    )
    db.add(
        PaperORM(
            id="2401.67890",
            title="Test Paper B",
            authors=["Charlie"],
            year=2023,
            abstract="Test abstract B",
        )
    )
    db.commit()
    return db


@pytest.fixture
def db_with_chapter(db_with_papers):
    """创建一个带写作项目和章节的 session。"""
    db = db_with_papers
    project = WritingProjectORM(title="测试项目")
    db.add(project)
    db.flush()
    chapter = ChapterORM(
        project_id=project.id,
        parent_id=None,
        title="引言",
        content="见 [@2401.12345] 与 [@2401.67890] 的研究。",
        order=0,
    )
    db.add(chapter)
    db.commit()
    return db, chapter


class TestValidateChapterCitations:
    """validate_chapter_citations 核心用例。"""

    def test_all_valid_citations(self, db_with_chapter) -> None:
        """所有引用均命中论文库 → valid=全部, invalid=空。"""
        db, chapter = db_with_chapter
        r = validate_chapter_citations(db, chapter.id)
        assert r["chapter_id"] == chapter.id
        assert sorted(r["cited"]) == sorted(["2401.12345", "2401.67890"])
        assert sorted(r["valid"]) == sorted(["2401.12345", "2401.67890"])
        assert r["invalid"] == []
        assert r["has_invalid"] is False

    def test_fabricated_citation_detected(self, db_with_chapter) -> None:
        """伪造引用 → invalid 非空, has_invalid=True。"""
        db, chapter = db_with_chapter
        chapter.content = "参考 [@2401.12345] 和 [@9999.fake] 的工作。"
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["valid"] == ["2401.12345"]
        assert r["invalid"] == ["9999.fake"]
        assert r["has_invalid"] is True

    def test_all_fabricated(self, db_with_chapter) -> None:
        """全部引用都不在库中 → valid=空, invalid=全部。"""
        db, chapter = db_with_chapter
        chapter.content = "见 [@0000.fake1] 和 [@0000.fake2]。"
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["valid"] == []
        assert sorted(r["invalid"]) == sorted(["0000.fake1", "0000.fake2"])
        assert r["has_invalid"] is True

    def test_no_citations(self, db_with_chapter) -> None:
        """章节无任何引用 → 全空。"""
        db, chapter = db_with_chapter
        chapter.content = "这是一段普通的正文，没有任何引用标记。"
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["cited"] == []
        assert r["valid"] == []
        assert r["invalid"] == []
        assert r["has_invalid"] is False

    def test_nonexistent_chapter(self, db_with_papers) -> None:
        """章节不存在 → 返回空但 has_invalid=False（非错误态）。"""
        db = db_with_papers
        r = validate_chapter_citations(db, 9999)
        assert r["chapter_id"] == 9999
        assert r["cited"] == []
        assert r["valid"] == []
        assert r["invalid"] == []
        assert r["has_invalid"] is False

    def test_inline_content_param(self, db_with_papers) -> None:
        """传入 content 参数 → 直接校验传入内容（不走 DB 读取章节正文）。"""
        db = db_with_papers
        r = validate_chapter_citations(
            db, chapter_id=1, content="见 [@2401.12345] 和 [@0000.ghost]。"
        )
        assert r["valid"] == ["2401.12345"]
        assert r["invalid"] == ["0000.ghost"]
        assert r["has_invalid"] is True

    def test_inline_content_param_no_db_chapter_needed(self, db_with_papers) -> None:
        """传入 content 参数时，即使章节 ID 不存在也不影响引用扫描。"""
        db = db_with_papers
        r = validate_chapter_citations(
            db, chapter_id=99999, content="引用 [@2401.12345] 的发现。"
        )
        assert r["valid"] == ["2401.12345"]
        assert r["invalid"] == []

    def test_dedup_citations(self, db_with_chapter) -> None:
        """重复引用 → cited 去重，valid 不重复。"""
        db, chapter = db_with_chapter
        chapter.content = "[@2401.12345] 与 [@2401.12345] 的结果一致。"
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["cited"] == ["2401.12345"]
        assert r["valid"] == ["2401.12345"]
        assert r["invalid"] == []

    def test_whitespace_in_cite_id(self, db_with_chapter) -> None:
        """引用 ID 含空白 → strip 后匹配。"""
        db, chapter = db_with_chapter
        chapter.content = "参考 [@ 2401.12345 ] 的研究。"
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["valid"] == ["2401.12345"]

    def test_empty_content(self, db_with_chapter) -> None:
        """章节内容为空字符串 → 无引用。"""
        db, chapter = db_with_chapter
        chapter.content = ""
        db.commit()
        r = validate_chapter_citations(db, chapter.id)
        assert r["cited"] == []
