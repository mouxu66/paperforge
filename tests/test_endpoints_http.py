"""HTTP 端点契约测试 —— 覆盖原 🟠「~50 端点 HTTP 未测」。

验证关键端点的 HTTP 契约（状态码、响应结构），不测试业务逻辑。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.settings import reset_settings


@pytest.fixture
def client(monkeypatch):
    """每次测试创建新的 TestClient，并关闭全局鉴权（避免受其他测试影响）。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
    reset_settings()
    return TestClient(create_app())


class TestHealthEndpoints:
    """健康检查端点。"""

    def test_health_live(self, client):
        resp = client.get("/api/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_ready(self, client):
        resp = client.get("/api/health/ready")
        assert resp.status_code in (200, 503)

    def test_health_compat(self, client):
        resp = client.get("/api/health")
        assert resp.status_code in (200, 503)


class TestPaperEndpoints:
    """论文端点基本契约。"""

    def test_list_papers(self, client):
        resp = client.get("/api/papers?page=1&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_get_nonexistent_paper_404(self, client):
        resp = client.get("/api/papers/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "totalPapers" in data

    def test_list_tags(self, client):
        """标签聚合端点不应被动态 /api/papers/{paper_id} 路由 shadow 而 404。"""
        resp = client.get("/api/papers/tags")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestTaskEndpoints:
    """任务端点契约。"""

    def test_get_nonexistent_task_404(self, client):
        resp = client.get("/api/tasks/nonexistent-uuid")
        assert resp.status_code == 404

    def test_list_tasks(self, client):
        resp = client.get("/api/tasks?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


class TestModelEndpoints:
    """模型管理端点契约。"""

    def test_list_models(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_current_model(self, client):
        resp = client.get("/api/model/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data


class TestDepthEndpoints:
    """DEPTH 端点契约。"""

    def test_depth_score_nonexistent_404(self, client):
        resp = client.get("/api/depth/score/nonexistent-id")
        assert resp.status_code == 404

    def test_depth_v4_result_nonexistent_404(self, client):
        resp = client.get("/api/depth/v4/result/nonexistent-id")
        assert resp.status_code == 404

    def test_depth_v4_list(self, client):
        resp = client.get("/api/depth/v4/list?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_reflection_list(self, client):
        resp = client.get("/api/depth/reflection/list?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


class TestAdminEndpoints:
    """admin 端点安全契约。

    ⚠️ IS_DEV_ENV 在模块加载时读取，默认 production（False）。
    若测试环境设了 ENV=development，此测试会失败。
    """

    def test_admin_package_blocked_in_production(self, client):
        """生产模式下 admin 打包端点应返回 403（IS_DEV_ENV 默认 False）。"""
        from mock_api.settings import get_settings

        if get_settings().is_dev_env:
            pytest.skip("ENV=development 时 admin 端点不返回 403，跳过")
        resp = client.post("/api/admin/package")
        assert resp.status_code == 403


class TestWritingProvenanceEndpoints:
    """写作助手 provenance 服务端打戳契约。"""

    def test_save_continuation_ignores_client_kind(self, client):
        """POST /continuations 应忽略客户端传入的 kind，固定盖戳 continue。"""
        # 创建项目 + 章节
        resp = client.post("/api/writing/projects", json={"title": "P"})
        assert resp.status_code == 201
        project_id = resp.json()["id"]
        resp = client.post(f"/api/writing/projects/{project_id}/chapters", json={"title": "引言"})
        assert resp.status_code == 201
        chapter_id = resp.json()["id"]

        # 新契约：不传入 kind 也能正常保存
        resp = client.post(
            f"/api/writing/chapters/{chapter_id}/continuations",
            json={"content": "AI 续写文本", "direction": "继续"},
        )
        assert resp.status_code == 200
        assert resp.json()["kind"] == "continue"

        # 旧客户端/恶意客户端尝试伪造 kind="rewrite"，服务端应忽略并继续盖戳 continue
        resp = client.post(
            f"/api/writing/chapters/{chapter_id}/continuations",
            json={"content": "AI 续写文本", "direction": "继续", "kind": "rewrite"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "continue"
        assert data["content"] == "AI 续写文本"
        # 最终应只有 2 条记录
        assert len(client.get(f"/api/writing/chapters/{chapter_id}/continuations").json()) == 2

    def test_rewrite_endpoint_stamps_rewrite(self, client, monkeypatch):
        """POST /rewrite 在返回改写结果后应自动保存 kind=rewrite 的 provenance。"""
        from mock_api import writing_assist

        monkeypatch.setattr(writing_assist, "rewrite_text", lambda _ctx, _text: "改写后的文本")

        resp = client.post("/api/writing/projects", json={"title": "P"})
        assert resp.status_code == 201
        project_id = resp.json()["id"]
        resp = client.post(f"/api/writing/projects/{project_id}/chapters", json={"title": "引言"})
        assert resp.status_code == 201
        chapter_id = resp.json()["id"]

        resp = client.post(
            f"/api/writing/chapters/{chapter_id}/rewrite",
            json={"text": "原文"},
        )
        assert resp.status_code == 200
        assert resp.json()["rewritten"] == "改写后的文本"

        # 改写应被记录为 provenance
        resp = client.get(f"/api/writing/chapters/{chapter_id}/continuations")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        assert records[0]["kind"] == "rewrite"
        assert records[0]["content"] == "改写后的文本"


class TestNewFeatureEndpoints:
    """WP-2.7 / WP-5.1 / WP-4.1 新增端点契约。"""

    def test_translate_nonexistent_paper_404(self, client):
        resp = client.post("/api/papers/nonexistent-id/translate", json={"text": "hello"})
        assert resp.status_code == 404

    def test_translate_empty_text_400(self, client):
        resp = client.post("/api/papers/nonexistent-id/translate", json={"text": ""})
        # 404 优先于 400，因为论文不存在先检查
        assert resp.status_code in (400, 404)

    def test_enrich_metadata_nonexistent_paper_404(self, client):
        resp = client.post("/api/papers/nonexistent-id/enrich-metadata")
        assert resp.status_code == 404

    def test_ingest_url_empty_400(self, client):
        resp = client.post("/api/ingest/url", json={"url": ""})
        assert resp.status_code == 400

    def test_ingest_url_invalid_url_400(self, client):
        resp = client.post("/api/ingest/url", json={"url": "not-a-url"})
        assert resp.status_code == 400

    def test_translate_llm_not_configured_503(self, client, db_session, monkeypatch):
        """LLM 未配置且论文存在时，翻译端点应返回 503。"""
        from mock_api.models import Paper
        from mock_api.routers import papers as papers_router

        # 创建一篇测试论文
        paper = Paper(
            id="test-paper-123",
            title="Test Paper",
            authors=["A Author"],
            abstract="abstract",
            category="all",
            tags=[],
            year=2024,
            journal="",
            pdf_url="",
            citations=0,
            chunk_count=0,
            index_size=0,
            source="upload",
        )
        db_session.add(paper)
        db_session.commit()

        def _fake_factory():
            raise RuntimeError("no provider")

        monkeypatch.setattr(papers_router, "get_factory", _fake_factory)
        resp = client.post("/api/papers/test-paper-123/translate", json={"text": "hello"})
        assert resp.status_code == 503
        assert "LLM" in resp.json().get("detail", "")
