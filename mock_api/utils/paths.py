"""路径工具函数。

集中管理项目中的磁盘路径，避免 pdf_parser、routers、workers 等模块互相耦合。
"""

from __future__ import annotations

from pathlib import Path

# PDF 存储目录（相对于项目根）
UPLOADS_DIR_NAME = "uploads"


def _get_uploads_dir() -> Path:
    """获取 PDF 存储目录（项目根下的 uploads/），不存在则创建。"""
    project_root = Path(__file__).resolve().parent.parent.parent
    uploads = project_root / UPLOADS_DIR_NAME
    uploads.mkdir(parents=True, exist_ok=True)
    return uploads
