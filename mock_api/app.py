"""PaperForge 应用工厂——集中式 FastAPI app 创建与中间件配置。

用法:
    from mock_api.app import create_app
    app = create_app()

将原 main.py 中散落于模块顶层的中间件注册、生命周期管理、全局异常处理
收敛为单一工厂函数，便于测试、CI 和未来蓝绿部署。
"""

from __future__ import annotations

import logging
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .errors import TraceMiddleware, envelope_response, register_error_handlers

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss: http: https:; "
    "frame-src 'self' data: blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)
MAX_REQUEST_BODY_BYTES = 100 * 1024 * 1024  # 100 MB


# ── 中间件类 ────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件（CSP/X-Content-Type-Options/X-Frame-Options）。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/docs") or path.startswith("/redoc") or path.startswith("/openapi"):
            csp = DEFAULT_CSP.replace(
                "script-src 'self' 'unsafe-inline'",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            )
        else:
            csp = DEFAULT_CSP
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """请求体大小限制中间件（防 OOM）。"""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > MAX_REQUEST_BODY_BYTES:
                return envelope_response(
                    status_code=413,
                    detail=(
                        f"请求体过大（{size / 1024 / 1024:.1f} MB），"
                        f"最大允许 {MAX_REQUEST_BODY_BYTES / 1024 / 1024:.0f} MB"
                    ),
                    request=request,
                    error_code="payload_too_large",
                )
        return await call_next(request)


# 公开路径白名单（健康检查）。
# 除本集合内的路径外，所有 GET/HEAD/OPTIONS 请求也需要鉴权。
_PUBLIC_PATHS = {"/api/health", "/api/health/live", "/api/health/ready"}


class AuthMiddleware(BaseHTTPMiddleware):
    """全局鉴权中间件（P0 安全加固 + Layer 1 多 API Key）。

    - 仅健康检查路径完全公开。
    - 其他所有请求（含 GET/HEAD/OPTIONS）统一走 authenticate_and_authorize。
    - 鉴权结果写入 request.state.auth_result，供限流/审计中间件使用。
    - /api/admin/* 额外要求开发态。
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. 公开白名单直接放行
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        from .auth import authenticate_and_authorize

        # 2. 统一鉴权（含 GET/HEAD/OPTIONS）
        #    EventSource 无法自定义请求头，仅对 SSE/stream 路径允许通过 query param ?token= 传递 token
        token = request.headers.get("X-PaperForge-Token")
        if token is None and path.endswith("/stream"):
            token = request.query_params.get("token")
        try:
            auth_result = authenticate_and_authorize(request, token)
            request.state.auth_result = auth_result

            # admin 端点额外检查：必须在显式开发态
            if path.startswith("/api/admin/"):
                from .settings import get_settings

                if not get_settings().is_dev_env:
                    return envelope_response(
                        status_code=403,
                        detail="admin 端点仅在显式开发态（ENV=development）可用。",
                        request=request,
                    )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return envelope_response(
                status_code=exc.status_code,
                detail=detail,
                request=request,
                headers=dict(exc.headers or {}),
            )

        return await call_next(request)


# ── 限流 + 审计中间件（Layer 2）────────────────────
class RateLimitAndAuditMiddleware(BaseHTTPMiddleware):
    """per-key 令牌桶限流 + 异步调用审计。

    依赖 AuthMiddleware 已写入 request.state.auth_result。
    执行顺序在 AuthMiddleware 之后（更内层），确保能读到鉴权结果。

    - 超级管理员（global / loopback）不限流
    - 限流超限返回 429 + Retry-After
    - 请求完成后异步记录审计日志（不阻塞响应）
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 跳过公开路径和文档（不消耗令牌、不记审计）
        if path in _PUBLIC_PATHS or path.startswith(("/docs", "/redoc", "/openapi")):
            return await call_next(request)

        auth_result = getattr(request.state, "auth_result", None)
        # 鉴权中间件未运行或鉴权失败 → 放行（由 AuthMiddleware 的 envelope 处理）
        if auth_result is None or not auth_result.authenticated:
            return await call_next(request)

        # ── 限流检查（仅对多 API Key 生效，超级管理员不限流）──
        if auth_result.scopes is not None and not auth_result.is_admin:
            from .rate_limit import get_rate_limiter
            from .settings import get_settings

            # per-key 限流配置（0=用系统默认）
            rate_limit = auth_result.rate_limit_per_min or get_settings().api_key_rate_limit_default
            if rate_limit > 0:
                limiter = get_rate_limiter()
                allowed, retry_after = limiter.check(
                    auth_result.key_id, rate_limit, auth_result.is_admin
                )
                if not allowed:
                    return envelope_response(
                        status_code=429,
                        detail=(f"请求频率超限（{rate_limit}/分钟），请 {retry_after} 秒后重试。"),
                        request=request,
                        headers={"Retry-After": str(retry_after)},
                    )

        # ── 执行请求并计时 ──
        start = _time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            # 异常请求也记审计（status=500）
            status_code = 500
            duration_ms = int((_time.perf_counter() - start) * 1000)
            _audit_log(request, auth_result, status_code, duration_ms)
            raise
        duration_ms = int((_time.perf_counter() - start) * 1000)

        # ── 异步记录审计日志 ──
        _audit_log(request, auth_result, status_code, duration_ms)

        return response


def _audit_log(request: Request, auth_result, status_code: int, duration_ms: int) -> None:
    """异步写入审计日志（不阻塞请求）。"""
    try:
        from .settings import get_settings

        if not get_settings().api_key_audit_enabled:
            return
        from .audit import log_api_call

        client_ip = request.client.host if request.client else ""
        log_api_call(
            key_id=auth_result.key_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
        )
    except Exception:  # noqa: BLE001 - 审计日志失败不应影响主请求
        pass


# ── 慢请求监测（P0 诊断能力）────────────────────
# 目的：把同步 endpoint 的耗时写在 logs/paperforge.log 中,便于定位
# 「分析多个文件」超时的具体瓶颈 endpoint。阈值之上的请求单行 WARNING,
# 含 method / 路径模板 / 状态码 / 秒数 / 客户端 IP+UA,便于 grep 与对齐。
SLOW_REQUEST_THRESHOLD_S = 5.0


class SlowRequestLogger(BaseHTTPMiddleware):
    """慢请求日志中间件(诊断用,不修改响应)。

    仅在 duration >= SLOW_REQUEST_THRESHOLD_S 时写 WARNING/WARNING-with-traceback,
    常规请求不写,避免刷屏。OpenAPI / 健康检查 / SSE 流式端点直接跳过:
    - 前两者是 docs / lifespan 例行探活;
    - 后者避免 BaseHTTPMiddleware 对 streaming body 的潜在 buffering 影响。
    StreamingResponse 不读取 body,仅记录「首字节耗时」。
    """

    async def dispatch(self, request: Request, call_next):
        if request.scope.get("type") != "http":
            return await call_next(request)
        path = request.url.path
        # 跳过文档 / 健康检查 / SSE 流式端点等噪音
        _SKIP_PREFIXES = ("/openapi", "/docs", "/redoc")
        if path.startswith(_SKIP_PREFIXES) or path in _PUBLIC_PATHS or path.endswith("/stream"):
            return await call_next(request)
        start = _time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration = _time.perf_counter() - start
            # logger.exception 自动带上 traceback,便于定位超时 endpoint 的真正根因
            logger.exception(
                "[slow-api-err] %s %s duration=%.3fs (raised)",
                request.method,
                path,
                duration,
            )
            raise
        duration = _time.perf_counter() - start
        if duration < SLOW_REQUEST_THRESHOLD_S:
            return response
        # 优先取路由模板 (如 /api/papers/{paper_id}),退化使用 scope 原始 path
        route = request.scope.get("route")
        log_path = getattr(route, "path", path)
        client_ip = request.client.host if request.client else "unknown"
        ua = (request.headers.get("user-agent") or "unknown")[:80]
        logger.warning(
            "[slow-api] %s %s -> %s duration=%.3fs client=%s ua=%s",
            request.method,
            log_path,
            status_code,
            duration,
            client_ip,
            ua,
        )
        return response


# ── 全局异常处理 ────────────────────────────────────
# 统一 envelope（detail + error_code + trace_id + path）由 mock_api.errors 提供，
# 在 create_app() 末尾 register_error_handlers(app) 注册 4 个 handler：
# - RequestValidationError → 422
# - SQLAlchemyError        → 500 database_error
# - StarletteHTTPException → 透传 4xx / 5xx
# - Exception              → 500 internal_error 兜底
# 旧版 _global_exception_handler 已被 errors.envelope_response 取代。


# ── 生命周期 ────────────────────────────────────────
def _cleanup_ghost_tasks() -> None:
    """启动时清理幽灵任务（上次进程异常中断留下的 pending/running 记录）。"""
    import datetime as _dt
    from datetime import datetime

    from sqlalchemy import text as _text

    from .database import SessionLocal

    STALE_MINUTES = 10
    stale_threshold = (datetime.now() - _dt.timedelta(minutes=STALE_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    db = SessionLocal()
    try:
        tasks_result = db.execute(
            _text(
                "UPDATE tasks "
                "SET status = 'failed', "
                "    error = COALESCE(error, '') || ' | 服务重启导致中断', "
                "    completed_at = COALESCE(completed_at, :now), "
                "    updated_at = :now "
                "WHERE status IN ('pending', 'running') "
                "  AND COALESCE(updated_at, created_at) < :stale"
            ),
            {"now": datetime.now(), "stale": stale_threshold},
        )
        v4_result = db.execute(
            _text(
                "UPDATE depth_reviews_v4 "
                "SET status = 'failed', "
                "    error_message = COALESCE(error_message, '') || ' | 服务重启导致中断', "
                "    completed_at = COALESCE(completed_at, :now) "
                "WHERE status IN ('pending', 'running') "
                "  AND (completed_at IS NULL) "
                "  AND created_at < :stale"
            ),
            {"now": datetime.now(), "stale": stale_threshold},
        )
        db.commit()
        n_tasks = getattr(tasks_result, "rowcount", None) or 0
        n_v4 = getattr(v4_result, "rowcount", None) or 0
        if n_tasks or n_v4:
            logger.warning("启动清理幽灵任务: tasks=%d, depth_reviews_v4=%d", n_tasks, n_v4)
    except Exception as exc:  # noqa: BLE001 - lifespan 钩子 - 启动失败应记录，不阻止应用启动
        logger.exception("lifespan 幽灵任务清理失败: %s", exc)
        db.rollback()
    finally:
        db.close()


def _create_lifespan():
    """创建应用生命周期上下文管理器（封装数据库 init / scheduler / 清理）。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .database import init_db
        from .scheduler import shutdown_scheduler, start_scheduler

        # 启动
        init_db()
        uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # 幽灵任务清理（上次进程异常中断留下的 running/pending 记录）
        _cleanup_ghost_tasks()

        # SSE 主事件循环绑定
        try:
            from . import tasks as tm

            tm.bind_main_event_loop()
        except Exception as exc:  # noqa: BLE001 - lifespan 钩子 - 启动失败应记录，不阻止应用启动
            logger.debug("SSE 主循环绑定跳过: %s", exc)

        start_scheduler()

        # Qwen/llama-server 托管自启（qwen_autostart）：后台拉起，不阻塞启动。
        # 冷启动 3-5 分钟由 manager 后台探活；失败不影响主服务启动。
        try:
            from .settings import get_settings

            if get_settings().qwen_autostart:
                import threading

                from .llama_server_manager import get_llama_server_manager

                threading.Thread(
                    target=get_llama_server_manager().ensure_started,
                    kwargs={"wait": False},
                    daemon=True,
                ).start()
                logger.info("已后台触发 llama-server 自启（qwen_autostart）")
        except Exception as exc:  # noqa: BLE001 - lifespan 钩子 - 启动失败应记录，不阻止应用启动
            logger.warning("llama-server 自启触发失败: %s", exc)

        yield
        # 关闭
        shutdown_scheduler()

        # 关闭 llama-server（如果由本进程托管）
        try:
            from .llama_server_manager import get_llama_server_manager

            get_llama_server_manager().shutdown()
        except Exception as exc:  # noqa: BLE001 - lifespan 钩子 - 关闭失败只记录
            logger.warning("关闭 llama-server 失败: %s", exc)

    return lifespan


# ── 工厂函数 ────────────────────────────────────────
def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例（含中间件、生命周期、异常处理）。"""

    app = FastAPI(
        title="PaperForge Mock API",
        version="3.0.0",
        lifespan=_create_lifespan(),
    )

    # TraceMiddleware 必须最后 add_middleware —— Starlette `add_middleware(X)` 实际是
    # ``user_middleware.insert(0, X)``，后被 ``reversed()`` 作为 dispatch 顺序。
    # 最后一个注册的 TraceMiddleware 称为最外层,先入先出,请求进入时其他 middleware 还能
    # 从 request.state.trace_id 读到 XY。
    app.add_middleware(
        CORSMiddleware,
        # 允许本地前端与浏览器扩展（chrome-extension://<id>）访问 API
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+|chrome-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 安全响应头
    app.add_middleware(SecurityHeadersMiddleware)

    # 请求体大小限制
    app.add_middleware(MaxBodySizeMiddleware)

    # 限流 + 审计（Layer 2）：必须在 AuthMiddleware 之前注册（即更内层），
    # 这样 AuthMiddleware 先执行写入 request.state.auth_result，再轮到本中间件读。
    app.add_middleware(RateLimitAndAuditMiddleware)

    # 全局鉴权（P0 安全加固 + Layer 1 多 API Key）
    app.add_middleware(AuthMiddleware)

    # 慢请求日志(诊断能力):放 TraceMiddleware 之前,Trace 仍为最外层,
    # 慢日志覆盖「鉴权之后 → envelope 之前 → 全链路处理」的总耗时。
    app.add_middleware(SlowRequestLogger)

    # TraceMiddleware:最后注册以保证在最外层,所有 envelope 能取到有效 trace_id
    app.add_middleware(TraceMiddleware)

    # 全局异常处理（统一 envelope；详见 mock_api.errors）
    register_error_handlers(app)

    # 挂载管理后台路由（打包 / 下载）
    from .admin.package import router as admin_router

    app.include_router(admin_router)

    # API Key 管理路由（Layer 1：多调用方管理，/api/system/api-keys）
    from .admin.api_keys import router as api_keys_router

    app.include_router(api_keys_router)

    # 健康检查路由（存活/就绪探针，PR1 抽取自 main.py）
    from .routers.health import router as health_router

    app.include_router(health_router)

    # LLM 模型管理路由（CRUD + 切换，PR2 抽取自 main.py）
    from .routers.arxiv import router as arxiv_router
    from .routers.chat import router as chat_router
    from .routers.compute import router as compute_router
    from .routers.depth import router as depth_router
    from .routers.depth_settings import router as depth_settings_router
    from .routers.experiment_audit import router as experiment_audit_router
    from .routers.figures import router as figures_admin_router
    from .routers.models import router as models_router
    from .routers.papers import router as papers_router
    from .routers.qwen import router as qwen_router
    from .routers.reflection import router as reflection_router
    from .routers.reports import router as reports_router
    from .routers.tasks import router as tasks_router
    from .routers.upload import router as upload_router
    from .routers.writing import router as writing_router
    from .routers.zotero import router as zotero_router

    app.include_router(papers_router)
    app.include_router(models_router)
    app.include_router(qwen_router)
    app.include_router(compute_router)
    app.include_router(chat_router)
    app.include_router(upload_router)
    app.include_router(arxiv_router)
    app.include_router(tasks_router)
    app.include_router(zotero_router)
    app.include_router(depth_router)
    app.include_router(depth_settings_router)
    app.include_router(reports_router)
    app.include_router(reflection_router)
    app.include_router(writing_router)
    app.include_router(figures_admin_router)
    app.include_router(experiment_audit_router)

    return app
