"""PaperForge 纯 API 服务启动脚本（供外部调用方使用）。

与 `mock_api/launcher.py` 的差异：
- 不启动前端、不自动构建 web/dist、不打开浏览器
- 默认绑定 127.0.0.1（仅本机；需对外暴露时显式 --host 0.0.0.0）
- 自动设置 PAPERFORGE_ALLOW_REMOTE=1
- 启动前显式校验安全配置：若未设置 PAPERFORGE_API_TOKEN 且无任何 API Key，
  会输出 WARNING 提醒（不阻止启动，便于本地调试）

用法：
    python scripts/serve_api.py                  # 127.0.0.1:8770
    python scripts/serve_api.py --host 127.0.0.1 # 仅本机
    python scripts/serve_api.py --port 9000
    python scripts/serve_api.py --workers 4       # 多 worker（生产部署推荐）
    python scripts/serve_api.py --reload          # 开发热重载

部署建议：
- 生产：用 gunicorn + uvicorn.worker.UvicornWorker 替代 --workers
- 反代：用 Nginx/Caddy 做 HTTPS 终止 + IP 白名单
- 限流：本系统已实现 per-key 令牌桶；额外可在反代层加全局限流
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保项目根在 sys.path 上（python scripts/serve_api.py 直接运行场景）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _print_security_warnings() -> None:
    """启动前打印安全提醒（不阻止启动，便于运维感知风险）。"""
    from mock_api.settings import get_settings

    settings = get_settings()

    # 1. 未配置全局 token 且无 API Key → WARNING
    has_global_token = bool(settings.api_token)
    has_any_key = False
    try:
        from mock_api.api_keys import get_api_key_service

        keys = get_api_key_service().list_all(enabled_only=True)
        has_any_key = len(keys) > 0
    except Exception as exc:  # noqa: BLE001 - 启动前提醒，不应阻塞
        print(f"[安全] 检查现有 API Key 失败: {exc}", file=sys.stderr)

    if not has_global_token and not has_any_key:
        print("\n" + "!" * 64, file=sys.stderr)
        print(
            "[安全 WARNING] 当前未配置 PAPERFORGE_API_TOKEN，且无任何已启用的 API Key。\n"
            "               任何能访问本端口的调用方都将拥有超级管理员权限（无鉴权）。\n"
            "               建议立即：\n"
            "               1) 设置 PAPERFORGE_API_TOKEN=<强随机字符串>，或\n"
            "               2) 启动后通过 admin 端点创建 API Key（POST /api/system/api-keys）",
            file=sys.stderr,
        )
        print("!" * 64 + "\n", file=sys.stderr)
    elif has_global_token and not has_any_key:
        print(
            "[安全] 已启用全局 token 鉴权；建议为不同调用方创建独立 API Key "
            "（POST /api/system/api-keys），避免共享 token。",
            file=sys.stderr,
        )

    # 2. is_dev_env=True 时提醒（admin 端点会暴露）
    if settings.is_dev_env:
        print(
            "[安全 WARNING] IS_DEV_ENV=development 已开启，admin 端点（/api/admin/*）"
            "将在所有接口上可用。生产部署建议关闭。",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PaperForge 纯 API 服务（对外暴露模式）"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="绑定地址（默认 127.0.0.1，仅本机；设 0.0.0.0 可对外暴露）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="绑定端口（默认读 PAPERFORGE_BIND_PORT 或 8770）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="uvicorn worker 数量（生产推荐 = CPU 核数）",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="热重载（仅开发用，与 --workers >1 互斥）",
    )
    args = parser.parse_args()

    # 纯 API 模式必须开启远端访问（覆盖 settings.allow_remote=False 默认值）
    os.environ.setdefault("PAPERFORGE_ALLOW_REMOTE", "1")

    # 修复 Windows GBK 控制台对非 GBK 字符的编码问题
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 端口优先级：CLI > 环境变量 > 默认 8770
    from mock_api.settings import get_settings, reset_settings

    reset_settings()
    settings = get_settings()
    port = args.port or settings.bind_port
    host = args.host

    _print_security_warnings()

    # 禁用 launcher 的前端自动构建（避免在 API 模式下触发 npm install）
    os.environ.setdefault("PAPERFORGE_NO_AUTO_REBUILD", "1")

    print()
    print("=" * 55)
    print("  PaperForge API Server")
    print("=" * 55)
    print(f"  监听:     http://{host}:{port}")
    print(f"  鉴权:     {'启用' if settings.auth_enabled else '关闭'}")
    print(f"  限流:     {settings.api_key_rate_limit_default}/min/key (默认)")
    print(f"  审计:     {'启用' if settings.api_key_audit_enabled else '关闭'}")
    print(f"  API 文档: http://{host}:{port}/docs")
    print(f"  Workers:  {args.workers}")
    print("=" * 55)
    print()

    import uvicorn

    # reload 模式下 workers 必须为 1
    workers = 1 if args.reload else args.workers

    try:
        uvicorn.run(
            "mock_api.app:create_app",
            factory=True,
            host=host,
            port=port,
            workers=workers,
            reload=args.reload,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n[serve_api] 收到中断信号，退出。")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
