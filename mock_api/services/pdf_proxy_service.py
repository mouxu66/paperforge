"""PDF 代理服务层（SSRF 防护 + 上游 PDF 流获取）。

将原 main.py::pdf_proxy 中的业务逻辑抽离为可独立测试的服务函数：
- resolve_pdf_url: 根据论文 ID 与原始 pdf_url 生成待代理 URL
- validate_pdf_url: SSRF 校验（协议、主机名白名单、DNS 解析后 IP）
- fetch_pdf_stream: 拉取上游 PDF 并返回可迭代字节流与响应头

该模块不依赖路由层，仅使用标准库与 requests/fastapi HTTPException。
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
import threading
from collections.abc import Iterator
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests
from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)

# 串行化 PDF 代理请求：防止 socket.getaddrinfo 临时补丁影响并发请求，
# 同时避免 DNS rebinding TOCTOU 窗口被并发请求放大。
_FETCH_LOCK = threading.Lock()

# 允许代理的 arXiv 主机名白名单
ALLOWED_HOSTS = {
    "arxiv.org",
    "www.arxiv.org",
    "ar5iv.labs.arxiv.org",
    "export.arxiv.org",
}


def resolve_pdf_url(paper_id: str, raw_pdf_url: str | None) -> str:
    """解析并归一化待代理的 PDF URL。

    - 无 pdf_url 时按 paper_id 拼装 arXiv 链接
    - 无 scheme 的相对 ID 同样按 arXiv 处理
    - 其他情况直接返回原始 URL，交给 validate_pdf_url 校验
    """
    if not raw_pdf_url:
        return f"https://arxiv.org/pdf/{paper_id}.pdf"
    if "://" not in raw_pdf_url:
        return f"https://arxiv.org/pdf/{raw_pdf_url}.pdf"
    return raw_pdf_url


def validate_url_ssrf(url: str) -> str:
    """对任意 URL 执行 SSRF 校验，通过则返回原 URL。

    与 ``validate_pdf_url`` 不同，本函数不对主机名做白名单限制，
    仅做协议与 DNS 解析后 IP 层的 SSRF 防护，适用于 arXiv 之外的
    外部 PDF 源（如 Semantic Scholar 的 openAccessPdf）。

    校验顺序：
    1. 协议仅允许 http/https（拦截 file://、data:、gopher: 等）
    2. DNS 解析后 IP 不得为私网/回环/链路本地/保留地址（防 DNS rebinding）
    """
    parsed = urlparse(url)

    if not parsed.scheme or parsed.scheme.lower() not in {"http", "https"}:
        raise HTTPException(
            status_code=403,
            detail=(
                f"PDF 源协议「{parsed.scheme or '空'}」不允许，仅支持 http/https（SSRF 防护）。"
            ),
        )

    if not parsed.hostname:
        raise HTTPException(
            status_code=403,
            detail="PDF 源 URL 缺少主机名（SSRF 防护）。",
        )

    try:
        addr_info = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        # fail-closed：DNS 解析失败即拒绝，避免把不可解析主机当作可信源放行，
        # 否则攻击者可借 DNS 失败绕过私网/回环 IP 校验（SSRF 防护）。
        # 受控代理流（fetch_pdf_stream）走独立的 _resolve_host_ip，不受影响。
        raise HTTPException(
            status_code=403,
            detail=(f"PDF 源主机「{parsed.hostname}」DNS 解析失败，已拒绝（SSRF 防护）。"),
        ) from exc

    for _family, _type, _proto, _canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_addr = ipaddress.ip_address(ip_str)
            if (
                ip_addr.is_private
                or ip_addr.is_loopback
                or ip_addr.is_link_local
                or ip_addr.is_reserved
            ):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"PDF 源主机「{parsed.hostname}」解析到内部 IP {ip_str}，"
                        "已拒绝（SSRF 防护：禁止访问私网/链路本地地址）。"
                    ),
                )
        except ValueError:
            # 非 IPv4/IPv6 地址格式，跳过
            continue

    return url


def validate_pdf_url(url: str) -> str:
    """对 PDF URL 执行 SSRF 校验，通过则返回原 URL。

    校验顺序：
    1. 协议仅允许 http/https（拦截 file://、data:、gopher: 等）
    2. 主机名在 arXiv 白名单内
    3.DNS 解析后 IP 不得为私网/回环/链路本地/保留地址（防 DNS rebinding）
    """
    parsed = urlparse(url)

    if not parsed.scheme or parsed.scheme.lower() not in {"http", "https"}:
        raise HTTPException(
            status_code=403,
            detail=(
                f"PDF 源协议「{parsed.scheme or '空'}」不允许，仅支持 http/https（SSRF 防护）。"
            ),
        )

    if not parsed.hostname or parsed.hostname.lower() not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"PDF 源主机名「{parsed.hostname or '空'}」不在允许列表中（只允许 arXiv 镜像），"
                "出于 SSRF 安全考虑已拒绝代理。"
            ),
        )

    return validate_url_ssrf(url)


def _resolve_host_ip(url: str) -> tuple[str, str]:
    """解析 URL 主机名并返回 (host, ip)。

    仅返回通过 SSRF 校验的第一个非私有/回环/链路本地 IP；
    若解析失败或全部为内部地址，则抛出 403。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=403, detail="PDF 源 URL 缺少主机名（SSRF 防护）。")

    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=502, detail=f"PDF 源 DNS 解析失败：{exc}") from exc

    for _family, _type, _proto, _canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip_addr.is_private
            or ip_addr.is_loopback
            or ip_addr.is_link_local
            or ip_addr.is_reserved
        ):
            continue
        return host, ip_str

    raise HTTPException(
        status_code=403,
        detail=(
            f"PDF 源主机「{host}」未解析到允许的公网 IP，"
            "已拒绝（SSRF 防护：禁止访问私网/回环/链路本地地址）。"
        ),
    )


@contextlib.contextmanager
def _pinned_host_resolution(host: str, ip: str) -> Generator[None, None, None]:
    """临时将 host 的 DNS 解析固定到指定 IP，防止 DNS rebinding。

    在 with 块内，requests/urllib3 对 host 的解析会被强制返回该 IP，
    不再依赖外部 DNS，从而消除 TOCTOU 窗口。
    """
    real_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo(name: str, *args, **kwargs):
        if name and name.lower() == host.lower():
            # 返回与 getaddrinfo 兼容的结构 (family, type, proto, canon, sockaddr)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
        return real_getaddrinfo(name, *args, **kwargs)

    socket.getaddrinfo = _getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


def fetch_pdf_stream(url: str) -> tuple[Iterator[bytes], dict[str, str]]:
    """流式下载上游 PDF。

    Returns:
        (iter_chunks, headers): iter_chunks 为生成器，headers 包含 Content-Type
        与可选 Content-Length。

    Raises:
        HTTPException: 上游下载失败或返回非 200 时抛出 502。
    """
    host, ip = _resolve_host_ip(url)
    try:
        with _FETCH_LOCK, _pinned_host_resolution(host, ip):
            upstream = requests.get(url, stream=True, timeout=30, allow_redirects=False)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"PDF 下载失败：{e}") from e

    if upstream.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"PDF 源返回 {upstream.status_code}",
        )

    def _iter_chunks() -> Iterator[bytes]:
        for chunk in upstream.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    headers: dict[str, str] = {"Content-Type": "application/pdf"}
    content_length = upstream.headers.get("Content-Length")
    if content_length:
        headers["Content-Length"] = content_length

    return _iter_chunks(), headers
