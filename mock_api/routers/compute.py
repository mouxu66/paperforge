"""计算模式（speed/deep）查询与切换。

PR4 抽取：从 main.py 搬迁 compute 路由（纯 config 依赖，无跨域耦合）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..compute_mode import (
    COMPUTE_MODES,
    get_compute_mode,
    get_compute_mode_config,
    set_compute_mode,
)
from ..schemas import SwitchComputeModeRequest

router = APIRouter(tags=["compute"])


@router.get("/api/compute/modes")
def get_compute_modes() -> dict:
    """获取所有可用计算模式 + 当前模式。"""
    current = get_compute_mode()
    modes = []
    for mode_id, cfg in COMPUTE_MODES.items():
        modes.append(
            {
                "id": mode_id,
                "name": cfg["name"],
                "description": cfg["description"],
                "temperature": cfg["temperature"],
                "max_tokens": cfg["max_tokens"],
                "parallel": cfg["parallel"],
                "llm_config_id": cfg.get("llm_config_id"),
            }
        )
    return {"current": current, "modes": modes}


@router.post("/api/compute/mode")
def switch_compute_mode(payload: SwitchComputeModeRequest) -> dict:
    """切换计算模式。

    示例：{"mode": "speed"} 或 {"mode": "deep"}
    """
    mode = payload.mode.strip().lower()
    if not set_compute_mode(mode):
        valid = list(COMPUTE_MODES.keys())
        raise HTTPException(
            status_code=400,
            detail=f"无效计算模式 '{mode}'，可选: {', '.join(valid)}",
        )
    cfg = get_compute_mode_config()
    return {
        "success": True,
        "current": mode,
        "name": cfg["name"],
        "message": f"已切换至 {cfg['name']}",
    }
