"""健康检查路由（存活探针 / 就绪探针 / 向后兼容）。

零依赖抽取自 mock_api/main.py，验证「APIRouter + app.py include_router」模式。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from .. import crud
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


# 🛡️ P2-8 修复：health 拆分为 liveness + readiness
# 原版 /api/health 单探针既查存活又查 DB，DB 慢时存活探针也失败
# 导致编排器误杀。拆为 live（仅进程存活）+ ready（含 DB 检查）。
@router.get("/api/health/live")
def health_live() -> dict:
    """Liveness probe：进程存活即返回 ok（不检查依赖）。"""
    return {"status": "ok", "service": "paperforge-mock"}


@router.get("/api/health/ready")
def health_ready(db: Session = Depends(get_db)):
    """Readiness probe：检查数据库连接可用，不可用时返回 503。"""
    try:
        stats = crud.get_stats(db)
        return {"status": "ok", "service": "paperforge-mock", "papers": stats.totalPapers}
    except Exception:
        logger.exception("Readiness probe failed: DB query error")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "paperforge-mock"},
        )


@router.get("/api/health")  # 向后兼容
def health_compat(db: Session = Depends(get_db)):
    """向后兼容：等同于 /api/health/ready。"""
    return health_ready(db)
