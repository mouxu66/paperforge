#!/usr/bin/env python3
"""委派 + 评审工具（AGENTS.md §2 纪律 7 的落地实现）。

把任务委派给 DeepSeek Harness（`dsh --profile headless`），拿到产出后，
用一组机器可检查的验收条件（shell 命令）自动评审，输出逐条 verdict。

工作流：
1. 预检：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL 必须已配置；dsh 命令必须可用
   （失败响亮，绝不静默跳过 —— 纪律 4）。
2. 委派：以项目根目录为 workspace 运行 `dsh --profile headless "<brief>"`，
   完整输出落盘为 transcript（模型可见 ⟺ 有日志 —— 纪律 5）。
3. 评审：在项目根目录逐个执行 `--accept "<shell 命令>"`，每个必须 exit 0；
   任一失败 → 非零退出，报告失败命令与输出尾部（验证行为而非表象 —— 纪律 2）。

用法（项目根目录，Git Bash / POSIX shell）:
    python scripts/dsh_delegate.py \\
        --brief "修复 tests/test_depth_v4.py 里失败的用例，并保持向后兼容" \\
        --accept "python -m pytest tests/test_depth_v4.py -x -q" \\
        --accept "python scripts/diagnostics/check_agents_docs_sync.py"

可选参数:
    --dsh-cmd CMD         dsh 启动命令（默认: npx --yes @deepseek-ai/dsh --profile headless）
                          DSH_ROOT 指向本地构建时可用: pnpm dsh --profile headless
    --timeout SEC         委派步骤超时（默认 1800s）
    --accept-timeout SEC  每个验收命令超时（默认 300s）
    --runs-dir DIR        transcript 落盘目录（默认 scripts/runs）
    --no-delegate         跳过委派步骤（只跑验收命令，用于评审已有产出/调试）

退出码:
    0 = 全部验收通过    1 = 存在未通过的验收条件
    2 = 预检失败（缺 key / dsh 不可用）    3 = 委派步骤失败
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows 控制台 UTF-8（同 scripts/run_fraud_paper_test.py 模式）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_DSH_CMD = "npx --yes @deepseek-ai/dsh --profile headless"


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _preflight(dsh_cmd: str, no_delegate: bool) -> str | None:
    """返回错误信息；无错误返回 None。"""
    # --no-delegate 只评审已有产出，不需要模型凭据
    if no_delegate:
        return None
    if not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("DEEPSEEK_BASE_URL"):
        return (
            "缺少模型凭据：需设置 DEEPSEEK_API_KEY（或 DEEPSEEK_BASE_URL 指向"
            "OpenAI 兼容端点）。dsh 的 agent 循环必须连接模型 API，没有\"无模型\"模式。"
        )
    if no_delegate:
        return None
    try:
        proc = subprocess.run(
            f"{dsh_cmd} --help",
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return "dsh 命令可用性检查超时（120s），请确认 --dsh-cmd 正确且网络可用"
    if proc.returncode != 0:
        return (
            "dsh 命令不可用。安装方式：\n"
            "  1) 源码构建: git clone https://github.com/deepseek-ai/deepseek-harness.git && "
            "cd deepseek-harness && pnpm install && pnpm run build\n"
            "     然后 --dsh-cmd \"cd <dsh目录> && pnpm dsh --profile headless\"\n"
            "  2) npm: 确认 npx 可用（默认命令 npx --yes @deepseek-ai/dsh）"
        )
    return None


def _delegate(dsh_cmd: str, brief: str, timeout: int, transcript: Path) -> tuple[int, str]:
    cmd = f"{dsh_cmd} {shlex.quote(brief)}"
    print(f"[delegate] 运行: {cmd}\n[delegate] transcript -> {transcript}")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as e:
        tail = (e.output or "")[-2000:]
        transcript.write_text(f"TIMEOUT after {timeout}s\nstdout tail:\n{tail}", encoding="utf-8")
        return 3, f"委派步骤超时（{timeout}s）"

    transcript.write_text(
        f"$ {cmd}\n\n=== exit {proc.returncode} ===\n\n{proc.stdout}\n"
        f"=== stderr ===\n\n{proc.stderr}\n",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return 3, (
            f"dsh 返回非零（{proc.returncode}），transcript: {transcript}\n"
            f"stderr 尾部: {proc.stderr[-800:] or '(空)'}"
        )
    return 0, proc.stdout


def _review(accepts: list[str], timeout: int, transcript: Path) -> int:
    """逐个执行验收命令；返回失败数。"""
    results: list[tuple[str, int | str, str]] = []
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(f"\n\n=== review {_ts()} ===\n")
        for i, cmd in enumerate(accepts, 1):
            fh.write(f"\n--- accept[{i}]: {cmd}\n")
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(ROOT),
                )
                status: int | str = proc.returncode
                tail = (proc.stdout or "")[-500:] + (proc.stderr or "")[-500:]
            except subprocess.TimeoutExpired as e:
                status = "timeout"
                tail = f"超过 {timeout}s"
                if isinstance(e.output, str):
                    tail += "\n" + e.output[-500:]
            fh.write(f"status={status}\n{tail}\n")
            results.append((cmd, status, tail))

    print("\n=== 评审 verdict ===")
    failed = 0
    for i, (cmd, status, tail) in enumerate(results, 1):
        ok = status == 0
        failed += 0 if ok else 1
        mark = "✅" if ok else "❌"
        print(f"{mark} accept[{i}]: {cmd}  ->  {status}")
        if not ok:
            print(f"    输出尾部: {tail.strip()[-400:]}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description="委派 dsh headless 并按验收条件评审产出")
    ap.add_argument("--brief", required=True, help="委派给 dsh 的任务描述")
    ap.add_argument("--accept", action="append", default=[], help="验收命令（可多次）")
    ap.add_argument("--dsh-cmd", default=DEFAULT_DSH_CMD, help="dsh 启动命令")
    ap.add_argument("--timeout", type=int, default=1800, help="委派步骤超时秒数")
    ap.add_argument("--accept-timeout", type=int, default=300, help="单条验收命令超时秒数")
    ap.add_argument("--runs-dir", default=str(ROOT / "scripts" / "runs"), help="transcript 目录")
    ap.add_argument("--no-delegate", action="store_true", help="跳过委派，只跑验收命令")
    args = ap.parse_args()

    if not args.accept:
        print("⚠️  未提供任何 --accept 验收条件——评审阶段将空转，建议至少给一条", file=sys.stderr)

    err = _preflight(args.dsh_cmd, args.no_delegate)
    if err:
        print(f"❌ 预检失败：\n  {err}", file=sys.stderr)
        return 2

    runs = Path(args.runs_dir)
    runs.mkdir(parents=True, exist_ok=True)
    transcript = runs / f"dsh_delegate_{_ts()}.log"

    if not args.no_delegate:
        code, out = _delegate(args.dsh_cmd, args.brief, args.timeout, transcript)
        if code != 0:
            print(f"❌ 委派失败：{out}", file=sys.stderr)
            return code
        print(f"[delegate] dsh 完成，最终输出尾部:\n{out.strip()[-800:]}\n")
    else:
        transcript.write_text(f"$ no-delegate 模式（仅评审）{_ts()}\n", encoding="utf-8")

    failed = _review(args.accept, args.accept_timeout, transcript)
    if failed:
        print(f"\n❌ {failed} 条验收未通过，详见 {transcript}", file=sys.stderr)
        return 1
    print(f"\n✅ 全部 {len(args.accept)} 条验收通过。transcript: {transcript}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
