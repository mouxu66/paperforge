"""全局设置在线读写（双模型交叉复核 / 被引情感云端复核 等开关）。

设计：直接改写 get_settings() 的 lru_cache 单例，对所有读取方（second_opinion、
crud/analysis、glm_vision 回退路径）立即生效。运行时生效、进程重启后恢复默认——
与 /api/depth/settings 的 runtime override 语义一致（见 runtime_settings.py）。

安全：仅暴露白名单内的非敏感开关，避免误改数据库/鉴权等配置。访问受全局
loopback + 可选 token 中间件保护（与 /api/depth 同级，非常规 admin 端点）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..settings import get_settings

router = APIRouter()

# 允许通过此端点在线调整的设置项（白名单，避免误改敏感配置）
_SETTINGS_WHITELIST = {
    "second_opinion_enabled",
    "second_opinion_override",
    "second_opinion_threshold",
    "second_opinion_model",
    "sentiment_recheck_enabled",
    "sentiment_recheck_low_conf",
}


class AppSettingsResponse(BaseModel):
    """当前生效的双模型 / 引用情感复核相关设置。"""

    second_opinion_enabled: bool
    second_opinion_override: bool
    second_opinion_threshold: float
    second_opinion_model: str
    sentiment_recheck_enabled: bool
    sentiment_recheck_low_conf: float


class AppSettingsUpdate(BaseModel):
    """更新设置（全部可选；仅提交到的字段会被改写）。"""

    second_opinion_enabled: Optional[bool] = None
    second_opinion_override: Optional[bool] = None
    second_opinion_threshold: Optional[float] = Field(default=None, ge=0.01, le=1.0)
    second_opinion_model: Optional[str] = Field(default=None, min_length=1, max_length=64)
    sentiment_recheck_enabled: Optional[bool] = None
    sentiment_recheck_low_conf: Optional[float] = Field(default=None, ge=0.0, le=1.0)


def _current_settings() -> AppSettingsResponse:
    s = get_settings()
    return AppSettingsResponse(
        second_opinion_enabled=s.second_opinion_enabled,
        second_opinion_override=s.second_opinion_override,
        second_opinion_threshold=s.second_opinion_threshold,
        second_opinion_model=s.second_opinion_model,
        sentiment_recheck_enabled=s.sentiment_recheck_enabled,
        sentiment_recheck_low_conf=s.sentiment_recheck_low_conf,
    )


@router.get("/api/settings", response_model=AppSettingsResponse)
async def get_app_settings() -> AppSettingsResponse:
    """读取当前生效的双模型复核相关设置。"""
    return _current_settings()


@router.put("/api/settings", response_model=AppSettingsResponse)
async def update_app_settings(payload: AppSettingsUpdate) -> AppSettingsResponse:
    """在线改写设置（运行时生效，重启后恢复默认）。"""
    s = get_settings()
    # model_dump(exclude_unset=True) 仅含客户端实际提交的字段
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key in _SETTINGS_WHITELIST:
            setattr(s, key, value)
    return _current_settings()
