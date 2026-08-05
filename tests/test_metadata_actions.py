"""Tests for WP-5.1 metadata action endpoints."""


import pytest
from fastapi.testclient import TestClient
from mock_api.models import Paper
from sqlalchemy.orm import Session


@pytest.fixture
def sample_paper(db_session: Session) -> Paper:
    paper = Paper(
        id="p1",
        title="Test Paper",
        authors=["Alice", "Bob"],
        year=2024,
        abstract="Abstract text",
        category="arxiv",
        tags=["ai"],
        citations=10,
        chunk_count=5,
        index_size=1024,
        pdf_url="",
        source="arxiv",
    )
    db_session.add(paper)
    db_session.commit()
    return paper


class TestExtractDoi:
    def test_extract_doi_from_full_text(
        self, client: TestClient, db_session: Session, sample_paper: Paper
    ) -> None:
        sample_paper.full_text = "This paper has DOI 10.1234/example.123 in the text."
        db_session.add(sample_paper)
        db_session.commit()

        resp = client.post(f"/api/papers/{sample_paper.id}/extract-doi")
        assert resp.status_code == 200
        data = resp.json()
        assert data["doi"] == "10.1234/example.123"

    def test_extract_doi_not_found(self, client: TestClient, sample_paper: Paper) -> None:
        resp = client.post(f"/api/papers/{sample_paper.id}/extract-doi")
        assert resp.status_code == 404

    def test_extract_doi_paper_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/papers/nonexistent/extract-doi")
        assert resp.status_code == 404


class TestRenamePdf:
    def test_rename_pdf_no_local_file(self, client: TestClient, sample_paper: Paper) -> None:
        resp = client.post(f"/api/papers/{sample_paper.id}/rename-pdf")
        assert resp.status_code == 404

    def test_rename_pdf_paper_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/papers/nonexistent/rename-pdf")
        assert resp.status_code == 404

    def test_rename_pdf_success(self, client: TestClient, sample_paper: Paper) -> None:
        from mock_api.pdf_parser import _get_uploads_dir

        uploads_dir = _get_uploads_dir()
        pdf_path = uploads_dir / f"{sample_paper.id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")

        try:
            resp = client.post(f"/api/papers/{sample_paper.id}/rename-pdf")
            assert resp.status_code == 200
            data = resp.json()
            assert "2024 - Test Paper" in data["pdfUrl"]
        finally:
            if pdf_path.exists():
                pdf_path.unlink()
            # Clean up any renamed file that may have been created
            for renamed in uploads_dir.glob("2024 - Test Paper*.pdf"):
                renamed.unlink(missing_ok=True)


class TestExtractAnnotations:
    def test_extract_annotations_no_local_file(
        self, client: TestClient, sample_paper: Paper
    ) -> None:
        resp = client.post(f"/api/papers/{sample_paper.id}/extract-annotations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["annotations"] == []

    def test_extract_annotations_paper_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/papers/nonexistent/extract-annotations")
        assert resp.status_code == 404

    def test_extract_annotations_with_real_pdf(
        self, client: TestClient, sample_paper: Paper
    ) -> None:
        """用 PyMuPDF 创建带高亮批注的 PDF，调用接口后断言持久化结果。"""
        import fitz  # PyMuPDF
        from mock_api.pdf_parser import _get_uploads_dir

        uploads_dir = _get_uploads_dir()
        pdf_path = uploads_dir / f"{sample_paper.id}.pdf"

        # 创建带高亮批注的 PDF
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "This is a test sentence for annotation extraction.")
        # 添加高亮批注
        highlight_rect = fitz.Rect(72, 70, 300, 90)
        annot = page.add_highlight_annot(highlight_rect)
        annot.set_info(content="test note")
        doc.save(str(pdf_path))
        doc.close()

        try:
            resp = client.post(f"/api/papers/{sample_paper.id}/extract-annotations")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert len(data["annotations"]) == 1
            annotation = data["annotations"][0]
            assert annotation["paperId"] == sample_paper.id
            assert annotation["page"] == 1
            assert annotation["note"] == "test note"
            assert annotation["color"].startswith("#")
            assert len(annotation["quadpoints"]) > 0

            # 幂等性：再次调用应仍只有 1 条
            resp2 = client.post(f"/api/papers/{sample_paper.id}/extract-annotations")
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["count"] == 1
            assert len(data2["annotations"]) == 1
        finally:
            if pdf_path.exists():
                pdf_path.unlink(missing_ok=True)
