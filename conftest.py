"""Rootdir conftest —— 所有 pytest 测试都得先经过这里。

作用：为 `from mock_api.depth_eval_v4 import ...` 类包导入提供兜底 sys.path 注入。

何时生效（pytest 体系）
- pytest >= 7.0：pytest.ini `pythonpath = .` 已做等价事，但只在 pytest 入口有效；
  这里再多插一次等价条目是幂等的。
- pytest < 7.0：`pythonpath` 是未知 key，pytest 会忽略；这里兜底注入。
- PyCharm / VSCode 通过 pytest runner 运行测试：会先 import 此 conftest。
- pytest 跨工作目录运行（`cd mock_api && pytest`、`cd anywhere && pytest
  tests/...`）：pytest 仍把 rootdir 解析为项目根，conftest 仍会被发现。

何时不生效（**重要**——这是边界）
- `python -m unittest` / `python -m unittest discover` —— unittest 不读
  conftest.py，此时 `import mock_api` 仍会 ModuleNotFoundError。
- `python -m doctest` / `python -m pytest --doctest-modules` 之外的手工 doctest：
  同样不读 conftest.py。
- `python setup.py test`（已废弃，但 CI 偶有）—— setuptools 不读 conftest.py。
- 这种场景统一推荐 `pip install -e .` 让 `mock_api` 进 site-packages，
  从而全局可 import，独立于运行器。

实现要点
- 用 pathlib.Path.resolve() 做规范化后比较 sys.path，避免 Windows 上
  `\\` vs `/`、symlink / reparse point 导致的 string 不等而误判重复插入。
- 重复条目不会让 mock_api/__init__.py 被多次执行（importlib 缓存），
  但会让 sys.path 多一条等价路径——所以仍尽量用规范化判等去重。
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

# ── 🛡️ 三层防线：防止原生资源检测导致 C 级进程崩溃 ──
# 防线 1（conftest）：所有 pytest session 默认禁用资源检测。
#   setdefault 仅当环境变量未设时生效，允许特定测试显式 unset 后重跑。
# 防线 2（compute_mode.detect_resources）：检测 Windows Store Python 并直接返回零值。
# 防线 3（compute_mode.detect_gpu_memory）：subprocess 硬化（CREATE_NO_WINDOW + startupinfo）。
# 详见 incident-compute-mode-segfault-2026-07-07.md
os.environ.setdefault("PAPERFORGE_DISABLE_RESOURCE_DETECT", "1")

# 测试会话默认关闭全局鉴权中间件（TestClient 不来自真实 loopback），
# 安全相关测试会显式启用并校验行为。
os.environ.setdefault("PAPERFORGE_AUTH_ENABLED", "0")

_PROJECT_ROOT = Path(__file__).resolve().parent
_EXISTING_ROOTS = {Path(p).resolve() for p in sys.path if p}

if _PROJECT_ROOT not in _EXISTING_ROOTS:
    sys.path.insert(0, str(_PROJECT_ROOT))
