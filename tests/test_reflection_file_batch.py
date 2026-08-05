"""Tests for batch reflection upload endpoint (POST /api/depth/reflection/files).

Test matrix:
- test_batch_multi_txt_success: 3 valid .txt files → 3 accepted
- test_batch_mixed_ext_failure: 1 .txt + 1 .exe → 1 accepted + 1 failed
- test_batch_oversize_400: 21 files → 400 (exceeds MAX_BATCH_SIZE)
- test_batch_empty_400: 0 files → 400
- test_batch_preserves_single_file_contract: 1 .txt → equivalent to /file endpoint

The test follows the same TestClient + fixture pattern as test_reflection_file_upload.py.
"""
import io

import pytest
from fastapi.testclient import TestClient
from mock_api.main import app

client = TestClient(app)


def _build_docx_with_title(title: str) -> bytes:
    """Build a minimal docx with an explicit 论文题目 header."""
    try:
        from docx import Document
    except ImportError:
        pytest.skip("python-docx not installed")
    doc = Document()
    doc.add_paragraph(f"论文题目：{title}")
    doc.add_paragraph("一、问题")
    doc.add_paragraph("二、技术")
    doc.add_paragraph("三、实验")
    doc.add_paragraph("四、感想")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _make_txt(name: str, body: str = "一份关于 Transformer 架构的读后感。") -> tuple:
    """Build a (filename, bytes, content_type) tuple for a TXT file upload."""
    return (name, body.encode("utf-8"), "text/plain")


def _make_docx_minimal(name: str = "report.docx") -> tuple:
    """Build a minimal valid .docx file.

    python-docx is heavy; use a tiny placeholder. If python-docx is unavailable
    the test will be skipped (the production code path also raises 400/422).
    """
    try:
        from docx import Document
    except ImportError:
        pytest.skip("python-docx not installed")
    doc = Document()
    doc.add_paragraph("一份关于 Transformer 架构的读后感。" * 5)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return (name, buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_batch_multi_txt_success():
    """3 valid .txt files → all 3 accepted with paperId + taskId."""
    files = [
        _make_txt("report_a.txt", "感悟 A：关于 attention 机制。"),
        _make_txt("report_b.txt", "感悟 B：关于 multi-head 结构。"),
        _make_txt("report_c.txt", "感悟 C：关于 positional encoding。"),
    ]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 202, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["total"] == 3
    assert body["submitted"] == 3
    assert body["failed"] == 0
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["status"] == "accepted"
        assert item["paperId"], f"missing paperId in {item}"
        assert item["taskId"], f"missing taskId in {item}"
        assert item["error"] == ""
        assert item["bytes"] > 0
    # Filenames preserved
    filenames = {it["filename"] for it in body["items"]}
    assert filenames == {"report_a.txt", "report_b.txt", "report_c.txt"}


def test_batch_mixed_ext_failure():
    """1 .txt + 1 .exe → 1 accepted + 1 failed (unsupported extension)."""
    files = [
        _make_txt("ok.txt", "正常文件内容：关于 RNN 的理解。"),
        ("bad.exe", b"random binary content", "application/octet-stream"),
    ]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 202, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["total"] == 2
    assert body["submitted"] == 1
    assert body["failed"] == 1
    by_filename = {it["filename"]: it for it in body["items"]}
    assert by_filename["ok.txt"]["status"] == "accepted"
    assert by_filename["bad.exe"]["status"] == "failed"
    assert "不支持" in by_filename["bad.exe"]["error"] or "不支持" in by_filename["bad.exe"]["error"]


def test_batch_oversize_400():
    """21 files → 400 (exceeds MAX_BATCH_SIZE=20)."""
    files = [_make_txt(f"r{i}.txt", f"content {i}") for i in range(21)]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 400
    assert "20" in resp.json()["detail"]


def test_batch_empty_422():
    """0 files → 422 (FastAPI rejects missing required File field before endpoint runs).

    注意：FastAPI 对 `files: list[UploadFile] = File(...)` 缺少时返回 422（请求验证失败），
    而不是我们 endpoint 中 400 的兜底。这是 FastAPI 的标准行为；调用方应使用
    TypeScript 端提前校验避免触发。"""
    resp = client.post("/api/depth/reflection/files", files=[])
    assert resp.status_code == 422


def test_batch_single_file_works():
    """1 .txt → equivalent to /file endpoint behavior (1 accepted item)."""
    files = [_make_txt("single.txt", "单文件批量上传测试。")]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["total"] == 1
    assert body["submitted"] == 1
    assert body["failed"] == 0
    assert body["items"][0]["status"] == "accepted"
    assert body["items"][0]["paperId"]
    assert body["items"][0]["taskId"]


def test_batch_persists_papers_to_db():
    """Accepted items should result in papers inserted into DB with category='report'."""
    from mock_api.database import SessionLocal
    from mock_api.models import Paper as PaperORM

    files = [_make_txt("persist_test.txt", "持久化测试：关于 self-attention 的数学推导。")]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 202
    body = resp.json()
    paper_id = body["items"][0]["paperId"]

    db = SessionLocal()
    try:
        p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        assert p is not None, f"paper {paper_id} not found in DB"
        assert p.category == "report"
        assert p.source == "upload"
        assert "self-attention" in (p.full_text or "")
    finally:
        db.close()


def test_batch_dedup_same_title_and_authors():
    """两个文件使用共享 title + authors_csv → 触发去重。

    注意：crud._find_duplicate_by_title_authors 要求非空的作者交集（不仅是标题），
    所以必须同时传 authors_csv 才会触发去重。
    """
    shared_title = "共享标题去重测试"
    shared_authors = "Test Author, Co-Author"
    same_content = "完全相同的内容。This is identical content for dedup test."
    files = [
        _make_txt("dup1.txt", same_content),
        _make_txt("dup2.txt", same_content),
    ]
    # 通过共享 title + authors_csv 让两个文件都映射到相同的 final_title + final_authors
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
        data={"title": shared_title, "authors_csv": shared_authors},
    )
    assert resp.status_code == 202
    body = resp.json()
    # 两个文件都应该产生相同的 paper_id（去重命中）
    paper_ids = {it["paperId"] for it in body["items"] if it["paperId"]}
    assert len(paper_ids) == 1, f"expected 1 unique paperId after dedup, got {paper_ids}"
    assert body["total"] == 2
    assert body["submitted"] == 2  # 两者都被接受（入库同一个 paper，更新现有记录）


def test_batch_docx_returns_source_paper(monkeypatch):
    """批量 docx 上传时每个文件独立返回 sourcePaper 状态。"""
    import mock_api.routers.reflection as reflection_router

    fake_paper_id = "arxiv_test_123"

    def _fake_resolve(title, author=None, db=None):
        return {"status": "exists", "paper_id": fake_paper_id, "title": title}

    monkeypatch.setattr(reflection_router, "resolve_and_ingest", _fake_resolve)

    docx_bytes = _build_docx_with_title("Remote laboratories for education")
    files = [("report.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code == 202, f"期望 202，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["total"] == 1
    assert body["submitted"] == 1
    item = body["items"][0]
    assert item["status"] == "accepted"
    assert item["sourcePaper"]["status"] == "exists"
    assert item["sourcePaper"]["paperId"] == fake_paper_id


def test_batch_docx_no_title_returns_no_title(monkeypatch):
    """docx 未解析到题目时返回 no_title，不调用 resolver。"""
    import mock_api.routers.reflection as reflection_router

    called = False

    def _fake_resolve(title, author=None, db=None):
        nonlocal called
        called = True
        return {"status": "no_match"}

    monkeypatch.setattr(reflection_router, "resolve_and_ingest", _fake_resolve)

    try:
        from docx import Document
    except ImportError:
        pytest.skip("python-docx not installed")
    doc = Document()
    doc.add_paragraph("一、问题")
    doc.add_paragraph("没有论文题目，只有中文内容。")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    files = [("report.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")]
    resp = client.post(
        "/api/depth/reflection/files",
        files=[("files", f) for f in files],
    )
    assert resp.status_code in (202, 422), f"期望 202/422，实际 {resp.status_code}: {resp.text}"
    if resp.status_code == 202:
        item = resp.json()["items"][0]
        assert item["sourcePaper"]["status"] == "no_title"
    assert not called
