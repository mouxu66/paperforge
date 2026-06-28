"""PaperForge 统一启动器。

双击运行即可同时启动后端 + 前端：
1. 自动寻找可用端口（8770 → 8771 → 8772 → ...）
2. 检测 web/dist/：
   - 存在 → 生产模式，FastAPI 直接托管静态文件
   - 不存在 → 开发模式，自动启动 Vite 开发服务器
3. 浏览器自动打开对应端口
4. 关闭窗口 / Ctrl+C 时自动终止前端子进程

使用方式：
    python mock_api/launcher.py
或双击该文件运行。
"""
from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# 确保项目根目录在 sys.path 中（双击运行时的路径修正）
# ---------------------------------------------------------------------------
def _ensure_project_root_on_path() -> None:
    """将项目根目录加入 sys.path，避免 `import mock_api` 失败。

    背景：launcher.py 位于 mock_api/ 子目录。直接用绝对路径执行时，Python 会把
    mock_api/ 加到 sys.path[0]，导致 import mock_api 解析为 mock_api/mock_api。
    把项目根（launcher.py 的父目录的父目录）放在 sys.path 最前面可解决。
    """
    script_path = (
        Path(sys.executable).resolve()
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve()
    )
    project_root = script_path.parent.parent  # mock_api/launcher.py -> project_root
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


_ensure_project_root_on_path()


# ---------------------------------------------------------------------------
# 端口探测
# ---------------------------------------------------------------------------
def _is_port_free(host: str, port: int) -> bool:
    """检测端口是否可用（bind 成功且无人 LISTEN）。

    Windows 上存在「孤儿 socket」：bind 成功但 uvicorn bind 失败，
    所以 bind 成功后再用 connect 确认没人正在 LISTEN。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    # bind 成功后确认没人 LISTEN（connect 127.0.0.1:port）
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect((host, port))
        except (OSError, ConnectionRefusedError):
            # 连不上说明没人 LISTEN → 端口可用
            return True
        # 居然连上了 → 已被占用
        return False


def _find_free_port(host: str, start: int = 8770, max_tries: int = 20) -> int:
    """从 start 开始递增寻找可用端口。

    Args:
        host: 绑定地址
        start: 起始端口
        max_tries: 最多尝试次数
    Returns:
        第一个可用端口号
    Raises:
        RuntimeError: 所有端口都被占用
    """
    for port in range(start, start + max_tries):
        if _is_port_free(host, port):
            return port
    raise RuntimeError(
        f"在 {start}-{start + max_tries - 1} 范围内未找到可用端口，"
        f"请检查是否有其他进程占用"
    )


def _find_free_frontend_port(host: str = "127.0.0.1", start: int = 5173, max_tries: int = 10) -> int:
    """探测前端可用端口（5173 → 5174 → ...）。

    前端端口只需确认没人 LISTEN（不需要 bind），因为 Vite 会自己 bind。
    """
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect((host, port))
            except (OSError, ConnectionRefusedError):
                return port  # 连不上 → 可用
    return start  # 兜底：让 Vite 自己处理冲突


# ---------------------------------------------------------------------------
# 后端就绪检测
# ---------------------------------------------------------------------------
def _wait_for_backend(host: str, port: int, timeout: int = 30) -> bool:
    """轮询后端健康检查接口，就绪返回 True。"""
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{host}:{port}/api/health"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _wait_for_frontend(url: str, timeout: int = 30) -> bool:
    """轮询前端开发服务器，就绪返回 True。"""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# 浏览器打开
# ---------------------------------------------------------------------------
def _open_browser_delayed(url: str, delay: float = 2.0) -> None:
    """延迟打开浏览器（等待服务就绪）。"""
    time.sleep(delay)
    print(f"[启动器] 正在打开浏览器：{url}")
    webbrowser.open(url)


# ---------------------------------------------------------------------------
# 子进程管理
# ---------------------------------------------------------------------------
def _kill_process_tree(proc: subprocess.Popen) -> None:
    """终止进程树（Windows 兼容）。

    Windows 上 shell=True 启动的进程需要用 taskkill /T 递归终止子进程。
    """
    if proc is None or proc.poll() is not None:
        return

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            import signal as sig_module
            os.killpg(os.getpgid(proc.pid), sig_module.SIGTERM)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> int:
    host = "127.0.0.1"

    # 1. 自动寻找可用后端端口（8770 → 8771 → ...）
    try:
        port = _find_free_port(host, start=8770)
    except RuntimeError as e:
        print(f"[启动器] {e}")
        return 1

    # 2. 检测前端模式
    project_root = Path(__file__).resolve().parent.parent
    web_dist = project_root / "web" / "dist"
    web_dir = project_root / "web"

    frontend_proc: subprocess.Popen | None = None
    frontend_url = ""
    mode = ""

    if web_dist.exists():
        # 生产模式：FastAPI 托管静态文件（main.py 已处理 mount）
        os.environ["PAPERFORGE_WEB_DIST"] = str(web_dist)
        frontend_url = f"http://{host}:{port}/"
        mode = "standalone（生产模式）"
    elif web_dir.exists():
        # 开发模式：启动 Vite 开发服务器
        os.environ["VITE_API_PORT"] = str(port)
        fe_port = _find_free_frontend_port(start=5173)
        frontend_url = f"http://localhost:{fe_port}/"
        mode = "dev（开发模式）"

        # 启动 npm run dev，指定端口
        cmd_str = f"npm run dev -- --port {fe_port} --strictPort"
        print(f"[启动器] 正在启动前端开发服务器（端口 {fe_port}）...")

        if sys.platform == "win32":
            frontend_proc = subprocess.Popen(
                cmd_str,
                cwd=str(web_dir),
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            frontend_proc = subprocess.Popen(
                cmd_str,
                cwd=str(web_dir),
                shell=True,
                start_new_session=True,
            )

        # 注册退出清理
        atexit.register(lambda: _kill_process_tree(frontend_proc))
    else:
        # 仅 API 模式（未找到 web/ 目录）
        frontend_url = f"http://{host}:{port}/docs"
        mode = "api-only"

    # 3. 打印端口信息
    print()
    print("=" * 55)
    print("  PaperForge 启动器")
    print("=" * 55)
    print(f"  模式:     {mode}")
    print(f"  后端运行在 http://{host}:{port}")
    if mode.startswith("dev"):
        print(f"  前端运行在 {frontend_url}")
    elif mode == "standalone（生产模式）":
        print(f"  前端运行在 {frontend_url}（由后端托管）")
    else:
        print(f"  API 文档:  {frontend_url}")
    print(f"  API 文档:  http://{host}:{port}/docs")
    print("=" * 55)
    print()

    # 4. 延迟打开浏览器（在新线程中）
    def _browser_task() -> None:
        if mode.startswith("dev"):
            # 开发模式：等前端 Vite 就绪后再开浏览器
            if _wait_for_frontend(frontend_url, timeout=30):
                time.sleep(1)
                _open_browser_delayed(frontend_url, delay=0)
            else:
                print(f"[启动器] 前端未在 30s 内就绪，请手动访问 {frontend_url}")
        elif mode == "standalone（生产模式）":
            # 生产模式：等后端就绪
            if _wait_for_backend(host, port, timeout=30):
                _open_browser_delayed(frontend_url, delay=1.0)
            else:
                print(f"[启动器] 后端未在 30s 内就绪，请手动访问 {frontend_url}")
        else:
            # API 模式
            if _wait_for_backend(host, port, timeout=30):
                _open_browser_delayed(frontend_url, delay=1.0)

    threading.Thread(target=_browser_task, daemon=True).start()

    # 5. 启动后端（阻塞主线程）
    import uvicorn

    try:
        uvicorn.run(
            "mock_api.main:app",
            host=host,
            port=port,
            reload=False,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n[启动器] 收到中断信号，正在停止...")
    finally:
        if frontend_proc is not None:
            print("[启动器] 正在关闭前端进程...")
            _kill_process_tree(frontend_proc)
            print("[启动器] 前端进程已终止。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
