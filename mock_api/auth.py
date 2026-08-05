"""PaperForge 鉴权依赖（本地单用户 + 多 API Key 双模式）。

设计目标：
1. 本地单用户模式（向后兼容）：loopback 限制 + 可选全局 token（PAPERFORGE_API_TOKEN）。
2. 多 API Key 模式（Layer 1，对外 API 调用）：
   - key 以 pf_live_ 前缀开头，走 ApiKeyService 验证
   - 全局 token 视为超级管理员，拥有所有 scope
   - scope 权限：read=GET，write=POST/PUT/DELETE，admin=/api/admin/*
3. admin 高危端点用 `require_admin_auth`，叠加 IS_DEV_ENV 显式开关。

不在本模块处理的：
- 用户体系 / 多租户 / JWT（轻量工具无需）
- HTTPS 传输层（由部署方/反代负责）

开关语义：
- IS_DEV_ENV（settings.py）：翻转默认为 False，仅显式 ENV=development 时为 True。
- PAPERFORGE_API_TOKEN：非空时启用全局 token 校验；作为超级管理员 key。
- PAPERFORGE_ALLOW_REMOTE：设为 "1" 时放宽 loopback 限制（对外 API 必须开启）。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_TOKEN_HEADER = APIKeyHeader(name="X-PaperForge-Token", auto_error=False)

Scope = Literal["read", "write", "admin"]


@dataclass
class AuthResult:
    """鉴权结果（存入 request.state 供限流/审计中间件使用）。"""

    authenticated: bool = False
    key_id: str = "anonymous"  # ApiKey.key_id 或 "global"（全局 token）或 "loopback"
    scopes: list[str] | None = None  # None=超级管理员（全局 token / loopback 免鉴权）
    is_admin: bool = False  # 全局 token 或 admin scope
    rate_limit_per_min: int = 0  # per-key 限流配置（0=用系统默认）


def _settings_auth_enabled() -> bool:
    """读取集中式配置中的鉴权开关。"""
    from .settings import get_settings

    return get_settings().auth_enabled


def _settings_allow_remote() -> bool:
    """读取集中式配置中的远端访问开关。"""
    from .settings import get_settings

    return get_settings().allow_remote


def _settings_api_token() -> str | None:
    """读取集中式配置中的 API Token。"""
    from .settings import get_settings

    return get_settings().api_token


def _is_loopback(host: str | None) -> bool:
    """判断请求来源是否为本机回环。"""
    if not host:
        return False
    h = host.split(":")[0].lower()  # 去端口
    return h in ("127.0.0.1", "::1", "localhost")


def _client_ip(request: Request) -> str:
    """提取客户端 IP，优先 X-Forwarded-For 首个地址，退化到 request.client。

    注意：当前未维护可信代理列表，X-Forwarded-For 可被客户端伪造。
    该 IP 仅用于 open 模式的基础按 IP 限流；在受信反向代理后部署时，
    应补充可信代理白名单以正确识别客户端 IP。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    client = request.client
    return client.host if client else "unknown"


def _check_loopback(request: Request) -> None:
    """检查请求来源是否为本机回环，非回环且未显式允许远端时拒绝。"""
    if _settings_allow_remote():
        return
    client = request.client
    host = client.host if client else None
    if not _is_loopback(host):
        logger.warning(
            "鉴权拒绝：非本机回环请求 host=%s（设置 PAPERFORGE_ALLOW_REMOTE=1 可放宽）",
            host,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="出于安全考虑，仅允许本机访问。如需内网/远端暴露，请设置 PAPERFORGE_ALLOW_REMOTE=1。",
        )


def _check_token(provided: str | None) -> bool:
    """检查全局 API Token 是否匹配。

    Returns:
        True 表示匹配全局 token（超级管理员），False 表示不匹配。
    """
    token = _settings_api_token()
    if token is None:
        return False  # 未配置 token
    if provided is None or not secrets.compare_digest(provided, token):
        return False
    return True


# ---------------------------------------------------------------------------
# scope 需求映射
# ---------------------------------------------------------------------------
def required_scope_for(method: str, path: str) -> Scope:
    """根据 HTTP 方法和路径推断所需的 scope。

    - /api/admin/* 和 /api/system/* → admin
    - GET/HEAD/OPTIONS → read
    - POST/PUT/DELETE/PATCH → write
    """
    if path.startswith("/api/admin/") or path.startswith("/api/system/"):
        return "admin"
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return "read"
    return "write"


def _has_required_scope(auth_result: AuthResult, required: Scope) -> bool:
    """检查鉴权结果是否拥有所需 scope。

    - scopes=None 表示超级管理员（全局 token / loopback 免鉴权），拥有所有权限
    - admin scope 拥有所有权限
    """
    if auth_result.scopes is None:
        return True  # 超级管理员
    if "admin" in auth_result.scopes:
        return True
    return required in auth_result.scopes


# ---------------------------------------------------------------------------
# 核心鉴权函数（AuthMiddleware 调用）
# ---------------------------------------------------------------------------
def authenticate_and_authorize(request: Request, token: str | None) -> AuthResult:
    """统一鉴权 + 授权入口（AuthMiddleware 调用）。

    鉴权流程：
    1. 鉴权开关关闭 → 直接放行（超级管理员）
    2. loopback 请求 + 未配置全局 token → 放行（超级管理员，向后兼容）
    3. token 以 pf_live_ 开头 → 多 API Key 验证（ApiKeyService）
    4. 其他 token → 全局 token 验证（超级管理员）
    5. 验证成功后检查 scope 是否满足

    Raises:
        HTTPException: 鉴权失败或 scope 不足时抛出
    Returns:
        AuthResult: 鉴权结果
    """
    # 1. 鉴权开关关闭
    if not _settings_auth_enabled():
        return AuthResult(authenticated=True, key_id="disabled", scopes=None, is_admin=True)

    # 2. loopback 检查
    try:
        _check_loopback(request)
    except HTTPException:
        # 非回环且未允许远端 → 检查是否有有效 token
        if token is None:
            raise  # 无 token，拒绝
        # 有 token 的远端请求继续走 token 验证

    client = request.client
    is_loopback = _is_loopback(client.host if client else None)

    # 3. 多 API Key 验证（pf_live_ 前缀）
    if token and token.startswith("pf_live_"):
        from .api_keys import get_api_key_service

        service = get_api_key_service()
        api_key = service.verify(token)
        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效或已吊销的 API Key。",
                headers={"WWW-Authenticate": "apiKey"},
            )
        result = AuthResult(
            authenticated=True,
            key_id=api_key.key_id,
            scopes=api_key.scopes or ["read"],
            is_admin="admin" in (api_key.scopes or []),
            rate_limit_per_min=api_key.rate_limit_per_min,
        )
    # 4. 全局 token 验证
    elif token and _check_token(token):
        result = AuthResult(authenticated=True, key_id="global", scopes=None, is_admin=True)
    # 5. loopback 免鉴权（向后兼容：未配置 token 时本机访问直接放行）
    elif is_loopback and _settings_api_token() is None:
        result = AuthResult(authenticated=True, key_id="loopback", scopes=None, is_admin=True)
    # 6. 远端 open 模式（ALLOW_REMOTE=1 + 未配置 token + 无 API Key）
    #    允许匿名 GET/HEAD/OPTIONS（read scope）并施加按 IP 基础限流；
    #    写操作需要 write scope，因此未带 token 的 POST/PUT/DELETE 会被拒绝。
    elif _settings_allow_remote() and _settings_api_token() is None and token is None:
        from .settings import get_settings

        result = AuthResult(
            authenticated=True,
            key_id=f"ip:{_client_ip(request)}",
            scopes=["read"],
            is_admin=False,
            rate_limit_per_min=get_settings().api_key_rate_limit_default,
        )
    else:
        # 既不是有效 API Key，也不是全局 token，也不是免鉴权 loopback
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失的 API Token（X-PaperForge-Token 头）。",
            headers={"WWW-Authenticate": "apiKey"},
        )

    # 6. scope 授权检查
    required = required_scope_for(request.method, request.url.path)
    if not _has_required_scope(result, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足：当前 Key 仅有 {result.scopes} 权限，本次请求需要 '{required}' 权限。",
        )

    return result


# ---------------------------------------------------------------------------
# FastAPI 依赖（向后兼容旧用法）
# ---------------------------------------------------------------------------
def require_auth(request: Request, token: str | None = Depends(_TOKEN_HEADER)) -> None:
    """通用鉴权依赖（向后兼容，实际鉴权已由 AuthMiddleware 完成）。

    AuthMiddleware 已在请求进入路由前完成鉴权并写入 request.state.auth_result。
    此依赖仅做存在性检查，保持旧代码的 Depends(require_auth) 用法不变。
    """
    # AuthMiddleware 已完成鉴权；此处仅防止绕过中间件的直接调用
    auth_result = getattr(request.state, "auth_result", None)
    if auth_result is None and _settings_auth_enabled():
        # 中间件未运行（如测试场景），走旧逻辑兜底
        _check_loopback(request)
        if _settings_api_token() is not None and not _check_token(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效或缺失的 API Token（X-PaperForge-Token 头）。",
                headers={"WWW-Authenticate": "apiKey"},
            )


def require_admin_auth(request: Request, token: str | None = Depends(_TOKEN_HEADER)) -> None:
    """admin 高危端点鉴权依赖：必须显式开发态 + 鉴权通过。

    用法：
        router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin_auth)])
    """
    from .settings import get_settings

    if not get_settings().is_dev_env:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin 端点仅在显式开发态（ENV=development）可用。",
        )
    if _settings_auth_enabled():
        # 复用 AuthMiddleware 的结果
        auth_result = getattr(request.state, "auth_result", None)
        if auth_result is not None:
            if not auth_result.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="admin 端点需要 admin 权限。",
                )
        else:
            # 中间件未运行，走旧逻辑
            _check_loopback(request)
            if _settings_api_token() is not None and not _check_token(token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效或缺失的 API Token（X-PaperForge-Token 头）。",
                    headers={"WWW-Authenticate": "apiKey"},
                )
