"""LLM 模型管理路由（CRUD + 当前模型查询 / 运行时切换）。

抽取自 mock_api/main.py（PR2）。带走 _llm_config_to_response 和
_safe_model_info 两个专属 helper。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..llm import get_factory
from ..llm.factory import _mask_api_key, detect_provider
from ..models import LLMConfig
from ..schemas import (
    CurrentModelResponse,
    LLMConfigCreate,
    LLMConfigResponse,
    LLMConfigUpdate,
    ModelInfo,
    SwitchModelRequest,
    SwitchModelResponse,
)
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_model_info(m: dict) -> ModelInfo:
    return ModelInfo(value=m["value"], label=m["label"], provider=m["provider"], model=m["model"])


def _llm_config_to_response(c: LLMConfig) -> LLMConfigResponse:
    """ORM 行转响应（API Key 脱敏）。"""
    return LLMConfigResponse(
        id=c.id,
        displayName=c.display_name,
        apiUrl=c.api_url,
        apiKey=_mask_api_key(c.api_key),
        modelId=c.model_id,
        enabled=c.enabled,
        provider=detect_provider(c.api_url),
        createdAt=c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
        updatedAt=c.updated_at.strftime("%Y-%m-%d %H:%M") if c.updated_at else "",
    )


# ---------------------------------------------------------------------------
# LLM 模型管理 CRUD + 当前模型 / 切换
# ---------------------------------------------------------------------------
@router.get("/api/models", response_model=list[LLMConfigResponse])
def list_models(db: Session = Depends(get_db)) -> list[LLMConfigResponse]:
    """列出所有模型配置。"""
    rows = db.query(LLMConfig).order_by(LLMConfig.id).all()
    return [_llm_config_to_response(r) for r in rows]


@router.post("/api/models", response_model=LLMConfigResponse)
def create_model(payload: LLMConfigCreate, db: Session = Depends(get_db)) -> LLMConfigResponse:
    """新增模型配置。"""
    if not payload.displayName.strip() or not payload.apiUrl.strip() or not payload.modelId.strip():
        raise HTTPException(status_code=400, detail="显示名、API 地址和模型 ID 不能为空")
    config = LLMConfig(
        display_name=payload.displayName.strip(),
        api_url=payload.apiUrl.strip(),
        api_key=payload.apiKey.strip(),
        model_id=payload.modelId.strip(),
        enabled=payload.enabled,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return _llm_config_to_response(config)


@router.put("/api/models/{config_id}", response_model=LLMConfigResponse)
def update_model(
    config_id: int, payload: LLMConfigUpdate, db: Session = Depends(get_db)
) -> LLMConfigResponse:
    """更新模型配置（部分编辑字段，apiKey 为空字符串时不更新）。"""
    config = db.query(LLMConfig).filter(LLMConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    if payload.displayName is not None:
        config.display_name = payload.displayName.strip()
    if payload.apiUrl is not None:
        config.api_url = payload.apiUrl.strip()
    if payload.modelId is not None:
        config.model_id = payload.modelId.strip()
    if payload.apiKey is not None and payload.apiKey.strip():
        config.api_key = payload.apiKey.strip()
    if payload.enabled is not None:
        config.enabled = payload.enabled
    db.commit()
    db.refresh(config)
    return _llm_config_to_response(config)


@router.delete("/api/models/{config_id}")
def delete_model(config_id: int, db: Session = Depends(get_db)) -> dict:
    """删除模型配置。"""
    config = db.query(LLMConfig).filter(LLMConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    db.delete(config)
    db.commit()
    return {"success": True}


@router.get("/api/model/current", response_model=CurrentModelResponse)
def get_current_model() -> CurrentModelResponse:
    """获取当前使用的模型值和可用模型列表（从 DB 读取）。"""
    factory = get_factory()
    try:
        current = factory.current_model_value()
        label = factory.current_label()
    except Exception:
        current = ""
        label = ""
    available = factory.available_models()
    return CurrentModelResponse(
        current=current,
        label=label,
        available=[_safe_model_info(m) for m in available],
        enabled=get_settings().model_switch_enabled,
    )


@router.post("/api/model/switch", response_model=SwitchModelResponse)
def switch_model(payload: SwitchModelRequest) -> SwitchModelResponse:
    """运行时切换 LLM（payload.model 为 LLMConfig.id 字符串形式）。"""
    factory = get_factory()
    try:
        factory.switch(payload.model)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模型切换失败: {e}") from e
    current = factory.current_model_value()
    label = factory.current_label()
    return SwitchModelResponse(
        success=True,
        current=current,
        label=label,
        message=f"已切换至 {label}",
    )
