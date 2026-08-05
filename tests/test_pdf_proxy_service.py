"""PDF 代理服务层单元测试。

覆盖 URL 归一化、SSRF 校验与上游 PDF 流获取，
不依赖数据库与完整 FastAPI 应用上下文。
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from mock_api.services.pdf_proxy_service import (
    ALLOWED_HOSTS,
    fetch_pdf_stream,
    resolve_pdf_url,
    validate_pdf_url,
)


class TestResolvePdfUrl:
    """URL 归一化逻辑测试。"""

    def test_empty_pdf_url_falls_back_to_arxiv(self):
        assert resolve_pdf_url("2501.12345", None) == "https://arxiv.org/pdf/2501.12345.pdf"
        assert resolve_pdf_url("2501.12345", "") == "https://arxiv.org/pdf/2501.12345.pdf"

    def test_relative_id_becomes_arxiv_url(self):
        assert resolve_pdf_url("2501.12345", "2501.99999") == "https://arxiv.org/pdf/2501.99999.pdf"

    def test_absolute_url_unchanged(self):
        url = "https://arxiv.org/pdf/2501.12345.pdf"
        assert resolve_pdf_url("2501.12345", url) == url


class TestValidatePdfUrl:
    """SSRF 校验函数单元测试。"""

    def test_valid_arxiv_url_passes_with_public_ip(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(2, 1, 6, "", ("151.101.65.42", 0))]
            assert validate_pdf_url("https://arxiv.org/pdf/2501.12345.pdf") == (
                "https://arxiv.org/pdf/2501.12345.pdf"
            )

    def test_file_scheme_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_pdf_url("file:///etc/passwd")
        assert exc_info.value.status_code == 403
        assert "协议" in exc_info.value.detail

    def test_non_arxiv_host_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_pdf_url("https://attacker.com/malicious.pdf")
        assert exc_info.value.status_code == 403
        assert "主机名" in exc_info.value.detail

    def test_private_ip_resolution_rejected(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(2, 1, 6, "", ("192.168.1.1", 0))]
            with pytest.raises(HTTPException) as exc_info:
                validate_pdf_url("https://arxiv.org/pdf/test.pdf")
        assert exc_info.value.status_code == 403
        assert "内部 IP" in exc_info.value.detail

    def test_loopback_ip_rejected(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
            with pytest.raises(HTTPException) as exc_info:
                validate_pdf_url("https://arxiv.org/pdf/test.pdf")
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize(
        "bad_url",
        [
            "ftp://arxiv.org/pdf/2501.12345.pdf",
            "javascript:alert(1)",
            "https:///pdf/2501.12345.pdf",
        ],
    )
    def test_invalid_protocol_or_hostname_rejected(self, bad_url: str):
        with pytest.raises(HTTPException) as exc_info:
            validate_pdf_url(bad_url)
        assert exc_info.value.status_code == 403

    def test_dns_failure_returns_403(self):
        """DNS 解析失败时 fail-closed 拒绝（SSRF 防护：避免借 DNS 失败绕过 IP 校验）。"""
        import socket

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failure")):
            with pytest.raises(HTTPException) as exc_info:
                validate_pdf_url("https://arxiv.org/pdf/test.pdf")
        assert exc_info.value.status_code == 403
        assert "DNS" in exc_info.value.detail

    def test_allowed_hosts_set_contains_arxiv_variants(self):
        assert "arxiv.org" in ALLOWED_HOSTS
        assert "ar5iv.labs.arxiv.org" in ALLOWED_HOSTS


class TestFetchPdfStream:
    """上游 PDF 流获取测试。"""

    def _mock_response(self, content: bytes, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = {"Content-Length": str(len(content))}
        resp.iter_content.return_value = [content[i : i + 4] for i in range(0, len(content), 4)]
        return resp

    def test_successful_fetch_yields_chunks_and_headers(self):
        content = b"%PDF-1.4 fake pdf content"
        resp = self._mock_response(content)
        with (
            patch("requests.get", return_value=resp) as mock_get,
            patch(
                "mock_api.services.pdf_proxy_service._resolve_host_ip",
                return_value=("arxiv.org", "151.101.65.42"),
            ),
        ):
            iter_chunks, headers = fetch_pdf_stream("https://arxiv.org/pdf/2501.12345.pdf")

        mock_get.assert_called_once_with(
            "https://arxiv.org/pdf/2501.12345.pdf", stream=True, timeout=30, allow_redirects=False
        )
        assert headers["Content-Type"] == "application/pdf"
        assert headers["Content-Length"] == str(len(content))
        assert b"".join(iter_chunks) == content

    def test_fetch_pins_resolved_ip_to_block_dns_rebinding(self):
        """请求 PDF 时把 host 解析固定到已校验 IP，防止 DNS rebinding。"""
        content = b"%PDF-1.4 fake pdf content"
        resp = self._mock_response(content)

        def _patched_get(url, **kwargs):
            # 在请求发出期间，socket.getaddrinfo 应被固定到已校验 IP
            resolved = socket.getaddrinfo("arxiv.org", None)
            assert resolved[0][4][0] == "151.101.65.42"
            return resp

        with (
            patch("requests.get", side_effect=_patched_get) as mock_get,
            patch(
                "mock_api.services.pdf_proxy_service._resolve_host_ip",
                return_value=("arxiv.org", "151.101.65.42"),
            ) as mock_resolve,
        ):
            fetch_pdf_stream("https://arxiv.org/pdf/2501.12345.pdf")
            mock_resolve.assert_called_once()
            mock_get.assert_called_once()

    def test_non_200_status_raises_502(self):
        resp = self._mock_response(b"Not found", status_code=404)
        with (
            patch("requests.get", return_value=resp),
            pytest.raises(HTTPException) as exc_info,
        ):
            fetch_pdf_stream("https://arxiv.org/pdf/2501.12345.pdf")
        assert exc_info.value.status_code == 502
        assert "404" in exc_info.value.detail

    def test_request_exception_raises_502(self):
        import requests

        with (
            patch("requests.get", side_effect=requests.RequestException("connection refused")),
            pytest.raises(HTTPException) as exc_info,
        ):
            fetch_pdf_stream("https://arxiv.org/pdf/2501.12345.pdf")
        assert exc_info.value.status_code == 502
        assert "PDF 下载失败" in exc_info.value.detail
