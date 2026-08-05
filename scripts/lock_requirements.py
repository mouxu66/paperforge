#!/usr/bin/env python3
"""生成 requirements.lock 文件（#10 构建分发治理）。

基于 requirements.txt 解析版本号并锁定为精确版本。
用法：
    python scripts/lock_requirements.py          # 从已安装包解析版本
    python scripts/lock_requirements.py --freeze  # 等价于 pip freeze 过滤

产物：requirements.lock（与 requirements.txt 同目录）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="生成 requirements.lock")
    parser.add_argument("--freeze", action="store_true", help="使用 pip freeze 锁定全部已安装包")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    req_txt = project_root / "mock_api" / "requirements.txt"
    lock_file = project_root / "mock_api" / "requirements.lock"

    if not req_txt.exists():
        print(f"错误: {req_txt} 不存在")
        sys.exit(1)

    if args.freeze:
        # 方式 A：pip freeze → 过滤 requirements.txt 顶层依赖
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True, text=True, check=True,
        )
        all_packages = {line.split("==")[0].lower(): line.strip() for line in result.stdout.splitlines() if "==" in line}

        # 读取 requirements.txt 中的顶层包名
        top_packages = set()
        for line in req_txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkg = line.split("==")[0].split(">=")[0].split("~=")[0].split("<")[0].strip().lower()
                if pkg:
                    top_packages.add(pkg)

        locked = []
        for pkg in sorted(top_packages):
            if pkg in all_packages:
                locked.append(all_packages[pkg])
            else:
                print(f"[WARN] {pkg} not found in pip freeze (may not be installed)")

        lock_content = (
            f"# PaperForge requirements.lock — 自动生成于 pip freeze\n"
            f"# 顶层依赖来自: {req_txt.relative_to(project_root)}\n"
            f"# 构建命令: python {Path(__file__).relative_to(project_root)}\n\n"
            f"# 顶层依赖（含传递依赖）:\n"
            + "\n".join(locked)
            + "\n"
        )
    else:
        # 方式 B：逐个查询已安装版本（pip show）
        locked = []
        for line in req_txt.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                locked.append(line)
                continue
            pkg_name = line.split("==")[0].split(">=")[0].split("~=")[0].split("<")[0].strip()
            # Strip extras suffix: uvicorn[standard] -> uvicorn
            pkg_name = pkg_name.split("[")[0].strip()
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", pkg_name],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                for info_line in result.stdout.splitlines():
                    if info_line.startswith("Version:"):
                        version = info_line.split(":")[1].strip()
                        locked.append(f"{pkg_name}=={version}")
                        break
            else:
                print(f"[WARN] {pkg_name} not installed, keeping original")
                locked.append(line)

        lock_content = (
            f"# PaperForge requirements.lock — 自动生成\n"
            f"# 原始文件: {req_txt.relative_to(project_root)}\n"
            f"# 生成命令: python {Path(__file__).relative_to(project_root)}\n\n"
            + "\n".join(locked)
            + "\n"
        )

    lock_file.write_text(lock_content, encoding="utf-8")
    print(f"OK requirements.lock generated: {lock_file}")


if __name__ == "__main__":
    main()
