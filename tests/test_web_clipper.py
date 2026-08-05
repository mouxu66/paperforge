"""WP-4.1 / WP-4.1b Web Clipper 后端测试。

覆盖 /api/ingest/url 的以下场景：
- 空 URL / 非法 URL → 400
- PDF 下载失败但提供元数据 → 降级保存元数据（mode=metadata）
- 未提供元数据且 PDF 失败 → 400
- SSRF：不可信 pdf_url 被忽略
- 来源字段校验与默认值

覆盖 /api/ingest/raw 的以下场景：
- 有效 PDF 直传 → 解析入库（mode=pdf）+ 元数据覆盖
- 非有效 PDF 且无元数据 → 400
- 非有效 PDF 但有元数据 → 降级保存元数据（mode=metadata）
- 来源归一化（非法 source → web_clipper；知网 source 保留）
- 空 file → 400
- 元数据覆盖：扩展提供的 title/authors 覆盖 PDF 内提取值
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.pdf_parser import UploadedPaper
from mock_api.settings import reset_settings


@pytest.fixture
def client(monkeypatch):
    """每次测试创建新的 TestClient，并关闭全局鉴权。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
    # 禁用后台异步富化，避免测试结束时背景任务抛异常
    monkeypatch.setattr("mock_api.crud.papers._enrich_async", lambda paper_id: None)
    reset_settings()
    return TestClient(create_app())


class TestWebClipperIngestUrl:
    """Web Clipper /api/ingest/url 端点测试。"""

    def test_ingest_url_empty_400(self, client):
        resp = client.post("/api/ingest/url", json={"url": ""})
        assert resp.status_code == 400

    def test_ingest_url_invalid_url_400(self, client):
        resp = client.post("/api/ingest/url", json={"url": "not-a-url"})
        assert resp.status_code == 400

    def test_ingest_url_metadata_fallback_success(self, client):
        """PDF 下载失败但提供元数据时，应降级保存元数据。"""
        with (
            patch("mock_api.routers.papers.requests.get") as mock_get,
            patch("mock_api.crud.papers._enrich_async") as mock_enrich,
        ):
            mock_enrich.return_value = None
            # 模拟 PDF 下载失败
            mock_get.side_effect = requests.exceptions.ConnectionError("connection refused")
            resp = client.post(
                "/api/ingest/url",
                json={
                    "url": "https://arxiv.org/abs/2401.00001",
                    "title": "Test Paper Title",
                    "authors": ["Alice Author"],
                    "abstract": "This is a test abstract.",
                    "year": 2024,
                    "journal": "Test Journal",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["mode"] == "metadata"
        assert data["title"] == "Test Paper Title"
        assert data["paperId"]

    def test_ingest_url_no_metadata_and_pdf_fails_400(self, client):
        """PDF 下载失败且未提供元数据时，应返回 400。"""
        with patch("mock_api.routers.papers.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("connection refused")
            resp = client.post(
                "/api/ingest/url",
                json={"url": "https://arxiv.org/abs/2401.00001"},
            )
        assert resp.status_code == 400

    def test_ingest_url_rejects_untrusted_pdf_url(self, client):
        """不可信的 pdf_url 应被忽略，不会导致服务器请求任意地址。"""
        with patch("mock_api.routers.papers.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "should not be called for untrusted host"
            )
            resp = client.post(
                "/api/ingest/url",
                json={
                    "url": "https://arxiv.org/abs/2401.00001",
                    "pdf_url": "http://attacker.example.com/malicious.pdf",
                    "title": "Test",
                    "authors": ["A"],
                    "abstract": "abstract",
                },
            )
        # 由于 pdf_url 被忽略，会尝试 arxiv PDF；这里模拟失败，降级保存元数据
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "metadata"
        # 确保没有请求不可信主机
        for call in mock_get.call_args_list:
            assert "attacker.example.com" not in call.args[0]

    def test_ingest_url_source_validation(self, client):
        """非法 source 字段应被归一化为 web_clipper。"""
        with patch("mock_api.routers.papers.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("connection refused")
            resp = client.post(
                "/api/ingest/url",
                json={
                    "url": "https://example.com/paper",
                    "source": "malicious_source",
                    "title": "Test",
                    "authors": ["A"],
                    "abstract": "abstract",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "metadata"
        # paperId 前缀应为 web_clipper
        assert "web_clipper" in data["paperId"]


# ===========================================================================
# WP-4.1b: /api/ingest/raw 测试（浏览器登录态下载后直传 PDF 字节）
# ===========================================================================
class TestWebClipperIngestRaw:
    """Web Clipper /api/ingest/raw 端点测试。"""

    # 构造一个最小但合法的 PDF 字节流（魔数 + 少量内容），用于模拟浏览器下载的 PDF。
    # process_one_pdf 会被 mock 成直接成功，避免触发真实 pypdf/OCR。
    FAKE_PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"

    def _mock_process_success(self, paper_id="raw_test_paper_001", title="Raw Import Paper"):
        """返回一个成功的 process_one_pdf mock，便于多个用例复用。"""
        return UploadedPaper(
            id=paper_id,
            title=title,
            authors=[],
            year=0,
            abstract="",
            source="upload",
            success=True,
            ocr_status="done",
            is_scanned=False,
        )

    def test_ingest_raw_valid_pdf_success(self, client):
        """有效 PDF 直传 → process_one_pdf 成功 → mode=pdf。"""
        with (
            patch("mock_api.routers.papers.process_one_pdf") as mock_proc,
            patch("mock_api.crud.papers._enrich_async") as mock_enrich,
            patch("mock_api.crud.papers._review_async"),
        ):
            mock_enrich.return_value = None
            mock_proc.return_value = self._mock_process_success()
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("paper.pdf", self.FAKE_PDF_BYTES, "application/pdf")},
                data={
                    "url": "https://kns.cnki.net/kcms/detail/detail.aspx?dbcode=CJFD",
                    "title": "知网论文标题",
                    "authors": '["张三", "李四"]',
                    "abstract": "这是一篇知网论文的摘要。",
                    "year": "2024",
                    "journal": "计算机学报",
                    "source": "cnki",
                    "tags": '["cnki", "to-read"]',
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert data["mode"] == "pdf"
        assert data["paperId"] == "raw_test_paper_001"
        # title 应为扩展覆盖后的值（优先用扩展提供的）
        assert data["title"] == "知网论文标题"
        # process_one_pdf 被调用且传入了 PDF 字节
        mock_proc.assert_called_once()
        assert mock_proc.call_args.args[0] == self.FAKE_PDF_BYTES

    def test_ingest_raw_metadata_override_applied(self, client):
        """扩展提供的元数据应覆盖到 process_one_pdf 创建的论文记录上。

        process_one_pdf 被 mock，因此需要手动在 DB 中创建论文记录，
        以验证 _apply_clipper_overrides 是否正确覆盖字段。
        """
        import mock_api.crud as crud_mod
        from mock_api.database import SessionLocal
        from mock_api.models import Paper as PaperORM

        # 先在 DB 中创建论文记录（模拟 process_one_pdf 的入库效果）
        db = SessionLocal()
        try:
            p = PaperORM(
                id="raw_override_001",
                title="PDF 内提取的标题",
                authors=["PDF作者"],
                abstract="PDF摘要",
                category="upload",
                tags=[],
                year=2020,
                journal="",
                pdf_url="/uploads/raw_override_001.pdf",
                citations=0,
                chunk_count=0,
                index_size=0,
                source="upload",
            )
            db.add(p)
            db.commit()
        finally:
            db.close()

        with (
            patch("mock_api.routers.papers.process_one_pdf") as mock_proc,
            patch("mock_api.crud.papers._enrich_async") as mock_enrich,
            patch("mock_api.crud.papers._review_async"),
        ):
            mock_enrich.return_value = None
            mock_proc.return_value = self._mock_process_success(
                paper_id="raw_override_001", title="PDF 内提取的标题"
            )
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("paper.pdf", self.FAKE_PDF_BYTES, "application/pdf")},
                data={
                    "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                    "title": "页面提取的准确标题",
                    "authors": '["王五"]',
                    "year": "2023",
                    "journal": "软件学报",
                    "source": "cnki",
                    "tags": '["cnki", "to-read"]',
                },
            )
        assert resp.status_code == 200
        # 验证覆盖生效：查询论文确认字段被覆盖
        db = SessionLocal()
        try:
            paper = crud_mod.get_paper(db, "raw_override_001")
            assert paper is not None
            assert paper.title == "页面提取的准确标题"
            assert "王五" in paper.authors
            assert paper.year == 2023
            assert paper.journal == "软件学报"
            assert paper.source == "cnki"
            assert "cnki" in paper.tags
            assert "to-read" in paper.tags
        finally:
            db.close()

    def test_ingest_raw_empty_file_400(self, client):
        """空文件 → 400。"""
        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"url": "https://kns.cnki.net/x"},
        )
        assert resp.status_code == 400

    def test_ingest_raw_non_pdf_no_metadata_400(self, client):
        """非有效 PDF 且无元数据 → 400。"""
        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("notpdf.bin", b"NOT_A_PDF", "application/octet-stream")},
            data={"url": "https://kns.cnki.net/x"},
        )
        assert resp.status_code == 400

    def test_ingest_raw_non_pdf_with_metadata_fallback(self, client):
        """非有效 PDF 但有元数据 → 降级保存元数据（mode=metadata）。"""
        with patch("mock_api.crud.papers._enrich_async") as mock_enrich:
            mock_enrich.return_value = None
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("login_page.html", b"<html>login page</html>", "text/html")},
                data={
                    "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                    "title": "付费墙拦截的论文",
                    "authors": '["作者"]',
                    "abstract": "摘要",
                    "source": "cnki",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert data["mode"] == "metadata"
        assert data["title"] == "付费墙拦截的论文"
        assert "cnki" in data["paperId"]

    def test_ingest_raw_source_normalization(self, client):
        """非法 source 归一化为 web_clipper。"""
        with patch("mock_api.crud.papers._enrich_async") as mock_enrich:
            mock_enrich.return_value = None
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("fake.pdf", b"NOT_PDF", "application/pdf")},
                data={
                    "url": "https://example.com/paper",
                    "source": "malicious_source",
                    "title": "Test",
                    "authors": '["A"]',
                    "abstract": "abstract",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "metadata"
        assert "web_clipper" in data["paperId"]

    def test_ingest_raw_cnki_source_preserved(self, client):
        """cnki source 应被保留（不归一化为 web_clipper）。"""
        with patch("mock_api.crud.papers._enrich_async") as mock_enrich:
            mock_enrich.return_value = None
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("fake.pdf", b"NOT_PDF", "application/pdf")},
                data={
                    "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                    "source": "cnki",
                    "title": "知网论文",
                    "authors": '["作者"]',
                    "abstract": "摘要",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "metadata"
        # paperId 前缀含 cnki（来自 _create_metadata_only_paper 的 clip_{source}_...）
        assert "cnki" in data["paperId"]

    def test_ingest_raw_parse_failure_with_metadata_fallback(self, client):
        """有效 PDF 魔数但 process_one_pdf 失败 → 有元数据时降级保存元数据。"""
        with (
            patch("mock_api.routers.papers.process_one_pdf") as mock_proc,
            patch("mock_api.crud.papers._enrich_async") as mock_enrich,
        ):
            mock_enrich.return_value = None
            mock_proc.return_value = UploadedPaper(
                id="",
                title="broken.pdf",
                success=False,
                error="PDF 内容损坏",
            )
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("broken.pdf", self.FAKE_PDF_BYTES, "application/pdf")},
                data={
                    "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                    "title": "损坏的知网论文",
                    "authors": '["作者"]',
                    "abstract": "摘要",
                    "source": "cnki",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert data["mode"] == "metadata"
        assert data["title"] == "损坏的知网论文"

    def test_ingest_raw_parse_failure_no_metadata_400(self, client):
        """有效 PDF 魔数但解析失败且无元数据 → 400。"""
        with patch("mock_api.routers.papers.process_one_pdf") as mock_proc:
            mock_proc.return_value = UploadedPaper(
                id="", title="broken.pdf", success=False, error="PDF 内容损坏"
            )
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("broken.pdf", self.FAKE_PDF_BYTES, "application/pdf")},
                data={"url": "https://kns.cnki.net/kcms/detail/detail.aspx"},
            )
        assert resp.status_code == 400

    def test_ingest_raw_malformed_authors_json_handled(self, client):
        """authors 字段非法 JSON 应被优雅处理为空列表，不报错。"""
        with (
            patch("mock_api.routers.papers.process_one_pdf") as mock_proc,
            patch("mock_api.crud.papers._enrich_async") as mock_enrich,
            patch("mock_api.crud.papers._review_async"),
        ):
            mock_enrich.return_value = None
            mock_proc.return_value = self._mock_process_success(paper_id="raw_badjson_001")
            resp = client.post(
                "/api/ingest/raw",
                files={"file": ("paper.pdf", self.FAKE_PDF_BYTES, "application/pdf")},
                data={
                    "url": "https://kns.cnki.net/x",
                    "title": "Bad JSON Paper",
                    "authors": "not-valid-json",
                    "tags": "also-bad",
                    "source": "cnki",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["mode"] == "pdf"
        # title 优先用扩展提供的值（authors/tags 非法 JSON 不影响 title 覆盖）
        assert data["title"] == "Bad JSON Paper"

    def test_ingest_raw_oversized_file_413(self, client):
        """超过大小限制的文件 → 413。"""
        from mock_api.routers.papers import MAX_RAW_PDF_SIZE

        big = b"%PDF-" + b"0" * (MAX_RAW_PDF_SIZE + 1)
        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("big.pdf", big, "application/pdf")},
            data={"url": "https://kns.cnki.net/x", "title": "Big", "source": "cnki"},
        )
        assert resp.status_code == 413


# ===========================================================================
# WP-4.1b 集成测试：真实最小 PDF 走完整 process_one_pdf 路径 + FTS5 索引同步验证
# ===========================================================================
class TestWebClipperIngestRawIntegration:
    """集成测试：用真实最小 PDF 走完整 process_one_pdf 路径，验证 FTS5 索引同步。

    与 TestWebClipperIngestRaw 的区别：不 mock process_one_pdf，让真实 PDF 解析、
    全文提取、分块、入库、FTS5 同步全链路执行，验证端到端正确性。

    覆盖点：
    - 真实 PDF（含 %PDF- 魔数 + pypdf 可提取文本）→ process_one_pdf 全链路
    - 论文入库后 full_text / chunk_count / index_size 非空
    - FTS5 paper_fts 索引行创建 + full_text 同步
    - FTS5 MATCH 查询能命中全文关键词
    - _apply_clipper_overrides 覆盖元数据后 FTS5 行同步更新
    - 覆盖后的 title/tags 关键词可通过 FTS5 MATCH 检索
    """

    @staticmethod
    def _build_real_pdf(text: str) -> bytes:
        """构造一个真实可解析的最小 PDF（含 %PDF- 魔数 + pypdf 可提取文本）。

        复用 test_reflection_file_upload.py 的 _build_minimal_pdf_with_text 模式：
        在 /Contents stream 中嵌入 BT/Tj/ET 文本算子，pypdf 的 extract_text() 可提取。
        """
        import io

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

    @pytest.fixture(autouse=True)
    def _setup_fts5_and_bg_mocks(self, monkeypatch):
        """每个集成测试前：创建 FTS5 虚拟表 + mock 后台任务。

        conftest 的 Base.metadata.create_all 不含 FTS5 虚拟表（虚拟表非 ORM 声明），
        且 test_web_clipper.py 的 client fixture 不用 ``with TestClient`` 故不触发
        lifespan / init_db / _ensure_fts5。需在此手动创建 paper_fts 表。

        先 DROP TABLE IF EXISTS 确保 clean slate：_reset_in_memory_db 的
        metadata.drop_all 不会清除虚拟表，残留数据会污染跨测试断言。
        """
        from sqlalchemy import text as sql_text

        from mock_api.database import SessionLocal

        db = SessionLocal()
        try:
            db.execute(sql_text("DROP TABLE IF EXISTS paper_fts"))
            db.execute(
                sql_text(
                    "CREATE VIRTUAL TABLE paper_fts USING fts5("
                    "paper_id UNINDEXED, title, abstract, authors, full_text, tags"
                    ")"
                )
            )
            db.commit()
        finally:
            db.close()

        # mock 后台任务：process_one_pdf → create_paper_from_upload 会触发
        # _enrich_async（Semantic Scholar）和 _review_async（深度审稿），
        # 两者均 spawn 后台线程。测试环境无 LLM / 无网络，后台线程虽静默失败
        # 但可能在 teardown 时产生 warning。mock 掉确保干净。
        monkeypatch.setattr("mock_api.crud.papers._enrich_async", lambda paper_id: None)
        monkeypatch.setattr("mock_api.crud.papers._review_async", lambda paper_id: None)
        monkeypatch.setattr(
            "mock_api.crud.papers._reflection_review_async", lambda paper_id: None
        )

    def test_ingest_raw_real_pdf_fts5_synced(self, client):
        """真实 PDF 直传 → process_one_pdf 全链路 → FTS5 索引行创建 + MATCH 可检索。

        验证链路：
        1. process_one_pdf 用 pypdf 提取全文 → full_text 非空
        2. split_into_chunks 分块 → chunk_count / index_size 非零
        3. create_paper_from_upload 入库 + INSERT OR REPLACE INTO paper_fts
        4. FTS5 MATCH 'transformer' 命中该论文
        """
        from sqlalchemy import text as sql_text

        from mock_api.database import SessionLocal
        from mock_api.models import Paper as PaperORM
        from mock_api.pdf_parser import extract_full_text

        pdf_bytes = self._build_real_pdf(
            "Deep learning transformer model for language understanding"
        )
        # 前置校验：测试 PDF 必须能被 pypdf 提取文本，否则 full_text 为空、
        # FTS5 full_text 列为空，后续 MATCH 断言无意义。
        extracted = extract_full_text(pdf_bytes)
        assert extracted, (
            "测试 PDF 无法被 pypdf 提取文本——请检查 pypdf 是否正确安装。"
            f" extract_full_text 返回: {extracted!r}"
        )

        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("integration_test.pdf", pdf_bytes, "application/pdf")},
            data={
                "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                "source": "cnki",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["mode"] == "pdf"
        paper_id = data["paperId"]

        db = SessionLocal()
        try:
            # 1. 论文已入库，full_text / chunk_count / index_size 非空
            paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
            assert paper is not None, "论文未入库"
            assert paper.full_text, "full_text 为空——pypdf 全文提取失败"
            assert "transformer" in paper.full_text.lower(), (
                f"full_text 不含 'transformer': {paper.full_text!r}"
            )
            assert paper.chunk_count > 0, "chunk_count 为 0——分块管道未执行"
            assert paper.index_size > 0, "index_size 为 0"

            # 2. FTS5 索引行存在且 full_text 同步
            fts_row = db.execute(
                sql_text(
                    "SELECT paper_id, title, full_text FROM paper_fts "
                    "WHERE paper_id = :pid"
                ),
                {"pid": paper_id},
            ).first()
            assert fts_row is not None, "FTS5 索引行未创建——create_paper_from_upload FTS5 同步失败"
            assert fts_row[2], "FTS5 full_text 列为空"

            # 3. FTS5 MATCH 查询命中全文关键词
            match = db.execute(
                sql_text(
                    "SELECT paper_id FROM paper_fts WHERE paper_fts MATCH :kw"
                ),
                {"kw": "transformer"},
            ).all()
            matched_ids = [r[0] for r in match]
            assert paper_id in matched_ids, (
                f"FTS5 未命中关键词 'transformer'，命中: {matched_ids}"
            )
        finally:
            db.close()

    def test_ingest_raw_overrides_update_fts5(self, client):
        """扩展覆盖元数据后 FTS5 索引应反映覆盖后的 title/abstract/authors/tags。

        验证链路：
        1. process_one_pdf 入库 → create_paper_from_upload 写入初始 FTS5 行（PDF 提取值）
        2. _apply_clipper_overrides 覆盖 title/authors/abstract/tags → 写入新 FTS5 行
        3. papers 表行反映覆盖后的值（可靠验证源）
        4. 覆盖后的 title/tags 关键词可通过 FTS5 MATCH 检索

        注意：FTS5 的 INSERT OR REPLACE 不按 paper_id 去重（rowid 自动递增、
        paper_id 仅有 UNINDEXED 非 UNIQUE），每次写入会新增一行而非替换。
        因此用 papers 表验证覆盖值，用 FTS5 MATCH（DISTINCT paper_id）验证可检索性。
        """
        from sqlalchemy import text as sql_text

        from mock_api.database import SessionLocal
        from mock_api.models import Paper as PaperORM

        pdf_bytes = self._build_real_pdf("Machine learning neural networks overview")
        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("override_fts.pdf", pdf_bytes, "application/pdf")},
            data={
                "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                "title": "Overridden Title Quantum Survey",
                "authors": '["Alice", "Bob"]',
                "abstract": "Overridden abstract about neural architectures",
                "source": "cnki",
                "tags": '["cnki", "survey"]',
            },
        )
        assert resp.status_code == 200, resp.text
        paper_id = resp.json()["paperId"]

        db = SessionLocal()
        try:
            # 1. papers 表验证覆盖值（可靠——_apply_clipper_overrides 直接修改 ORM 对象）
            paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
            assert paper is not None, "论文未入库"
            assert paper.title == "Overridden Title Quantum Survey", (
                f"title 未覆盖: {paper.title!r}"
            )
            assert "neural architectures" in (paper.abstract or ""), (
                f"abstract 未覆盖: {paper.abstract!r}"
            )
            assert "Alice" in (paper.authors or []), f"authors 未覆盖: {paper.authors!r}"
            assert "cnki" in (paper.tags or []) and "survey" in (paper.tags or []), (
                f"tags 未覆盖: {paper.tags!r}"
            )

            # 2. FTS5 索引存在该论文的行（可能多行——INSERT OR REPLACE 不去重）
            fts_count = db.execute(
                sql_text("SELECT count(*) FROM paper_fts WHERE paper_id = :pid"),
                {"pid": paper_id},
            ).scalar()
            assert fts_count > 0, "FTS5 索引行未创建"

            # 3. 覆盖后的关键词可通过 FTS5 MATCH 检索（DISTINCT 去重）
            match_title = db.execute(
                sql_text(
                    "SELECT DISTINCT paper_id FROM paper_fts WHERE paper_fts MATCH :kw"
                ),
                {"kw": "Quantum"},
            ).all()
            assert paper_id in [r[0] for r in match_title], (
                "FTS5 未命中覆盖后的 title 关键词 'Quantum'"
            )

            match_tag = db.execute(
                sql_text(
                    "SELECT DISTINCT paper_id FROM paper_fts WHERE paper_fts MATCH :kw"
                ),
                {"kw": "survey"},
            ).all()
            assert paper_id in [r[0] for r in match_tag], (
                "FTS5 未命中覆盖后的 tag 关键词 'survey'"
            )
        finally:
            db.close()

    def test_ingest_raw_full_text_searchable_via_fts5(self, client):
        """导入后，全文中的独特关键词可通过 FTS5 MATCH 检索到该论文（唯一命中）。

        用自造独特关键词 ``quantumentanglement`` 确保不与种子数据冲突，
        验证 FTS5 full_text 列被正确写入且可被 MATCH 查询命中。

        注意：source='cnki' 会触发 _apply_clipper_overrides（cnki != upload），
        导致 FTS5 有多行（INSERT OR REPLACE 不按 paper_id 去重），
        因此用 DISTINCT paper_id 去重后断言唯一命中。
        """
        from sqlalchemy import text as sql_text

        from mock_api.database import SessionLocal

        unique_keyword = "quantumentanglement"
        pdf_bytes = self._build_real_pdf(
            f"Research on {unique_keyword} and quantum computing applications"
        )
        resp = client.post(
            "/api/ingest/raw",
            files={"file": ("fts_searchable.pdf", pdf_bytes, "application/pdf")},
            data={
                "url": "https://kns.cnki.net/kcms/detail/detail.aspx",
                "source": "cnki",
            },
        )
        assert resp.status_code == 200, resp.text
        paper_id = resp.json()["paperId"]

        db = SessionLocal()
        try:
            # 独特关键词应只命中本论文（DISTINCT 去重，因 FTS5 可能有重复行）
            match = db.execute(
                sql_text(
                    "SELECT DISTINCT paper_id FROM paper_fts WHERE paper_fts MATCH :kw"
                ),
                {"kw": unique_keyword},
            ).all()
            matched_ids = [r[0] for r in match]
            assert paper_id in matched_ids, (
                f"FTS5 未命中独特关键词 '{unique_keyword}'，命中: {matched_ids}"
            )
            # 自造关键词应唯一命中（不与种子数据冲突）
            assert len(matched_ids) == 1, (
                f"独特关键词命中多篇论文（预期仅 1 篇）: {matched_ids}"
            )
        finally:
            db.close()
