"""Tests for reflection_docx_parser.parse_docx_from_bytes."""
from __future__ import annotations

import io
import zipfile

from mock_api.reflection_docx_parser import parse_docx_from_bytes


def _build_docx_bytes(paragraphs: list[str], filename: str = "2021001-张三.docx") -> bytes:
    """Build a minimal valid .docx file in memory."""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}</w:body>'
            '</w:document>',
        )
    return buf.getvalue()


class TestParseDocxFromBytes:
    """Unit tests for parse_docx_from_bytes."""

    def test_extracts_student_id_and_name_from_filename(self):
        """Filename 学号-姓名.docx → student_id / name 正确提取。"""
        paragraphs = ["论文题目：Attention Is All You Need", "一、问题", "二、技术", "三、实验", "四、感想"]
        data = _build_docx_bytes(paragraphs, filename="2021001-张三.docx")
        doc = parse_docx_from_bytes(data, filename="2021001-张三.docx")
        assert doc.student_id == "2021001"
        assert doc.name == "张三"

    def test_extracts_paper_title_from_header(self):
        """显式「论文题目：」标签优先级最高。"""
        data = _build_docx_bytes(["论文题目：Remote laboratories for education", "一、问题"])
        doc = parse_docx_from_bytes(data)
        assert doc.paper_title == "Remote laboratories for education"

    def test_extracts_paper_author_from_header(self):
        """显式「论文作者：」标签可提取作者。"""
        data = _build_docx_bytes(["论文作者：Benmohamed, et al.", "一、问题"])
        doc = parse_docx_from_bytes(data)
        assert doc.paper_author == "Benmohamed, et al."

    def test_fallback_title_from_english_first_line(self):
        """无显式标签时，首行英文标题作为兜底。"""
        data = _build_docx_bytes(["Attention Is All You Need", "一、问题"])
        doc = parse_docx_from_bytes(data)
        assert doc.paper_title == "Attention Is All You Need"

    def test_no_title_returns_no_title(self):
        """无显式标签且无英文标题 → paper_title 为 None。"""
        data = _build_docx_bytes(["一、问题", "这只是中文内容，没有标题。"])
        doc = parse_docx_from_bytes(data)
        assert doc.paper_title is None

    def test_sections_split(self):
        """四段式主节标记正确分节。"""
        paragraphs = [
            "论文题目：Test Paper",
            "一、问题描述",
            "这是问题部分。",
            "二、技术方法",
            "这是技术部分。",
            "三、实验结果",
            "这是实验部分。",
            "四、感想",
            "这是感想部分。",
        ]
        data = _build_docx_bytes(paragraphs)
        doc = parse_docx_from_bytes(data)
        assert set(doc.sections.keys()) == {"q", "tech", "exp", "reflection"}
        assert "问题描述" in doc.sections["q"]
        assert "技术方法" in doc.sections["tech"]
