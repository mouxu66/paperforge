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
        f"在 {start}-{start + max_tries - 1} 范围内未找到可用端口，请检查是否有其他进程占用"
    )


def _find_free_frontend_port(
    host: str = "127.0.0.1", start: int = 5173, max_tries: int = 10
) -> int:
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
# 服务就绪检测
# ---------------------------------------------------------------------------
def _check_url_ready(url: str, timeout: int = 30) -> bool:
    """轮询 URL，服务可响应即返回 True。

    关键点：
    - 使用空的 ProxyHandler 绕过系统代理，避免 Clash/V2Ray 等软件
      拦截 127.0.0.1 请求导致误判为未就绪。
    - 即使返回 404/500 等 HTTP 错误，也说明服务已启动，应判定为就绪。
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    # 绕过所有代理，确保 localhost 请求直连
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    while time.time() < deadline:
        try:
            opener.open(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            # 任何 HTTP 响应（即使是 404/500）都说明服务已经监听并响应
            return True
        except Exception:  # noqa: BLE001 - lifespan/startup 钩子 - 启动失败应记录，不阻止
            time.sleep(0.5)
    return False


def _wait_for_backend(host: str, port: int, timeout: int = 30) -> bool:
    """轮询后端健康检查接口，就绪返回 True。"""
    return _check_url_ready(f"http://{host}:{port}/api/health", timeout)


def _wait_for_frontend(url: str, timeout: int = 30) -> bool:
    """轮询前端开发服务器，就绪返回 True。"""
    return _check_url_ready(url, timeout)


# ---------------------------------------------------------------------------
# 浏览器打开
# ---------------------------------------------------------------------------
_BROWSER_OPEN_SIGNAL = ".paperforge_url"


def _write_browser_url(url: str) -> None:
    """将待打开的 URL 写入项目根目录信号文件，供外部启动脚本兜底使用。"""
    try:
        project_root = Path(__file__).resolve().parent.parent
        (project_root / _BROWSER_OPEN_SIGNAL).write_text(url, encoding="utf-8")
    except Exception:  # noqa: BLE001 - 信号文件写入失败不应影响浏览器打开
        pass


def _open_browser_delayed(url: str, delay: float = 2.0) -> None:
    """延迟打开浏览器，并在失败时使用系统级命令兜底。

    Windows 下通过 bat 启动且 stdout 被重定向到文件时，``webbrowser.open``
    可能因句柄继承问题失效。因此先尝试 ``webbrowser.open``，失败后再用
    ``start``/``open``/``xdg-open`` 兜底。同时把 URL 写入信号文件，供
    ``start_paperforge.bat`` 的异步监视线制作为最后保险。
    """
    time.sleep(delay)
    print(f"[启动器] 正在打开浏览器：{url}", flush=True)

    # 1. 优先使用 webbrowser（跨平台、尊重用户默认浏览器）
    try:
        if webbrowser.open(url):
            return
        print("[启动器] webbrowser.open 返回 False，尝试系统命令兜底", flush=True)
    except Exception as exc:  # noqa: BLE001 - 兜底不应阻塞启动流程
        print(f"[启动器] webbrowser.open 异常：{exc}，尝试系统命令兜底", flush=True)

    # 2. 系统级命令兜底（避免 stdout 重定向导致的句柄继承问题）
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["cmd", "/c", "start", "", url],
                capture_output=True,
                check=False,
            )
            return
        elif sys.platform == "darwin":
            subprocess.run(["open", url], capture_output=True, check=False)
            return
        else:
            subprocess.run(["xdg-open", url], capture_output=True, check=False)
            return
    except Exception as exc:  # noqa: BLE001
        print(f"[启动器] 系统命令兜底也未能打开浏览器：{exc}", flush=True)

    # 3. 所有本进程打开方式均失败：写入信号文件，由启动脚本的外部监控兜底
    print("[启动器] 已写入浏览器打开信号文件，等待外部监控处理", flush=True)
    _write_browser_url(url)


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
        except Exception:  # noqa: BLE001 - lifespan/startup 钩子 - 启动失败应记录，不阻止
            pass
    else:
        try:
            import signal as sig_module

            os.killpg(os.getpgid(proc.pid), sig_module.SIGTERM)
        except Exception:  # noqa: BLE001 - lifespan/startup 钩子 - 启动失败应记录，不阻止
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - lifespan/startup 钩子 - 启动失败应记录，不阻止
                proc.kill()


# ---------------------------------------------------------------------------
# 前端自动构建（解决"改完 web/src 重启看不到新代码"的经典坑）
# ---------------------------------------------------------------------------
def _get_source_mtime(web_dir: Path) -> float:
    """获取 web 前端源码最大修改时间。

    扫描范围：
    - 根配置：web/index.html, package.json, vite.config.ts, tsconfig*.json
    - web/src 下除测试文件外的所有源文件

    用于和 web/dist/index.html 的 mtime 对比，决定是否需要重新 build。
    排除配套：node_modules / __tests__ / *.test.ts(x) 等不会进生产产物的文件。
    """
    max_mtime = 0.0
    ROOT_TARGETS = (
        "index.html",
        "package.json",
        "vite.config.ts",
        "tsconfig.json",
        "tsconfig.node.json",
    )
    for name in ROOT_TARGETS:
        f = web_dir / name
        if f.exists():
            max_mtime = max(max_mtime, f.stat().st_mtime)

    src_dir = web_dir / "src"
    if src_dir.exists():
        for root, dirs, files in os.walk(src_dir):
            # 原地修剪：跳过测试目录与 node_modules
            dirs[:] = [d for d in dirs if d not in {"__tests__", "node_modules"}]
            for fname in files:
                if fname.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
                    continue
                try:
                    mt = (Path(root) / fname).stat().st_mtime
                except OSError:
                    continue
                if mt > max_mtime:
                    max_mtime = mt
    return max_mtime


def _auto_build_frontend(web_dir: Path) -> bool:
    """按需自动构建 web 前端（npm install + npm run build）。

    触发条件（满足任意一条即触发 build）：
    1. web/dist/index.html 不存在（首次构建）
    2. 源码 mtime > dist/index.html mtime + 1.0 秒容差

    依赖处理：node_modules 缺失时，先跑 npm install。

    错误策略：构建失败 → 输出醒目红色错误并返回 False（上层应退出 launcher
    以避免使用过期的 dist 误导用户）。

    关闭：环境变量 PAPERFORGE_NO_AUTO_REBUILD=1 时跳过。

    Args:
        web_dir: web/ 目录绝对路径。
    Returns:
        True 表示无需重建或构建成功；False 表示构建失败。
    """
    # 绝对导入：launcher 既可能被 `python -m mock_api.launcher` 调用，
    # 也可能被 `python mock_api/launcher.py` 当脚本直接运行（此时 __package__ 为空，
    # 相对导入 `from .settings` 会抛 ImportError）。项目根已由
    # _ensure_project_root_on_path() 加入 sys.path，故用绝对导入兼容两种调用方式。
    from mock_api.settings import get_settings

    if get_settings().no_auto_rebuild:
        print("\033[33m[构建] 已禁用自动重建（PAPERFORGE_NO_AUTO_REBUILD=1）\033[0m", flush=True)
        return True

    dist_index = web_dir / "dist" / "index.html"
    node_modules = web_dir / "node_modules"
    # Windows 下必须显式 .cmd 后缀；shell=False 时 Python 不会从 PATHEXT 推断。
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

    # 1. node_modules 缺失 → 先 npm install
    if not node_modules.exists():
        print(
            "\n\033[33m[构建] web/node_modules 不存在，开始安装依赖 (npm install)...\033[0m",
            flush=True,
        )
        # 不捕获 stdout/stderr：直接继承父进程，让 npm 的 progress bar / 颜色实时呈现
        try:
            install_rc = subprocess.run([npm_cmd, "install"], cwd=str(web_dir)).returncode
        except FileNotFoundError:
            print("\n\033[31m" + "=" * 64)
            print("[构建] FATAL: 找不到 npm 命令！请先安装 Node.js 18+ 并加入 PATH。")
            print("=" * 64 + "\033[0m\n", flush=True)
            return False
        if install_rc != 0:
            print("\n\033[31m" + "=" * 64)
            print("[构建] FATAL: npm install 失败，无法启动前端！")
            print("=" * 64 + "\033[0m\n", flush=True)
            return False

    # 2. 决策是否需要 build
    need_build = False
    reason = ""
    if not dist_index.exists():
        need_build = True
        reason = "首次构建（web/dist 缺失）"
    else:
        src_mtime = _get_source_mtime(web_dir)
        dist_mtime = dist_index.stat().st_mtime
        # 2.0 秒容差：覆盖 FAT / 部分 Windows 文件系统的 2 秒 mtime 粒度极限，
        # 同时容忍 Git checkout / rsync 导致的轻微抖动。
        if src_mtime > dist_mtime + 2.0:
            need_build = True
            src_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(src_mtime))
            dist_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(dist_mtime))
            reason = f"检测到源码更新（src {src_ts} > dist {dist_ts}）"

    # 3. 执行 npm run build
    if need_build:
        print(f"\n\033[36m[构建] {reason}，正在重新构建前端 (npm run build)...\033[0m", flush=True)
        print(
            "\033[90m[构建] 提示：会运行 tsc 类型检查 + vite 打包，耗时约 20-60 秒\033[0m\n",
            flush=True,
        )
        # 完整 npm 输出落盘，便于失败时定位（尤其 Windows 下窗口一闪而过）
        build_log = web_dir / "paperforge_build.log"
        try:
            with open(build_log, "w", encoding="utf-8") as blog:
                blog.write(f"=== npm run build @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                blog.flush()
                build_rc = subprocess.run(
                    [npm_cmd, "run", "build"],
                    cwd=str(web_dir),
                    stdout=blog,
                    stderr=subprocess.STDOUT,
                ).returncode
        except FileNotFoundError:
            print("\n\033[31m" + "=" * 64)
            print("[构建] FATAL: 找不到 npm 命令！请先安装 Node.js 18+ 并加入 PATH。")
            print("=" * 64 + "\033[0m\n", flush=True)
            return False
        if build_rc != 0:
            print("\n\033[31m" + "=" * 64)
            print("[构建] 前端构建失败（npm run build 返回非零）。")
            print(f"        完整错误已写入：{build_log}")
            print("        可将该文件内容发给我定位问题。")
            if dist_index.exists():
                # 已有旧 dist：不硬崩，先用旧代码启动，避免"点一下就崩"的体验
                print("\033[33m[构建] 警告：将使用已有的 web/dist 启动（可能是旧代码）。\033[0m")
                print("\033[33m        请修复上面的构建错误后重启以获得最新代码。\033[0m")
                print("=" * 64 + "\033[0m\n", flush=True)
                return True
            print("        且 web/dist 不存在，无法启动前端。")
            print("=" * 64 + "\033[0m\n", flush=True)
            return False
        print("\n\033[32m" + "=" * 64)
        print("[构建] [OK] 前端构建成功！将用最新代码启动服务。")
        print("=" * 64 + "\033[0m\n", flush=True)

    return True


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> int:
    # 从集中式配置读取绑定地址（默认 127.0.0.1，设 PAPERFORGE_BIND_HOST=0.0.0.0 可对外）
    from mock_api.settings import get_settings

    host = get_settings().bind_host

    # 修复 Windows GBK 控制台 / 重定向日志无法编码非 GBK 字符（如 ✓、→）导致启动崩溃
    # 将 stdout/stderr 强制为 UTF-8，使中文与符号都能安全写入日志文件
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 0. 计算项目路径（提前以便后面预构建步骤复用）
    project_root = Path(__file__).resolve().parent.parent
    web_dir = project_root / "web"

    # 1. 前端自动构建（源码更新 → 自动 rebuild dist，实现"改完代码重启即生效"）
    #    放在端口探测之前，错误能提前快速失败，避免端口浪费。
    if web_dir.exists():
        if not _auto_build_frontend(web_dir):
            return 1  # 构建失败：避免被过期 dist 误导，立即退出

    # 2. 自动寻找可用后端端口（8770 → 8771 → ...）
    try:
        port = _find_free_port(host, start=8770)
    except RuntimeError as e:
        print(f"[启动器] {e}")
        return 1

    # 3. 检测前端模式（dist 由上面步骤保证最新，不存在 = 走 Vite dev）
    web_dist = project_root / "web" / "dist"

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
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        print(f"[启动器] 正在启动前端开发服务器（端口 {fe_port}）...")

        if sys.platform == "win32":
            frontend_proc = subprocess.Popen(
                [npm_cmd, "run", "dev", "--", "--port", str(fe_port), "--strictPort"],
                cwd=str(web_dir),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            frontend_proc = subprocess.Popen(
                [npm_cmd, "run", "dev", "--", "--port", str(fe_port), "--strictPort"],
                cwd=str(web_dir),
                start_new_session=True,
            )

        # 注册退出清理
        atexit.register(lambda: _kill_process_tree(frontend_proc))
    else:
        # 仅 API 模式（未找到 web/ 目录）
        frontend_url = f"http://{host}:{port}/docs"
        mode = "api-only"

    # 4. 打印端口信息
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

    # 5. 延迟打开浏览器（在新线程中）
    def _browser_task() -> None:
        # 无论检测是否超时，都尝试打开浏览器；超时只作为警告，避免用户
        # 因网络代理、健康检查偶发失败等原因完全无法自动打开页面。
        if mode.startswith("dev"):
            if not _wait_for_frontend(frontend_url, timeout=30):
                print(
                    f"[启动器] 前端未在 30s 内就绪，仍尝试打开浏览器：{frontend_url}",
                    flush=True,
                )
            time.sleep(1)
            _open_browser_delayed(frontend_url, delay=0)
        elif mode == "standalone（生产模式）":
            if not _wait_for_backend(host, port, timeout=30):
                print(
                    f"[启动器] 后端未在 30s 内就绪，仍尝试打开浏览器：{frontend_url}",
                    flush=True,
                )
            _open_browser_delayed(frontend_url, delay=1.0)
        else:
            # API 模式
            if not _wait_for_backend(host, port, timeout=30):
                print(
                    "[启动器] API 后端未在 30s 内就绪，仍尝试打开文档页面",
                    flush=True,
                )
            _open_browser_delayed(frontend_url, delay=1.0)

    threading.Thread(target=_browser_task, daemon=True).start()

    # 6. 启动后端（阻塞主线程）
    import uvicorn

    try:
        uvicorn.run(
            "mock_api.main:app",
            host=host,
            port=port,
            reload=False,
            log_level="info",
            timeout_keep_alive=180,  # 3 分钟：确保 AI 评估等长耗时请求不被切断
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
