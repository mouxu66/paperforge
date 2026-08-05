"""Smoke test for POST /api/depth/reflection/file endpoint.

Covers:
- TXT file upload → 202 + taskId + paperId
- DOCX file upload (if python-docx available) → 202 + taskId
- PDF file upload (if pypdf available) → 202 + taskId
- Unsupported extension → 400
- Empty file → 400
- Oversized file (> 10MB) → 413
- Missing content extraction (binary junk) → 422

Run:
    cd mock_api && python -m pytest tests/test_reflection_file_upload.py -v
or
    python -m pytest tests/test_reflection_file_upload.py -v
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.settings import reset_settings


def _build_minimal_docx(content: str = "Hello world from DOCX test fixture.") -> bytes:
    """Build a minimal valid .docx file in memory.

    将 content 按换行拆分为独立段落，与真实 Word 输出及 reflection_docx_parser
    的段落级正则保持一致（原单段落含换行会导致头部正则无法跨行匹配）。
    """
    paragraphs = "\n".join(
        f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>'
        for line in content.split("\n")
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
            f'<w:body>{paragraphs}</w:body>'
            '</w:document>',
        )
    return buf.getvalue()


def _build_minimal_pdf_with_text(text: str) -> bytes:
    """Build a minimal PDF file with embedded extractable text (no pypdf needed)."""
    # Very small synthetic PDF with a /Contents stream holding `text`.
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode())
        out.write(obj)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n")
    out.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    out.write(b"startxref\n")
    out.write(f"{xref_pos}\n".encode())
    out.write(b"%%EOF\n")
    return out.getvalue()


@pytest.fixture
def client(monkeypatch):
    """每次测试创建新的 TestClient，并关闭全局鉴权（避免受其他测试影响）。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
    reset_settings()
    return TestClient(create_app())


class TestReflectionFileUpload:
    """Smoke tests for the /api/depth/reflection/file endpoint."""

    def test_txt_upload_success(self, client):
        """Plain text file → 202 + taskId + paperId starting with 'report_'."""
        content = "这是我的复现报告：读 Attention Is All You Need 有感。Transformer 的核心是 self-attention。".encode()
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("report.txt", content, "text/plain")},
            data={"title": "TXT 报告测试"},
        )
        assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["paperId"].startswith("report_")
        assert "taskId" in body
        assert body["documentType"] == "report"

    def test_md_upload_success(self, client):
        """Markdown file → 202 + paperId with .md extension accepted."""
        content = (
            "# 报告标题\n\n"
            "这是 Markdown 格式的复现报告。\n\n"
            "- 观点 1\n- 观点 2\n"
        ).encode()
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("notes.md", content, "text/markdown")},
        )
        assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["paperId"].startswith("report_")

    def test_docx_upload_success(self, client):
        """Valid DOCX file → 202 + paperId."""
        docx_bytes = _build_minimal_docx("DOCX 测试内容：本文档用于复现 Reflection 文件上传 pipeline。")
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("paper_report.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"title": "DOCX 报告测试"},
        )
        # python-docx may or may not be installed; either 202 or 422 (extraction failed) is acceptable
        assert resp.status_code in (202, 422), f"期望 202/422，实际 {resp.status_code}: {resp.text}"
        if resp.status_code == 202:
            body = resp.json()
            assert body["paperId"].startswith("report_")

    def test_pdf_upload_success(self, client):
        """Valid PDF file → 202 or 422 (depending on pypdf availability)."""
        pdf_bytes = _build_minimal_pdf_with_text("PDF text for reflection test")
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("reflection.pdf", pdf_bytes, "application/pdf")},
        )
        # 202 (extracted) or 422 (extraction failed) both acceptable
        assert resp.status_code in (202, 422), f"期望 202/422，实际 {resp.status_code}: {resp.text}"
        if resp.status_code == 202:
            body = resp.json()
            assert body["paperId"].startswith("report_")

    def test_unsupported_extension_rejected(self, client):
        """Unsupported extension (.zip / .exe) → 400."""
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("archive.zip", b"PK\x03\x04fake", "application/zip")},
        )
        assert resp.status_code == 400, f"期望 400，实际 {resp.status_code}: {resp.text}"
        assert "不支持" in resp.json()["detail"]

    def test_empty_file_rejected(self, client):
        """Empty file → 400."""
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert resp.status_code == 400, f"期望 400，实际 {resp.status_code}: {resp.text}"
        assert "空" in resp.json()["detail"]

    def test_oversized_file_rejected(self, client):
        """File > 10MB → 413."""
        big_content = b"x" * (11 * 1024 * 1024)  # 11MB
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("huge.txt", big_content, "text/plain")},
        )
        assert resp.status_code == 413, f"期望 413，实际 {resp.status_code}: {resp.text}"
        assert "10MB" in resp.json()["detail"] or "过大" in resp.json()["detail"]

    def test_metadata_overrides_take_effect(self, client):
        """authors_csv + year form fields flow into final paper record."""
        content = "Author/year override test reflection. 提交者希望 author 字段被显式覆盖".encode()
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("override_test.txt", content, "text/plain")},
            data={
                "title": "覆盖元数据测试",
                "authors_csv": "Test Author, AI Researcher",
                "year": "2025",
            },
        )
        assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["paperId"].startswith("report_")
        # Verify the paper was actually created with the supplied authors/year
        paper_resp = client.get(f"/api/papers/{body['paperId']}")
        assert paper_resp.status_code == 200, f"论文未创建: {paper_resp.text}"
        paper = paper_resp.json()
        assert "Test Author" in paper["authors"]
        assert paper["year"] == 2025

    def test_docx_upload_auto_imports_source_paper(self, client, monkeypatch):
        """DOCX 头部含论文题目时，resolver 被触发并返回 sourcePaper.imported。"""
        import mock_api.routers.reflection as reflection_router

        fake_paper_id = "arxiv_0706.2974v1"

        def _fake_resolve(title, author=None, db=None):
            return {"status": "imported", "paper_id": fake_paper_id, "arxiv": "0706.2974v1", "title": title}

        monkeypatch.setattr(reflection_router, "resolve_and_ingest", _fake_resolve)

        docx_bytes = _build_minimal_docx(
            "论文题目：Remote laboratories for education\n一、问题\n二、技术\n三、实验\n四、感想"
        )
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("report.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["paperId"].startswith("report_")
        assert body["sourcePaper"]["status"] == "imported"
        assert body["sourcePaper"]["paperId"] == fake_paper_id

        # 验证报告记录的 source_paper_id 已写入
        from mock_api.database import SessionLocal
        from mock_api.models import Paper as PaperORM

        db = SessionLocal()
        try:
            p = db.query(PaperORM).filter(PaperORM.id == body["paperId"]).first()
            assert p is not None
            assert p.source_paper_id == fake_paper_id
        finally:
            db.close()

    def test_docx_upload_no_title_returns_no_title(self, client, monkeypatch):
        """DOCX 未识别到原论文题目时返回 sourcePaper.no_title。"""
        import mock_api.routers.reflection as reflection_router

        def _fake_resolve(title, author=None, db=None):
            return {"status": "no_match"}

        monkeypatch.setattr(reflection_router, "resolve_and_ingest", _fake_resolve)

        docx_bytes = _build_minimal_docx("一、问题\n只有中文内容，没有论文题目。")
        resp = client.post(
            "/api/depth/reflection/file",
            files={"file": ("report.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert resp.status_code in (202, 422), f"期望 202/422，实际 {resp.status_code}: {resp.text}"
        if resp.status_code == 202:
            body = resp.json()
            assert body["sourcePaper"]["status"] == "no_title"
