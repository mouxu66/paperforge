"""First-tier feature catalog endpoint tests.

Covers the endpoints flagged as "遗漏" (missing coverage) in the
PaperForge 功能清单与分类架构 document — the high-frequency endpoints
that regular users hit most:

  - POST /api/upload-batch      (批量 PDF 上传)
  - POST /api/arxiv/search      (arXiv 检索)
  - POST /api/arxiv/import      (arXiv 导入)
  - POST /api/ingest/url        (URL 直链收录)
  - GET  /api/papers/tags       (标签列表)
  - POST /api/papers/tags/rename (标签重命名)
  - DELETE /api/papers/tags/{tag} (标签删除)
  - POST /api/papers/batch-tag  (批量打标签)
  - POST /api/papers/merge      (重复论文合并)
  - POST /api/papers/batch-delete (批量删除)

These are HTTP contract + happy-path tests, not exhaustive business-logic
tests. External network calls (arXiv, Semantic Scholar) are mocked.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_api.models import Paper as PaperORM


# Reuse client/db_session fixtures from tests/conftest.py
def _seed_paper(db, pid: str, title: str = "Test Paper", tags: list[str] | None = None) -> PaperORM:
    """Insert a minimal paper row and return it."""
    paper = PaperORM(
        id=pid,
        title=title,
        authors=["Author A"],
        abstract="abstract text",
        category="all",
        tags=tags or [],
        year=2024,
        journal="",
        pdf_url="",
        citations=0,
        chunk_count=0,
        index_size=0,
        source="upload",
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


# ---------------------------------------------------------------------------
# Batch upload (POST /api/upload-batch)
# ---------------------------------------------------------------------------
class TestUploadBatch:
    """批量 PDF 上传端点。"""

    def test_upload_batch_empty_files_400(self, client):
        """空文件列表应返回 422 或 400。"""
        resp = client.post("/api/upload-batch", files=[])
        # FastAPI 对空 files 列表返回 422 (validation) 或 400
        assert resp.status_code in (400, 422)

    def test_upload_batch_with_dummy_pdf(self, client):
        """上传一个最小 PDF 字节流，应返回 UploadBatchResponse 结构。"""
        # 构造最小 PDF 字节（不足以真正解析，但端点会尝试处理）
        pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"
        resp = client.post(
            "/api/upload-batch",
            files=[("files", ("test.pdf", pdf_bytes, "application/pdf"))],
        )
        # 端点可能成功或解析失败，但不应 500
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "successCount" in data
        assert "failCount" in data
        assert isinstance(data["results"], list)

    def test_upload_batch_unsupported_file_type(self, client):
        """不支持的文件类型应计入 unsupportedCount。"""
        resp = client.post(
            "/api/upload-batch",
            files=[("files", ("notes.txt", b"hello world", "text/plain"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("unsupportedCount", 0) >= 1


# ---------------------------------------------------------------------------
# arXiv search (POST /api/arxiv/search) — needs mocking
# ---------------------------------------------------------------------------
class TestArxivSearch:
    """arXiv 检索端点。"""

    def test_arxiv_search_empty_keyword_400(self, client):
        """空关键词应返回 400。"""
        resp = client.post("/api/arxiv/search", json={"keyword": "", "maxResults": 5})
        assert resp.status_code in (400, 422)

    def test_arxiv_search_mocked_results(self, client):
        """mock search_arxiv 返回结果时，应返回 ArxivSearchResponse 结构。"""
        mock_papers = [
            {
                "id": "2401.00001",
                "title": "Mock Paper on LLMs",
                "authors": ["Alice", "Bob"],
                "year": 2024,
                "abstract": "A mock abstract.",
                "pdfUrl": "https://arxiv.org/pdf/2401.00001",
                "category": "cs.CL",
            }
        ]
        with patch("mock_api.routers.arxiv.search_arxiv", return_value=mock_papers):
            resp = client.post(
                "/api/arxiv/search",
                json={"keyword": "large language model", "maxResults": 5},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Mock Paper on LLMs"

    def test_arxiv_search_external_error_502(self, client):
        """search_arxiv 抛 RuntimeError 时应返回 502。"""
        with patch("mock_api.routers.arxiv.search_arxiv", side_effect=RuntimeError("arXiv down")):
            resp = client.post(
                "/api/arxiv/search",
                json={"keyword": "transformer", "maxResults": 3},
            )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# arXiv import (POST /api/arxiv/import)
# ---------------------------------------------------------------------------
class TestArxivImport:
    """arXiv 导入端点。"""

    def test_arxiv_import_empty_papers_400(self, client):
        """空论文列表应返回 400（未选择要导入的论文）。"""
        resp = client.post("/api/arxiv/import", json={"papers": []})
        assert resp.status_code == 400

    def test_arxiv_import_single_paper(self, client):
        """导入单篇 arXiv 论文应返回 ArxivImportResponse 结构。"""
        paper_payload = {
            "id": "2401.00002",
            "title": "Imported Paper",
            "authors": ["Carol"],
            "year": 2024,
            "abstract": "abstract",
            "pdfUrl": "https://arxiv.org/pdf/2401.00002",
            "source": "arxiv",
            "category": "arxiv",
            "tags": [],
        }
        resp = client.post("/api/arxiv/import", json={"papers": [paper_payload]})
        # 导入可能成功或因去重跳过，但不应 500
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "successCount" in data
        assert "failCount" in data
        assert isinstance(data["results"], list)


# ---------------------------------------------------------------------------
# Tag CRUD (GET /api/papers/tags, POST /api/papers/tags/rename,
#            DELETE /api/papers/tags/{tag}, POST /api/papers/batch-tag)
# ---------------------------------------------------------------------------
class TestTagCRUD:
    """标签 CRUD 端点。"""

    def test_list_tags_returns_list(self, client):
        """标签列表端点应返回数组（种子数据可能已带标签，不假设为空）。"""
        resp = client.get("/api/papers/tags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        # 仅做结构校验：每个元素含 name 和 count
        for tag in resp.json():
            assert "name" in tag
            assert "count" in tag

    def test_list_tags_after_batch_tag(self, client, db_session):
        """批量打标签后，标签列表应包含新标签。"""
        paper = _seed_paper(db_session, "tag-test-1", tags=[])
        # 批量打标签
        resp = client.post(
            "/api/papers/batch-tag",
            json={"paper_ids": [paper.id], "add_tags": ["machine-learning", "NLP"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["updated_count"] >= 1

        # 标签列表应包含新标签
        resp = client.get("/api/papers/tags")
        assert resp.status_code == 200
        tag_names = [t["name"] for t in resp.json()]
        assert "machine-learning" in tag_names
        assert "NLP" in tag_names

    def test_rename_tag(self, client, db_session):
        """重命名标签应更新标签列表。"""
        paper = _seed_paper(db_session, "rename-test-1", tags=["old-tag"])
        assert paper.tags == ["old-tag"]

        resp = client.post(
            "/api/papers/tags/rename",
            json={"old_name": "old-tag", "new_name": "new-tag"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["affected_count"] >= 1

        # 验证标签列表中 old-tag 消失，new-tag 出现
        resp = client.get("/api/papers/tags")
        tag_names = [t["name"] for t in resp.json()]
        assert "new-tag" in tag_names
        assert "old-tag" not in tag_names

    def test_delete_tag(self, client, db_session):
        """删除标签应从论文上移除该标签。"""
        paper = _seed_paper(db_session, "delete-tag-1", tags=["to-delete"])
        assert "to-delete" in paper.tags

        resp = client.delete("/api/papers/tags/to-delete")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # 验证标签列表中不再包含 to-delete
        resp = client.get("/api/papers/tags")
        tag_names = [t["name"] for t in resp.json()]
        assert "to-delete" not in tag_names

    def test_batch_tag_empty_paper_ids_422(self, client):
        """空论文 ID 列表应返回 422。"""
        resp = client.post(
            "/api/papers/batch-tag",
            json={"paper_ids": [], "add_tags": ["test"]},
        )
        assert resp.status_code == 422

    def test_batch_tag_remove_tag(self, client, db_session):
        """移除标签应生效。"""
        paper = _seed_paper(db_session, "remove-test-1", tags=["remove-me"])
        resp = client.post(
            "/api/papers/batch-tag",
            json={"paper_ids": [paper.id], "remove_tags": ["remove-me"]},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # 验证标签已被移除
        db_session.expire_all()
        refreshed = db_session.query(PaperORM).filter_by(id="remove-test-1").first()
        assert "remove-me" not in (refreshed.tags or [])


# ---------------------------------------------------------------------------
# Paper merge (POST /api/papers/merge)
# ---------------------------------------------------------------------------
class TestPaperMerge:
    """重复论文合并端点。"""

    def test_merge_nonexistent_papers_404(self, client):
        """合并不存在的论文应返回 404。"""
        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": "nonexistent-target",
                "source_ids": ["nonexistent-source"],
            },
        )
        assert resp.status_code == 404

    def test_merge_duplicate_papers(self, client, db_session):
        """合并标题相同的重复论文应成功。"""
        # 创建两篇标题相同的论文（构成重复组）
        p1 = _seed_paper(db_session, "merge-1", title="Duplicate Title", tags=["t1"])
        p2 = _seed_paper(db_session, "merge-2", title="Duplicate Title", tags=["t2"])

        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": p1.id,
                "source_ids": [p2.id],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["target_id"] == "merge-1"
        assert "merge-2" in data["deleted_ids"]

        # 验证源论文已被删除
        db_session.expire_all()
        assert db_session.query(PaperORM).filter_by(id="merge-2").first() is None
        assert db_session.query(PaperORM).filter_by(id="merge-1").first() is not None

    def test_merge_with_field_sources(self, client, db_session):
        """指定字段来源的合并应使用来源论文的字段值。"""
        p1 = _seed_paper(db_session, "merge-fs-1", title="Same Title", tags=["keep"])
        p2 = _seed_paper(db_session, "merge-fs-2", title="Same Title", tags=["from-source"])

        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": p1.id,
                "source_ids": [p2.id],
                "field_sources": {"tags": "merge-fs-2"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # 验证 target 的 tags 被替换为 source 的 tags
        db_session.expire_all()
        target = db_session.query(PaperORM).filter_by(id="merge-fs-1").first()
        assert target is not None
        assert "from-source" in (target.tags or [])


# ---------------------------------------------------------------------------
# Batch delete (POST /api/papers/batch-delete)
# ---------------------------------------------------------------------------
class TestBatchDelete:
    """批量删除端点。"""

    def test_batch_delete_empty_422(self, client):
        """空 ID 列表应返回 422。"""
        resp = client.post("/api/papers/batch-delete", json={"paper_ids": []})
        assert resp.status_code == 422

    def test_batch_delete_existing_papers(self, client, db_session):
        """批量删除存在的论文应成功。"""
        _seed_paper(db_session, "bd-1", title="To Delete 1")
        _seed_paper(db_session, "bd-2", title="To Delete 2")

        resp = client.post("/api/papers/batch-delete", json={"paper_ids": ["bd-1", "bd-2"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["deleted_count"] == 2
        assert set(data["failed_ids"]) == set()

        # 验证论文已被删除
        db_session.expire_all()
        assert db_session.query(PaperORM).filter_by(id="bd-1").first() is None
        assert db_session.query(PaperORM).filter_by(id="bd-2").first() is None

    def test_batch_delete_nonexistent_returns_failed_ids(self, client):
        """删除不存在的论文应返回 failed_ids。"""
        resp = client.post(
            "/api/papers/batch-delete",
            json={"paper_ids": ["nonexistent-xyz"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "nonexistent-xyz" in data["failed_ids"]


# ---------------------------------------------------------------------------
# URL ingest (POST /api/ingest/url)
# ---------------------------------------------------------------------------
class TestIngestUrl:
    """URL 直链收录端点。"""

    def test_ingest_url_empty_400(self, client):
        """空 URL 应返回 400。"""
        resp = client.post("/api/ingest/url", json={"url": ""})
        assert resp.status_code == 400

    def test_ingest_url_invalid_format_400(self, client):
        """非 URL 格式应返回 400。"""
        resp = client.post("/api/ingest/url", json={"url": "not-a-url"})
        assert resp.status_code == 400

    def test_ingest_url_with_metadata_only(self, client):
        """提供元数据时，应直接入库元数据而不依赖外部网络。"""
        # mock 掉 requests.get，避免真的去下载 PDF
        with patch("mock_api.routers.papers.requests.get") as mock_get:
            mock_get.return_value.status_code = 404  # 模拟 PDF 下载失败，走元数据分支
            resp = client.post(
                "/api/ingest/url",
                json={
                    "url": "https://example.com/paper/123",
                    "title": "Metadata Only Paper",
                    "authors": ["Test Author"],
                    "abstract": "Test abstract",
                    "year": 2024,
                    "journal": "Test Journal",
                    "source": "web_clipper",
                },
            )
            # 确保网络请求确实被 mock 拦截
            mock_get.assert_called_once()
        # 端点应返回 200/201 并成功创建论文
        assert resp.status_code in (200, 201)
        data = resp.json()
        # IngestUrlResponse 包含 paper_id / id 之一
        assert "paper_id" in data or "paperId" in data or "id" in data
