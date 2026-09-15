"""桌面版打包模块（PyInstaller 一键生成 .exe）。

通过 APIRouter 挂载到主应用，提供：
- POST /api/admin/package   触发打包（前端构建 + PyInstaller）
- GET  /api/admin/download/{filename}  下载打包产物

安全校验：仅当 ENV=development 时允许调用，防止线上暴露。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import require_admin_auth
from ..schemas import PackageResponse

logger = logging.getLogger(__name__)

# 🛡️ P0-1 修复：admin 高危端点叠加鉴权依赖（loopback + token + 显式开发态）
router = APIRouter(dependencies=[Depends(require_admin_auth)])


def _run_subprocess_capture(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str]:
    """执行子进程并捕获输出（合并 stdout/stderr）。

    Returns:
        (returncode, combined_output)：returncode 非 0 表示失败。
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=isinstance(cmd, str),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except subprocess.TimeoutExpired as e:
        out = (
            e.stdout.decode("utf-8", errors="ignore")
            if isinstance(e.stdout, bytes)
            else (e.stdout or "")
        )
        out += (
            e.stderr.decode("utf-8", errors="ignore")
            if isinstance(e.stderr, bytes)
            else (e.stderr or "")
        )
        raise
    except FileNotFoundError as e:
        raise RuntimeError(f"未找到可执行文件：{cmd[0] if cmd else ''}") from e


def _tail_lines(text: str, max_lines: int = 30, max_chars: int = 2000) -> str:
    """截取输出末尾若干行，避免错误消息过长。"""
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = "...\n" + tail[-max_chars:]
    return tail


def _swap_env_for_dist(project_root: Path) -> tuple[Path, Path, Path]:
    """用 .env.dist 替换 .env，并返回 (env_file, env_dist, env_backup)。

    分发构建必须使用安全的默认配置（禁用本地 OCR、无绝对路径），
    避免目标机器继承开发机 .env 导致静默失效或 Segfault。
    """
    env_file = project_root / ".env"
    env_dist = project_root / ".env.dist"
    env_backup = project_root / ".env.local.backup"

    if env_dist.exists():
        try:
            if env_file.exists():
                env_backup.write_bytes(env_file.read_bytes())
            env_file.write_bytes(env_dist.read_bytes())
            logger.info("分发构建：已用 .env.dist 替换 .env")
        except Exception as exc:  # noqa: BLE001 - admin 打包 - 局部失败隔离
            logger.warning("替换 .env 失败: %s", exc)

    return env_file, env_dist, env_backup


def _restore_env_after_dist(env_file: Path, env_backup: Path) -> None:
    """在分发构建结束后恢复本地 .env。"""
    try:
        if env_backup.exists():
            env_backup.replace(env_file)
        elif env_file.exists():
            env_file.unlink()
    except Exception as exc:  # noqa: BLE001 - admin 打包 - 局部失败隔离
        logger.warning("恢复本地 .env 失败: %s", exc)


@router.post("/api/admin/package", response_model=PackageResponse)
def package_desktop_version() -> PackageResponse:
    """触发一键打包桌面版（PyInstaller 生成 .exe）。

    安全校验：由路由级 Depends(require_admin_auth) 保证（loopback + token + 显式开发态）。
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    web_dir = project_root / "web"
    spec_file = project_root / "paperforge.spec"
    dist_dir = project_root / "dist"
    out_exe = dist_dir / "PaperForge.exe"

    # D1/B8 修复：分发构建时使用安全的 .env.dist
    env_file, _env_dist, env_backup = _swap_env_for_dist(project_root)

    logs: list[str] = []

    try:
        # 1. 构建前端静态资源
        if web_dir.exists():
            logs.append("[1/2] npm run build...")
            try:
                rc, out = _run_subprocess_capture(
                    ["npm", "run", "build"], cwd=str(web_dir), timeout=180
                )
            except subprocess.TimeoutExpired:
                return PackageResponse(
                    success=False, message="前端构建超时（>180s），请检查 npm 是否可用"
                )
            if rc != 0:
                tail = _tail_lines(out)
                return PackageResponse(
                    success=False,
                    message=f"前端构建失败（exit={rc}）。日志：\n{tail}",
                )
            logs.append("[1/2] 前端构建完成")

        # 2. 调用 PyInstaller
        if spec_file.exists():
            logs.append("[2/2] PyInstaller 打包...")
            docker_img = "paperforge-builder"
            docker_available = False
            try:
                rc, _ = _run_subprocess_capture(
                    ["docker", "--version"], cwd=str(project_root), timeout=10
                )
                docker_available = rc == 0
            except Exception:  # noqa: BLE001 - admin 打包 - 局部失败隔离，不影响其他文件
                docker_available = False

            if docker_available:
                cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{project_root}:/app",
                    docker_img,
                    "pyinstaller",
                    "paperforge.spec",
                    "--noconfirm",
                ]
                mode = "docker"
            else:
                cmd = ["pyinstaller", str(spec_file), "--noconfirm"]
                mode = "local"

            try:
                rc, out = _run_subprocess_capture(cmd, cwd=str(project_root), timeout=300)
            except subprocess.TimeoutExpired:
                return PackageResponse(
                    success=False, message="PyInstaller 打包超时（>300s），请重试"
                )
            except RuntimeError as e:
                return PackageResponse(
                    success=False,
                    message=f"打包失败：{e}。Docker 模式失败时也支持本地 pyinstaller（需先 pip install pyinstaller）",
                )
            if rc != 0:
                tail = _tail_lines(out)
                return PackageResponse(
                    success=False,
                    message=f"PyInstaller 失败（{mode} mode, exit={rc}）。日志：\n{tail}",
                )
            logs.append(f"[2/2] PyInstaller 完成（{mode} mode）")
        else:
            return PackageResponse(success=False, message=f"未找到打包配置: {spec_file}")

        if not out_exe.exists():
            return PackageResponse(
                success=False,
                message=f"打包完成但未找到输出文件: {out_exe}。请检查 PyInstaller 日志。",
            )

        download_url = "/api/admin/download/PaperForge.exe"
        return PackageResponse(
            success=True,
            message="打包完成！\n" + "\n".join(logs),
            downloadUrl=download_url,
        )
    except Exception as e:  # noqa: BLE001 - admin 打包 - 局部失败隔离，不影响其他文件
        return PackageResponse(success=False, message=f"打包异常：{e}")
    finally:
        _restore_env_after_dist(env_file, env_backup)


@router.get("/api/admin/download/{filename}")
def download_package(filename: str):
    """下载打包产物（由路由级鉴权依赖保护）。"""
    # 防路径穿越
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    project_root = Path(__file__).resolve().parent.parent.parent
    target = project_root / "dist" / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    def iterfile():
        with open(target, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
