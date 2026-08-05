"""API Key 管理端点（Layer 1：多调用方管理）。

路径前缀 /api/system/api-keys —— 需要 admin scope，但不受 is_dev_env 限制
（管理 API Key 是运维需求，生产环境也需要）。

安全设计：
- AuthMiddleware 已通过 scope 映射（/api/system/ → admin）校验权限
- 全局 token（PAPERFORGE_API_TOKEN）视为超级管理员，可访问
- 创建 key 时明文仅返回一次

OpenAPI 文档增强：
- 每个端点提供 summary / description / responses 示例
- 错误响应（401/403/404/400）均附带 JSON 示例
- 调用方可直接在 /docs 体验，无需查阅源码
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from ..api_keys import get_api_key_service
from ..audit import cleanup_old_logs, query_usage_summary
from ..database import SessionLocal
from ..schemas import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ApiKeyUpdate,
    ApiKeyUsageResponse,
)

logger = logging.getLogger(__name__)

# 通用错误响应模板（OpenAPI 文档用）
_COMMON_401 = {
    "description": "未授权：缺少或无效的 X-PaperForge-Token 头。",
    "content": {
        "application/json": {
            "example": {"detail": "无效或缺失的 API Token（X-PaperForge-Token 头）。"}
        }
    },
}
_COMMON_403 = {
    "description": "权限不足：当前 Key 仅有 read/write 权限，需 admin 权限。",
    "content": {
        "application/json": {
            "example": {
                "detail": "权限不足：当前 Key 仅有 ['read'] 权限，本次请求需要 'admin' 权限。"
            }
        }
    },
}
_COMMON_404 = {
    "description": "API Key 不存在。",
    "content": {"application/json": {"example": {"detail": "API Key ak_xxx 不存在"}}},
}
_COMMON_400 = {
    "description": "参数越界。",
    "content": {"application/json": {"example": {"detail": "hours 须在 1-720 之间"}}},
}

router = APIRouter(
    prefix="/api/system/api-keys",
    tags=["API Key 管理"],
)
# 路由组描述（FastAPI 0.115 不支持 APIRouter(description=...)，故用 docstring 形式记录）：
# PaperForge 多 API Key 管理端点。
# 用于为外部调用方（合作机构、自动化脚本、第三方集成）签发、吊销、查询 API Key，
# 并查看用量统计与审计日志。
# 鉴权要求：所有端点均需 admin scope。可通过以下三种方式之一访问：
#   1. 全局 token（X-PaperForge-Token: <PAPERFORGE_API_TOKEN>）—— 超级管理员
#   2. loopback 无 token（本机访问，桌面模式默认）—— 超级管理员
#   3. 拥有 admin scope 的 API Key（X-PaperForge-Token: pf_live_xxx_ak_xxx）
# 明文安全：创建时返回的 key 字段仅此一次明文返回，数据库只存 sha256 hash。


def _orm_to_list_item(api_key) -> ApiKeyListItem:
    """ORM → 列表项（不含完整 key）。"""
    return ApiKeyListItem(
        id=api_key.id,
        keyId=api_key.key_id,
        keyPrefix=api_key.key_prefix,
        name=api_key.name,
        description=api_key.description or "",
        scopes=api_key.scopes or [],
        rateLimitPerMin=api_key.rate_limit_per_min,
        enabled=api_key.enabled,
        createdAt=api_key.created_at.strftime("%Y-%m-%d %H:%M:%S") if api_key.created_at else "",
        lastUsedAt=api_key.last_used_at.strftime("%Y-%m-%d %H:%M:%S")
        if api_key.last_used_at
        else None,
    )


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建 API Key（明文仅此一次返回）",
    description=(
        "为外部调用方签发新的 API Key。\n\n"
        "**安全提示**：\n"
        "- 响应体中的 `key` 字段为完整明文，**仅在创建时返回一次**\n"
        "- 数据库只存 sha256 hash，无法找回明文\n"
        "- 请调用方立即保存到密钥管理系统（如 Vault、AWS Secrets Manager）\n"
        "- 若丢失只能吊销重建\n\n"
        "**scope 说明**：\n"
        "- `read`：GET/HEAD/OPTIONS 请求\n"
        "- `write`：POST/PUT/DELETE/PATCH 请求\n"
        "- `admin`：所有权限 + 本组管理端点\n\n"
        "**限流说明**：\n"
        "- `rateLimitPerMin=0` 表示使用系统默认（`PAPERFORGE_API_KEY_RATE_LIMIT_DEFAULT`，默认 60/分钟）\n"
        "- 超级管理员（全局 token / loopback）不受限流影响"
    ),
    responses={
        401: _COMMON_401,
        403: _COMMON_403,
        422: {
            "description": "请求体校验失败（如 scopes 包含非法值、name 为空）。",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {
                                "type": "value_error",
                                "loc": ["body", "scopes"],
                                "msg": "scopes 仅允许 ['read', 'write', 'admin']，且去重后不能为空",
                            }
                        ]
                    }
                }
            },
        },
    },
)
def create_api_key(body: ApiKeyCreate) -> ApiKeyCreateResponse:
    """创建新 API Key —— 明文仅此一次返回，请妥善保存。"""
    service = get_api_key_service()
    api_key, full_key = service.create(
        name=body.name,
        description=body.description,
        scopes=body.scopes,
        rate_limit_per_min=body.rateLimitPerMin,
    )
    logger.info(
        "创建 API Key: key_id=%s, name=%s, scopes=%s", api_key.key_id, body.name, body.scopes
    )
    return ApiKeyCreateResponse(
        keyId=api_key.key_id,
        key=full_key,
        name=api_key.name,
        scopes=api_key.scopes,
        rateLimitPerMin=api_key.rate_limit_per_min,
        createdAt=api_key.created_at.strftime("%Y-%m-%d %H:%M:%S") if api_key.created_at else "",
    )


@router.get(
    "",
    response_model=list[ApiKeyListItem],
    summary="列出所有 API Key",
    description=(
        "返回所有 API Key 的元数据列表（不含明文 key）。\n\n"
        "**参数**：\n"
        "- `enabled_only=true`：仅返回启用中的 Key（默认 false，返回全部）\n\n"
        "**响应字段**：\n"
        "- `keyPrefix`：key 前 12 字符（如 `pf_live_abcd`），用于列表展示识别\n"
        "- `lastUsedAt`：最近一次被中间件记录使用的时间（限流/审计触发更新）"
    ),
    responses={401: _COMMON_401, 403: _COMMON_403},
)
def list_api_keys(enabled_only: bool = False) -> list[ApiKeyListItem]:
    """列出所有 API Key（不含完整 key 明文）。"""
    service = get_api_key_service()
    keys = service.list_all(enabled_only=enabled_only)
    return [_orm_to_list_item(k) for k in keys]


@router.get(
    "/{key_id}",
    response_model=ApiKeyListItem,
    summary="查询单个 API Key 详情",
    description=(
        "按 `key_id`（如 `ak_abcd1234`）查询某个 API Key 的元数据。\n\n"
        "**注意**：`key_id` 是创建时返回的短 ID（`ak_` 前缀），不是完整明文 key。\n"
        "完整明文 key 不会在查询接口返回（数据库无明文存储）。"
    ),
    responses={401: _COMMON_401, 403: _COMMON_403, 404: _COMMON_404},
)
def get_api_key(key_id: str) -> ApiKeyListItem:
    """查询单个 API Key 详情。"""
    service = get_api_key_service()
    api_key = service.get_by_id(key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
    return _orm_to_list_item(api_key)


@router.put(
    "/{key_id}",
    response_model=ApiKeyListItem,
    summary="更新 API Key 配置",
    description=(
        "更新 API Key 的名称、描述、scope、限流配置或启用状态。\n\n"
        "**部分更新语义**：所有字段都是可选（`None` 表示不修改）。\n\n"
        "**典型用法**：\n"
        '- 临时降低某调用方的限流：`{"rateLimitPerMin": 10}`\n'
        '- 调整权限范围：`{"scopes": ["read"]}`（从读写降为只读）\n'
        '- 重命名用途：`{"name": "新合作方名称", "description": "新说明"}`\n\n'
        "**注意**：若要禁用 Key 但保留审计记录，请使用 `POST /{key_id}/revoke` 而非 `enabled=false`。"
    ),
    responses={
        401: _COMMON_401,
        403: _COMMON_403,
        404: _COMMON_404,
        422: {
            "description": "请求体校验失败。",
        },
    },
)
def update_api_key(key_id: str, body: ApiKeyUpdate) -> ApiKeyListItem:
    """更新 API Key 配置（名称、描述、scope、限流、启用状态）。"""
    service = get_api_key_service()
    api_key = service.update(
        key_id,
        name=body.name,
        description=body.description,
        scopes=body.scopes,
        rate_limit_per_min=body.rateLimitPerMin,
        enabled=body.enabled,
    )
    if api_key is None:
        raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
    return _orm_to_list_item(api_key)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="彻底删除 API Key（不可恢复）",
    description=(
        "**危险操作**：从数据库彻底删除 API Key 记录，不可恢复。\n\n"
        "**建议**：生产环境优先使用 `POST /{key_id}/revoke` 吊销，保留审计记录便于追溯。\n"
        "仅在测试环境或数据清理场景使用 DELETE。\n\n"
        "**响应**：204 No Content（无响应体）。"
    ),
    responses={
        401: _COMMON_401,
        403: _COMMON_403,
        404: _COMMON_404,
    },
)
def delete_api_key(key_id: str):
    """彻底删除 API Key（不可恢复）。建议用 revoke 替代以保留审计记录。"""
    service = get_api_key_service()
    if not service.delete(key_id):
        raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
    logger.info("删除 API Key: key_id=%s", key_id)


@router.post(
    "/{key_id}/revoke",
    response_model=ApiKeyListItem,
    summary="吊销 API Key（禁用，保留审计记录）",
    description=(
        "立即禁用 API Key，使其后续调用返回 401，但**保留数据库记录**便于审计追溯。\n\n"
        "**与 DELETE 的区别**：\n"
        "- `revoke`：`enabled=false`，记录保留，可查询历史用量 —— **推荐**\n"
        "- `DELETE`：物理删除，无记录 —— 仅测试用\n\n"
        "**响应**：返回更新后的 Key 元数据（`enabled=false`）。"
    ),
    responses={401: _COMMON_401, 403: _COMMON_403, 404: _COMMON_404},
)
def revoke_api_key(key_id: str) -> ApiKeyListItem:
    """吊销（禁用）API Key —— 保留记录但立即失效。"""
    service = get_api_key_service()
    if not service.revoke(key_id):
        raise HTTPException(status_code=404, detail=f"API Key {key_id} 不存在")
    api_key = service.get_by_id(key_id)
    return _orm_to_list_item(api_key)


@router.get(
    "/usage/all",
    response_model=ApiKeyUsageResponse,
    summary="查询所有 Key 的用量统计",
    description=(
        "聚合查询最近 N 小时内所有 API Key 的调用统计。\n\n"
        "**参数**：\n"
        "- `hours`：时间窗口（小时），默认 24，范围 1-720（即最长 30 天）\n\n"
        "**响应字段**：\n"
        "- `totalCalls`：总调用次数（含失败）\n"
        "- `successCalls`：状态码 < 400 的调用次数\n"
        "- `failedCalls`：状态码 >= 400 的调用次数\n"
        "- `avgDurationMs`：平均响应耗时（毫秒）\n"
        "- `lastCalledAt`：最近一次调用时间\n\n"
        "**数据来源**：`api_call_logs` 表（由 `RateLimitAndAuditMiddleware` 异步写入）。"
        "若 `PAPERFORGE_API_KEY_AUDIT_ENABLED=false` 则无数据。"
    ),
    responses={401: _COMMON_401, 403: _COMMON_403, 400: _COMMON_400},
)
def get_usage_summary(hours: int = 24) -> ApiKeyUsageResponse:
    """查询所有 Key 的用量统计（默认最近 24 小时）。"""
    if hours < 1 or hours > 720:
        raise HTTPException(status_code=400, detail="hours 须在 1-720 之间")
    db = SessionLocal()
    try:
        items = query_usage_summary(db, hours=hours)
        return ApiKeyUsageResponse(items=items, period=f"{hours}h")
    finally:
        db.close()


@router.post(
    "/cleanup-logs",
    response_model=dict[str, int],
    summary="清理审计日志",
    description=(
        "清理 N 天前的 `api_call_logs` 记录，释放数据库空间。\n\n"
        "**参数**：\n"
        "- `days`：保留最近多少天的日志，默认 30，范围 1-365\n\n"
        "**建议**：\n"
        "- 高频调用场景每周执行一次（days=7）\n"
        "- 低频调用可保留更长（days=90）\n"
        "- 可配合 cron / 计划任务定期调用\n\n"
        '**响应**：`{"deleted": <删除行数>, "days": <保留天数>}`'
    ),
    responses={401: _COMMON_401, 403: _COMMON_403, 400: _COMMON_400},
)
def cleanup_audit_logs(days: int = 30) -> dict:
    """清理 N 天前的审计日志。返回删除行数。"""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days 须在 1-365 之间")
    db = SessionLocal()
    try:
        deleted = cleanup_old_logs(db, days=days)
        logger.info("清理审计日志: 删除 %d 条 %d 天前记录", deleted, days)
        return {"deleted": deleted, "days": days}
    finally:
        db.close()
