"""安全加固测试 —— 覆盖 SSRF、DNS rebinding、全局鉴权中间件。

注意：conftest.py 默认设置 PAPERFORGE_AUTH_ENABLED=0，
安全相关测试会显式启用并校验行为。
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.app import create_app
from mock_api.database import SessionLocal
from mock_api.settings import reset_settings
from sqlalchemy import text


@pytest.fixture
def client():
    """每次测试创建新的 TestClient（确保 middleware 重新加载环境变量）。"""
    reset_settings()
    return TestClient(create_app())


def _seed_paper(paper_id: str, pdf_url: str) -> None:
    """在测试数据库插入一条论文记录。"""
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT OR REPLACE INTO papers("
                "id, title, authors, abstract, category, tags, year, journal, pdf_url, "
                "citations, chunk_count, index_size, source"
                ") VALUES ("
                ":id, :title, :authors, :abstract, :category, :tags, :year, :journal, :pdf_url, "
                ":citations, :chunk_count, :index_size, :source"
                ")"
            ),
            {
                "id": paper_id,
                "title": "Test Paper",
                "authors": "[]",
                "abstract": "abstract",
                "category": "all",
                "tags": "[]",
                "year": 2024,
                "journal": "",
                "pdf_url": pdf_url,
                "citations": 0,
                "chunk_count": 0,
                "index_size": 0,
                "source": "arxiv",
            },
        )
        db.commit()
    finally:
        db.close()


class TestPdfProxySSRF:
    """PDF 代理 SSRF 防护测试。"""

    def test_pdf_proxy_rejects_non_arxiv_hostname(self, client):
        """非 arXiv 主机名应被 403 拒绝。"""
        paper_id = f"ssrf-{uuid.uuid4().hex[:8]}"
        _seed_paper(paper_id, "http://attacker.com/malicious.pdf")

        resp = client.get(f"/api/papers/{paper_id}/pdf-proxy")
        assert resp.status_code == 403
        assert "SSRF" in resp.text or "允许列表" in resp.text

    def test_pdf_proxy_rejects_private_ip_resolution(self, client):
        """合法域名解析到私网 IP 时应被 DNS rebinding 防护拒绝。"""
        paper_id = f"rebind-{uuid.uuid4().hex[:8]}"
        _seed_paper(paper_id, "https://arxiv.org/pdf/test.pdf")

        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ]
            resp = client.get(f"/api/papers/{paper_id}/pdf-proxy")
            assert resp.status_code == 403
            assert "内部 IP" in resp.text or "SSRF" in resp.text

    def test_pdf_proxy_rejects_file_scheme(self, client):
        """file:// 协议应被 403 拒绝。"""
        paper_id = f"file-{uuid.uuid4().hex[:8]}"
        _seed_paper(paper_id, "file:///etc/passwd")

        resp = client.get(f"/api/papers/{paper_id}/pdf-proxy")
        assert resp.status_code == 403

    def test_pdf_proxy_returns_404_for_missing_paper(self, client):
        """论文不存在时，PDF 代理应返回 404。"""
        paper_id = f"missing-{uuid.uuid4().hex[:8]}"
        resp = client.get(f"/api/papers/{paper_id}/pdf-proxy")
        assert resp.status_code == 404
        assert "不存在" in resp.text

    def test_pdf_proxy_rejects_http_redirect(self, client):
        """上游返回 302 重定向到内部地址时，PDF 代理应拒绝跟随，返回 502。"""
        paper_id = f"redirect-{uuid.uuid4().hex[:8]}"
        _seed_paper(paper_id, "https://arxiv.org/pdf/test.pdf")

        fake_response = Mock()
        fake_response.status_code = 302
        fake_response.headers = {"Location": "http://127.0.0.1:6379/internal.pdf"}
        with patch("mock_api.services.pdf_proxy_service.requests.get", return_value=fake_response):
            resp = client.get(f"/api/papers/{paper_id}/pdf-proxy")
            assert resp.status_code == 502, f"期望 502，实际 {resp.status_code}"


class TestGlobalAuthMiddleware:
    """全局鉴权中间件测试。"""

    def test_write_operation_blocked_without_auth(self, client, monkeypatch):
        """启用鉴权后，未授权的 POST 应返回 401/403。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        reset_settings()  # 清除 settings 缓存，使新环境变量生效
        # 重新创建 client 以读取新环境变量
        client = TestClient(create_app())

        resp = client.post("/api/favorites", json={"paper_id": "paper-1"})
        assert resp.status_code in (401, 403)

    def test_public_health_endpoint_unauthenticated(self, client, monkeypatch):
        """健康检查端点始终公开。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        reset_settings()
        client = TestClient(create_app())

        resp = client.get("/api/health/live")
        assert resp.status_code == 200

    def test_get_papers_requires_auth(self, client, monkeypatch):
        """GET /api/papers 已纳入鉴权，未授权时应返回 401/403。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        reset_settings()
        client = TestClient(create_app())

        resp = client.get("/api/papers?page=1&page_size=1")
        assert resp.status_code in (401, 403)

    def test_write_operation_with_valid_token_succeeds(self, client, monkeypatch):
        """配置正确 API Token 后，带 Token 的 POST 应通过鉴权。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("PAPERFORGE_API_TOKEN", "valid-token-123")
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
        reset_settings()
        client = TestClient(create_app())

        resp = client.post(
            "/api/favorites",
            json={"paper_id": "paper-1"},
            headers={"X-PaperForge-Token": "valid-token-123"},
        )
        # 论文不存在时后端可能返回 404，但鉴权必须通过（不是 401/403）
        assert resp.status_code not in (401, 403)

    def test_write_operation_with_invalid_token_blocked(self, client, monkeypatch):
        """API Token 不匹配时，POST 应返回 401。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("PAPERFORGE_API_TOKEN", "valid-token-123")
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
        reset_settings()
        client = TestClient(create_app())

        resp = client.post(
            "/api/favorites",
            json={"paper_id": "paper-1"},
            headers={"X-PaperForge-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_admin_endpoint_blocked_outside_dev_env(self, client, monkeypatch):
        """未开启开发态（ENV=development）时，admin 端点应返回 403。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
        reset_settings()
        client = TestClient(create_app())

        resp = client.get("/api/admin/download/test.exe")
        assert resp.status_code == 403

    def test_admin_endpoint_requires_loopback_or_token_in_dev(self, client, monkeypatch):
        """开发态下 admin 端点仍受 loopback + token 约束（TestClient 视为非 loopback）。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("ENV", "development")
        reset_settings()
        client = TestClient(create_app())

        resp = client.get("/api/admin/download/test.exe")
        # TestClient 不来自真实 loopback，且未带 token，应被拒绝
        assert resp.status_code in (401, 403)


@pytest.mark.critical
class TestWriteAuthMatrix:
    """写操作鉴权矩阵：覆盖主要写端点，验证未授权 401/403 与带 token 通过。"""

    @pytest.fixture(autouse=True)
    def _enable_auth(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("PAPERFORGE_API_TOKEN", "test-token-123")
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
        reset_settings()

    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/api/favorites", {"paper_id": "paper-1"}),
            ("POST", "/api/arxiv/import", {"papers": []}),
            ("POST", "/api/depth/score", {"paper_id": "paper-1"}),
            ("POST", "/api/ask", {"question": "test"}),
            ("POST", "/api/generate", {"topic": "test"}),
            ("POST", "/api/search/semantic", {"question": "test"}),
            ("POST", "/api/compute/mode", {"mode": "deep"}),
            ("POST", "/api/writing/projects", {"title": "test"}),
        ],
    )
    def test_write_endpoint_blocked_without_auth(self, method, path, payload, monkeypatch):
        """未带 token 的写操作应返回 401/403。"""
        client = TestClient(create_app())
        resp = client.request(method, path, json=payload)
        assert resp.status_code in (401, 403), f"{method} {path} 应被鉴权拦截"

    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/api/favorites", {"paper_id": "paper-1"}),
            ("POST", "/api/arxiv/import", {"papers": []}),
            ("POST", "/api/depth/score", {"paper_id": "paper-1"}),
            ("POST", "/api/ask", {"question": "test"}),
            ("POST", "/api/generate", {"topic": "test"}),
            ("POST", "/api/search/semantic", {"question": "test"}),
            ("POST", "/api/compute/mode", {"mode": "deep"}),
            ("POST", "/api/writing/projects", {"title": "test"}),
        ],
    )
    def test_write_endpoint_with_valid_token_not_401(self, method, path, payload, monkeypatch):
        """带正确 token 的写操作不应再因鉴权被拒绝（业务 404/422 等允许）。"""
        client = TestClient(create_app())
        resp = client.request(
            method,
            path,
            json=payload,
            headers={"X-PaperForge-Token": "test-token-123"},
        )
        assert resp.status_code not in (401, 403), f"{method} {path} 带 token 仍被鉴权拒绝"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/papers?page=1&page_size=1",
            "/api/papers/nonexistent-id",
            "/api/favorites",
            "/api/stats",
            "/api/depth/v4/list?limit=5",
            "/api/models",
            "/api/model/current",
        ],
    )
    def test_get_endpoint_requires_auth(self, path, monkeypatch):
        """除 health 外的 GET 端点已纳入鉴权，未授权应返回 401/403。"""
        client = TestClient(create_app())
        resp = client.get(path)
        assert resp.status_code in (401, 403), f"GET {path} 应被鉴权拦截"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/papers?page=1&page_size=1",
            "/api/favorites",
            "/api/stats",
            "/api/models",
            "/api/model/current",
        ],
    )
    def test_get_endpoint_with_valid_token_not_401(self, path, monkeypatch):
        """带正确 token 的 GET 操作不应再因鉴权被拒绝。"""
        client = TestClient(create_app())
        resp = client.get(path, headers={"X-PaperForge-Token": "test-token-123"})
        assert resp.status_code not in (401, 403), f"GET {path} 带 token 仍被鉴权拒绝"


class TestOpenMode:
    """open 模式（ALLOW_REMOTE=1 + 未配置 API token）行为验证。"""

    @pytest.fixture(autouse=True)
    def _enable_open_mode(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1")
        # 明确不设置 PAPERFORGE_API_TOKEN
        for key in ("PAPERFORGE_API_TOKEN",):
            monkeypatch.delenv(key, raising=False)
        reset_settings()

    def test_open_mode_get_allowed(self, monkeypatch):
        """open 模式下匿名 GET 应被允许（按 IP 限流），不应 401/403。"""
        client = TestClient(create_app())
        resp = client.get("/api/papers?page=1&page_size=1")
        assert resp.status_code not in (401, 403)

    def test_open_mode_post_blocked(self, monkeypatch):
        """open 模式下匿名 POST 需要 write scope，应被 403 拒绝。"""
        client = TestClient(create_app())
        resp = client.post("/api/favorites", json={"paper_id": "paper-1"})
        assert resp.status_code == 403, f"期望 403，实际 {resp.status_code}"
