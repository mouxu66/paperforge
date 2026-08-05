"""临时文件管理工具 —— 上下文管理器 + 装饰器，确保退出时自动清理。

v4.2 新增（2026-07-27）：
- ``managed_tempdir``: 类 TemporaryDirectory 的上下文管理器，使用 ``pathlib.Path``。
- ``managed_tempdir`` 也可用作装饰器，将临时目录路径注入被装饰函数的第一个参数。
- ``safe_write`` / ``safe_remove``: pathlib.Path 安全写入/删除工具函数，
  替代全仓库中 ``os.remove`` / ``open().write()`` 的裸调用。

用法::

    # 上下文管理器
    with managed_tempdir() as tmp:
        img = tmp / "figure.png"
        img.write_bytes(png_data)

    # 装饰器（注入 tmpdir 作为第一个参数）
    @managed_tempdir(prefix="fig_")
    def extract_figures(tmpdir: Path, paper_id: str) -> list[dict]:
        ...

    # 安全写入/删除
    safe_write(path, data)
    safe_remove(path)
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger(__name__)


class managed_tempdir:  # noqa: N801 - 类名用小写下划线风格以匹配上下文管理器习惯
    """双模工具：上下文管理器 + 装饰器工厂。

    - 作为上下文管理器：``with managed_tempdir() as tmp: ...``
    - 作为装饰器：``@managed_tempdir(prefix="fig_")``

    退出时自动执行 ``shutil.rmtree`` 清理。
    """

    def __init__(
        self,
        prefix: str = "pf_tmp_",
        suffix: str = "",
        *,
        base_dir: Path | str | None = None,
    ):
        self._prefix = prefix
        self._suffix = suffix
        self._base_dir = base_dir
        self._path: Path | None = None

    def __enter__(self) -> Path:
        target = Path(self._base_dir) if self._base_dir else None
        tmp_name = tempfile.mkdtemp(
            prefix=self._prefix,
            suffix=self._suffix,
            dir=str(target) if target else None,
        )
        self._path = Path(tmp_name)
        logger.debug("managed_tempdir 创建: %s", self._path)
        return self._path

    def __exit__(self, *args: Any) -> None:
        if self._path is not None and self._path.exists():
            try:
                shutil.rmtree(self._path, ignore_errors=True)
            except Exception:
                logger.warning(
                    "managed_tempdir 清理失败（部分文件残留）: %s",
                    self._path,
                    exc_info=True,
                )
            finally:
                self._path = None

    def __call__(self, func: F) -> F:
        """作为装饰器使用：注入 tmpdir 为第一个参数。"""
        from functools import wraps

        prefix = self._prefix
        suffix = self._suffix
        base_dir = self._base_dir

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with managed_tempdir(prefix=prefix, suffix=suffix, base_dir=base_dir) as tmpdir:
                return func(tmpdir, *args, **kwargs)

        return wrapper  # type: ignore[return-value]


def safe_write(path: Path, data: bytes) -> bool:
    """安全写入文件（使用 pathlib.Path）。

    与裸 ``path.write_bytes(data)`` 不同，本函数提供：
    - 父目录自动创建
    - 写入失败时记录错误日志但不抛异常

    Returns:
        True 表示写入成功。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except Exception:
        logger.warning("写入文件失败: %s", path, exc_info=True)
        return False


def safe_remove(path: Path) -> bool:
    """安全删除文件（使用 pathlib.Path）。

    与裸 ``os.remove`` 不同，本函数静默吞下文件不存在和权限错误。
    用于替代全仓库中 ``os.remove`` / ``os.unlink`` 的裸调用。

    Returns:
        True 表示文件不存在或删除成功。
    """
    try:
        if path.exists():
            path.unlink()
        return True
    except Exception:
        logger.warning("删除文件失败: %s", path, exc_info=True)
        return False
