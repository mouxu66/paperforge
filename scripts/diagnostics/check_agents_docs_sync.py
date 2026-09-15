#!/usr/bin/env python3
"""pre-commit 检查：AGENTS.md 与 deepseek-harness skill 的双向引用与纪律锚点同步。

防止「必读纪律」（AGENTS.md §2）与「完整方法论」（.agents/skills/deepseek-harness/SKILL.md）
脱节。检查项：

1. 两个文件都存在；
2. 双向引用存在：AGENTS.md → skill 路径；skill → AGENTS.md；
3. 纪律锚点 [P1]..[P8] 在两个文件中各出现且仅出现一次（两边各 8 条，一一对应）；
4. 两个文档的纪律章节头存在。

任何改动（改纪律内容、改章节结构、移动文件）都必须保持上述不变量，
否则本检查以非零退出码响亮报错（对齐 AGENTS.md §2 纪律 4：配置显式、失败响亮）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows 控制台 UTF-8（同 scripts/run_fraud_paper_test.py 模式）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = ROOT / "AGENTS.md"
SKILL_MD = ROOT / ".agents/skills/deepseek-harness/SKILL.md"

PRINCIPLES = [f"P{i}" for i in range(1, 9)]


def _count(text: str, marker: str) -> int:
    return len(re.findall(re.escape(marker), text))


def main() -> int:
    errors: list[str] = []

    if not AGENTS_MD.exists():
        errors.append(f"缺少 AGENTS.md: {AGENTS_MD}")
    if not SKILL_MD.exists():
        errors.append(f"缺少 skill 文件: {SKILL_MD}")
    if errors:
        return _fail(errors)

    agents = AGENTS_MD.read_text(encoding="utf-8")
    skill = SKILL_MD.read_text(encoding="utf-8")

    # ── 双向引用 ──────────────────────────────────────────────
    if ".agents/skills/deepseek-harness/SKILL.md" not in agents:
        errors.append(
            "AGENTS.md 缺少指向 skill 的引用：`.agents/skills/deepseek-harness/SKILL.md`"
        )
    if "AGENTS.md" not in skill:
        errors.append("skill 缺少指向 AGENTS.md 的引用")

    # ── 章节头 ────────────────────────────────────────────────
    if "## 2. 核心纪律" not in agents:
        errors.append("AGENTS.md 缺少纪律章节头 `## 2. 核心纪律`")
    if "## Core Principles" not in skill:
        errors.append("skill 缺少纪律章节头 `## Core Principles`")

    # ── 锚点同步：两边各出现且仅出现一次 ─────────────────────────
    for pid in PRINCIPLES:
        for label, text in (("AGENTS.md", agents), ("skill", skill)):
            n = _count(text, f"[{pid}]")
            if n != 1:
                errors.append(
                    f"[{pid}] 在 {label} 中出现 {n} 次（应为 1 次）——"
                    f"纪律内容或锚点被改动，请同步两处文档"
                )

    return _fail(errors) if errors else 0


def _fail(errors: list[str]) -> int:
    print("❌ 纪律文档同步检查失败：")
    for e in errors:
        print(f"  - {e}")
    print("提示：AGENTS.md §2 与 .agents/skills/deepseek-harness/SKILL.md 必须保持同步；")
    print("8 条纪律锚点 [P1]..[P8] 在两个文件中各出现一次，双向引用必须存在。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
