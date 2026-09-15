"""DEPTH A/B 参数在线调节。

允许用户在「设置」页面实时调整 DEPTH 审稿参数，无需重启服务或修改环境变量。
覆盖值保存在 runtime_settings 的进程级字典中，对后续 DEPTH 审稿立即生效。

安全说明（2026-08-03 修复）：原实现挂在 /api/admin/depth 下并依赖
require_admin_auth（仅显式开发态可用），导致生产模式（桌面版 exe）下
「设置 → DEPTH 参数调优」整块功能 403 不可用。本路由为用户可见的常规设置
（与 compute/model 等设置同级、仅 loopback + 可选 token 保护），因此改为
非 admin 前缀 /api/depth。进程重启后覆盖值丢失（runtime 语义，符合预期）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from ..compute_mode import (
    get_depth_claim_validation_penalty_max,
    get_depth_claim_validation_penalty_per_claim,
    get_depth_delta_bounds_overrides,
    get_depth_delta_default_max,
    get_depth_delta_default_min,
    get_depth_q5c_claim_severity_factor,
    get_depth_severity_fallback_threshold,
    get_depth_severity_fatal_weight,
    get_depth_severity_minor_weight,
)
from ..runtime_settings import set_runtime_overrides

router = APIRouter(prefix="/api/depth")


class DepthSettingsResponse(BaseModel):
    """当前生效的 DEPTH A/B 参数。"""

    severity_fallback_threshold: float = Field(..., ge=0.0, le=1.0)
    severity_fatal_weight: float = Field(..., ge=0.0)
    severity_minor_weight: float = Field(..., ge=0.0)
    q5c_claim_severity_factor: float = Field(..., ge=0.0)
    claim_validation_penalty_per_claim: float = Field(..., ge=0.0)
    claim_validation_penalty_max: float = Field(..., ge=0.0)
    delta_default_min: float
    delta_default_max: float
    delta_bounds_overrides: dict[str, Any]


class DepthSettingsUpdate(BaseModel):
    """更新 DEPTH A/B 参数的请求体。

    所有字段可选；提供的字段会覆盖运行时默认值，未提供的字段保持当前值。
    """

    severity_fallback_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    severity_fatal_weight: float | None = Field(default=None, ge=0.0)
    severity_minor_weight: float | None = Field(default=None, ge=0.0)
    q5c_claim_severity_factor: float | None = Field(default=None, ge=0.0)
    claim_validation_penalty_per_claim: float | None = Field(default=None, ge=0.0)
    claim_validation_penalty_max: float | None = Field(default=None, ge=0.0)
    delta_default_min: float | None = None
    delta_default_max: float | None = None
    delta_bounds_overrides: dict[str, Any] | None = None

    @field_validator("delta_bounds_overrides")
    @classmethod
    def _validate_delta_bounds_overrides(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """校验每个覆盖项都包含数值 min/max，且 min <= max。"""
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("delta_bounds_overrides 必须是对象")
        for key, cfg in v.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"delta_bounds_overrides[{key}] 必须是对象")
            min_val = cfg.get("min")
            max_val = cfg.get("max")
            try:
                min_val = float(min_val)  # type: ignore[arg-type]
                max_val = float(max_val)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"delta_bounds_overrides[{key}] 的 min/max 必须是数值") from exc
            if min_val > max_val:
                raise ValueError(f"delta_bounds_overrides[{key}] 的 min 不能大于 max")
            # 替换为标准化后的值
            cfg["min"] = min_val
            cfg["max"] = max_val
        return v


def _current_settings() -> DepthSettingsResponse:
    """从当前运行时 / 环境配置构造响应对象。"""
    return DepthSettingsResponse(
        severity_fallback_threshold=get_depth_severity_fallback_threshold(),
        severity_fatal_weight=get_depth_severity_fatal_weight(),
        severity_minor_weight=get_depth_severity_minor_weight(),
        q5c_claim_severity_factor=get_depth_q5c_claim_severity_factor(),
        claim_validation_penalty_per_claim=get_depth_claim_validation_penalty_per_claim(),
        claim_validation_penalty_max=get_depth_claim_validation_penalty_max(),
        delta_default_min=get_depth_delta_default_min(),
        delta_default_max=get_depth_delta_default_max(),
        delta_bounds_overrides=get_depth_delta_bounds_overrides(),
    )


@router.get("/settings", response_model=DepthSettingsResponse)
async def get_depth_settings() -> DepthSettingsResponse:
    """获取当前生效的 DEPTH A/B 参数。"""
    return _current_settings()


@router.put("/settings", response_model=DepthSettingsResponse)
async def update_depth_settings(payload: DepthSettingsUpdate) -> DepthSettingsResponse:
    """更新 DEPTH A/B 参数运行时覆盖值。"""
    updates: dict[str, Any] = {}
    if payload.severity_fallback_threshold is not None:
        updates["depth_severity_fallback_threshold"] = payload.severity_fallback_threshold
    if payload.severity_fatal_weight is not None:
        updates["depth_severity_fatal_weight"] = payload.severity_fatal_weight
    if payload.severity_minor_weight is not None:
        updates["depth_severity_minor_weight"] = payload.severity_minor_weight
    if payload.q5c_claim_severity_factor is not None:
        updates["depth_q5c_claim_severity_factor"] = payload.q5c_claim_severity_factor
    if payload.claim_validation_penalty_per_claim is not None:
        updates["depth_claim_validation_penalty_per_claim"] = (
            payload.claim_validation_penalty_per_claim
        )
    if payload.claim_validation_penalty_max is not None:
        updates["depth_claim_validation_penalty_max"] = payload.claim_validation_penalty_max
    if payload.delta_default_min is not None:
        updates["depth_delta_default_min"] = payload.delta_default_min
    if payload.delta_default_max is not None:
        updates["depth_delta_default_max"] = payload.delta_default_max
    if payload.delta_bounds_overrides is not None:
        updates["depth_delta_bounds_overrides"] = payload.delta_bounds_overrides

    if updates:
        set_runtime_overrides(updates)
    return _current_settings()
