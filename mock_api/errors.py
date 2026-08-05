"""PaperForge 错误信封与全局异常处理 —— 单一事实源。

设计目标（按"前后端契约稳定 + 排障效率"权衡校准）：

1. **响应形态**：所有 4xx / 5xx 返回统一 envelope::

       {
         "detail": "<人可读消息，前端原有 UI 仍可用>",
         "error_code": "<机器可读代号，如 unauthorized / not_found / internal_error>",
         "trace_id": "<per-request UUID v4，前端传给客服/服务端日志检索>",
         "path": "<请求路径，便于排障>"
       }

   - **保留 `detail` 字段**：web/src/api/* 与 web/src/store/* 大量使用
     `error.response.data.detail` 提取消息。改名会触发大面积前端改造。
   - **新增 `error_code` / `trace_id` / `path`**：纯加字段，老客户端不解析
     也无副作用。
2. **trace_id 注入**：TraceMiddleware 最先注册，在 ``request.state.trace_id``
   写入 ``uuid.uuid4()``。下游 middleware（MaxBodySizeMiddleware /
   AuthMiddleware）从 state 里读出后塞进 envelope。
3. **日志策略**：
   - 5xx（包括未捕获 Exception / SQLAlchemyError）→ ``logger.exception``
     带堆栈，便于排障。
   - 4xx（HTTPException client errors、422 validation）→ ``logger.warning``
     不带堆栈（无价值且淹没日志）。
4. **不做任何异常类型吞咽**：所有面向用户的 except 仍由各模块负责；
   本模块只兜"没人接"的最后一手。

公开 API：
- :func:`register_error_handlers`: 在 ``create_app()`` 末尾调用
- :class:`TraceMiddleware`: 在 ``create_app()`` 中 add_middleware 最先注册
- :func:`envelope_response`: 已注册的 middleware 构造 4xx/5xx 响应时调用
- :func:`current_trace_id`: 供日志模块在同请求上下文内打 trace 标签
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# HTTP 状态码 → error_code 映射。覆盖 PaperForge 实际触发的全部 4xx/5xx。
_STATUS_TO_ERROR_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def error_code_for_status(status_code: int) -> str:
    """Map HTTP status code → canonical error_code（fallback = unknown_<status>）。"""
    return _STATUS_TO_ERROR_CODE.get(status_code, f"unknown_{status_code}")


# ── Trace middleware ─────────────────────────────────────
class TraceMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 ``request.state.trace_id = uuid4()``。

    必须是 ``create_app()`` add_middleware 链的最先一项——后续 middleware
    与异常 handler 都需要此 id 才能把同请求路径串起来。
    """

    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        # 把 trace_id 也写到响应头，方便客户端 / 代理侧抓取
        response.headers.setdefault("X-PaperForge-Trace-Id", trace_id)
        return response


def current_trace_id(request: Request) -> str:
    """从 request.state 读出 trace_id（缺省返回 '-'，不应发生）。"""
    return getattr(request.state, "trace_id", "-")


# ── Envelope factory ─────────────────────────────────────
class _UTF8JSONResponse(JSONResponse):
    """强制 JSON 序列化时保留 UTF-8 中文（ensure_ascii=False）。"""

    def render(self, content):
        return json.dumps(content, ensure_ascii=False).encode("utf-8")


def envelope_response(
    *,
    status_code: int,
    detail: str,
    request: Request,
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """构造 envelope JSONResponse —— 所有 4xx/5xx 都应通过这里返回。

    Args:
        status_code: HTTP 状态码。
        detail: 人可读错误描述（前端 UI 直接展示）。
        request: 当前 Request（用于读取 trace_id + 写入 path）。
        error_code: 机器可读代号；缺省按状态码查表。
        extra: 附加字段（如 validation_error 的错误项列表）。
        headers: 透传响应头（如 401 的 WWW-Authenticate）。

    Returns:
        :class:`JSONResponse` —— 包含 ``detail / error_code / trace_id / path``。
    """
    body: dict[str, Any] = {
        "detail": detail,
        "error_code": error_code or error_code_for_status(status_code),
        "trace_id": current_trace_id(request),
        "path": request.url.path,
    }
    if extra:
        body.update(extra)
    return _UTF8JSONResponse(status_code=status_code, content=body, headers=headers)


# ── Exception handlers ──────────────────────────────────
async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """统一 HTTPException（含 FastAPI HTTPException）的返回形态。

    - 5xx → logger.exception 记录堆栈
    - 4xx → logger.warning 简单记录
    """
    status_code = exc.status_code
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    extra: dict[str, Any] | None = None
    if isinstance(exc.detail, dict):
        # 422 validation 这类多字段 detail，保持原结构
        extra = exc.detail
    if status_code >= 500:
        logger.exception(
            "[trace=%s] HTTP %s %s: %s",
            current_trace_id(request),
            request.method,
            request.url.path,
            detail,
        )
    else:
        logger.warning(
            "[trace=%s] HTTP %s %s: %s",
            current_trace_id(request),
            request.method,
            request.url.path,
            detail,
        )
    return envelope_response(
        status_code=status_code,
        detail=detail,
        request=request,
        extra=extra,
        headers=dict(exc.headers or {}),
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic / FastAPI 422 校验失败 → envelope 携带 errors。

    保留 ``errors`` 列表供前端逐字段提示，避免因 envelope 升级丢上下文。
    """
    logger.warning(
        "[trace=%s] 422 validation failed at %s %s: %d error(s)",
        current_trace_id(request),
        request.method,
        request.url.path,
        len(exc.errors()),
    )
    return envelope_response(
        status_code=422,
        detail="请求参数校验失败。",
        request=request,
        error_code="validation_error",
        extra={"errors": exc.errors()},
    )


async def _handle_sqlalchemy_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """SQLAlchemy 异常 → 500 数据库错误。

    不暴露 SQL 细节，仅记录堆栈与 type，让前端看到通用"服务器内部错误"，
    服务端日志拿到完整堆栈。
    """
    logger.exception(
        "[trace=%s] unhandled SQLAlchemyError at %s %s: %s",
        current_trace_id(request),
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return envelope_response(
        status_code=500,
        detail="数据库错误，请稍后重试。",
        request=request,
        error_code="database_error",
    )


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """兜底 handler：捕捉未被任何业务代码处理的异常。

    CancelledError / KeyboardInterrupt 是 BaseException 子类，永远不会被
    此层捕获（FastAPI 已经默认跳过 BaseException）。这里只阻断意外泄漏的
    代码 bug → 用户可见的"500 internal error + trace_id"。
    """
    logger.exception(
        "[trace=%s] unhandled exception at %s %s: %s",
        current_trace_id(request),
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return envelope_response(
        status_code=500,
        detail="服务器内部错误，请稍后重试。",
        request=request,
        error_code="internal_error",
    )


# ── 注册入口 ────────────────────────────────────────────
def register_error_handlers(app: FastAPI) -> None:
    """把全局异常处理挂到 FastAPI app。``create_app()`` 末尾调用。

    注册顺序：先细后宽——``RequestValidationError`` 在 ``Exception`` 之前才会
    优先匹配。
    """
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(SQLAlchemyError, _handle_sqlalchemy_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unhandled_exception)
