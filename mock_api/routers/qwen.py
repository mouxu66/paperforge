"""Qwen/llama-server 托管状态 + VRAM 调度器运行时状态。

PR3 抽取：从 main.py 搬迁 qwen_status 路由（单例 lazy import 验证抽取无障碍）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..settings import get_settings

router = APIRouter(tags=["qwen"])


@router.get("/api/qwen/status")
def qwen_status() -> dict:
    """Qwen/llama-server 托管状态 + 最近切换事件，供前端展示「模型加载中」提示。

    Returns:
        {
          "managed": bool,            # 是否由 PaperForge 托管（False=复用外部实例）
          "ready": bool,              # llama-server 是否就绪
          "vram_exclusive": bool,     # 是否 OCR/Qwen 互斥
          "event": {                  # 前端轮询用：最近一次切换/加载事件
            "kind": "ready|loading|switching|idle",
            "eta_seconds": int,
            "state": "idle|qwen_active|ocr_active"
          }
        }
    """
    from ..llama_server_manager import get_llama_server_manager
    from ..vram_scheduler import get_vram_scheduler

    mgr = get_llama_server_manager()
    sched = get_vram_scheduler()
    return {
        "managed": mgr._managed,
        "ready": mgr.is_ready(),
        "vram_exclusive": get_settings().vram_exclusive,
        "event": sched.get_status(),
    }


@router.get("/api/qwen/events")
def qwen_events(limit: int = Query(20, ge=1, le=100)) -> dict:
    """获取最近的 VRAM 调度事件。"""
    from ..vram_scheduler import get_vram_scheduler

    return {"events": get_vram_scheduler().get_events(limit)}


@router.post("/api/qwen/events/clear")
def clear_qwen_events() -> dict:
    """清空当前进程内的 VRAM 调度事件。"""
    from ..vram_scheduler import get_vram_scheduler

    get_vram_scheduler().clear_events()
    return {"success": True}
