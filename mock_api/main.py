"""PaperForge 主入口模块。

所有业务路由已迁至 mock_api/routers/ 下的独立模块并在 app.py 直挂（PR1→PR15）：
  PR1  health       PR2  models       PR3  qwen        PR4  compute
  PR5  chat         PR6  upload       PR7  arxiv       PR8  tasks
  PR9  zotero       PR10 admin        PR11 depth        PR12 reflection
  PR13 reports      PR14 writing      PR15 papers → app.py 直挂，删除 api_router

本文件仅保留：日志初始化、应用工厂、SPA catch-all。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 日志落盘 ──────────────────────────────────────────
import logging.handlers as _logging_handlers

from .database import DATA_DIR as _data_dir

_logs_dir = _data_dir / "logs"
try:
    _logs_dir.mkdir(parents=True, exist_ok=True)
    _file_handler = _logging_handlers.RotatingFileHandler(
        _logs_dir / "paperforge.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    _root_logger = logging.getLogger()
    if not any(isinstance(h, _logging_handlers.RotatingFileHandler) for h in _root_logger.handlers):
        _root_logger.addHandler(_file_handler)
except Exception as _log_exc:
    logger.warning("日志文件初始化失败（不影响运行）: %s", _log_exc)

# ── 应用工厂 + SPA ──
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .app import create_app
from .settings import get_settings

app = create_app()

_web_dist = get_settings().web_dist or str(Path(__file__).resolve().parent.parent / "web" / "dist")
if Path(_web_dist).exists():
    _assets_dir = os.path.join(_web_dist, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        index_path = os.path.join(_web_dist, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Frontend not built")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mock_api.main:app", host="127.0.0.1", port=8770, reload=False)
