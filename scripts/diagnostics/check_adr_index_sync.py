#!/usr/bin/env python3
"""pre-commit 检查：docs/adr/README.md 索引表与 ADR 文件的一致性。

防止「新增 ADR 忘登记索引」或「索引登记了不存在的 ADR」导致的漂移。不变量：

1. 每个 ADR 文件（docs/adr/*.md，除 README.md）的编号必须出现在索引表中；
2. 索引表中的每个编号必须有对应文件（历史决策 001-005 无独立文件，列入例外表）；
3. 编号不得重复（文件侧与索引侧各自唯一）。

任何不一致都以非零退出码响亮报错（对齐 AGENTS.md §2 纪律 4：配置显式、失败响亮）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows 控制台 UTF-8（同 scripts/run_fraud_paper_test.py 模式，避免 GBK 下
# ❌/✅ 等字符触发 UnicodeEncodeError 让钩子以 traceback 而非整洁报错收场）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent.parent.parent
ADR_DIR = ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"

# 索引中登记但无独立文件的历史决策（早于 docs/adr/ 目录体系建立，
# 决策内容记录在各自实现/文档中，如 ADR-003 见 mock_api/settings.py 注释）。
INDEX_ONLY_LEGACY = {1, 2, 3, 4, 5}

_ROW_RE = re.compile(r"^\|\s*(\d{3})\s*\|")
_HEADER_RE = re.compile(r"# ADR-(\d+):")


def _index_rows(text: str) -> list[int]:
    return [int(m.group(1)) for m in (_ROW_RE.match(line) for line in text.splitlines()) if m]


def main() -> int:
    errors: list[str] = []

    if not INDEX.exists():
        return _fail([f"缺少 ADR 索引: {INDEX}"])

    rows = _index_rows(INDEX.read_text(encoding="utf-8"))
    if not rows:
        errors.append(f"{INDEX.name}: 索引表中未解析到任何 `| NNN |` 行")

    files: dict[int, list[str]] = {}
    for f in sorted(ADR_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        head = f.read_text(encoding="utf-8", errors="replace")[:200]
        m = _HEADER_RE.search(head)
        if not m:
            errors.append(f"{f.name}: 文件头部缺少 `# ADR-<编号>:` 标题")
            continue
        files.setdefault(int(m.group(1)), []).append(f.name)

    # 文件 → 索引：每个 ADR 文件必须在索引表中登记
    for num in sorted(files):
        if num not in rows:
            errors.append(
                f"ADR-{num:03d}（{files[num][0]}）未在 docs/adr/README.md 索引表中登记"
            )

    # 索引 → 文件：除历史例外表外，每个索引编号必须有对应文件
    for num in sorted(rows):
        if num in INDEX_ONLY_LEGACY:
            continue
        if num not in files:
            errors.append(f"索引表登记了 ADR-{num:03d}，但 docs/adr/ 下无对应文件")

    # 重复检测
    for num, names in files.items():
        if len(names) > 1:
            errors.append(f"ADR-{num:03d} 存在多个文件: {names}")

    return _fail(errors) if errors else 0


def _fail(errors: list[str]) -> int:
    print("❌ ADR 索引一致性检查失败：")
    for e in errors:
        print(f"  - {e}")
    print("提示：新增 ADR 时须同时在 docs/adr/README.md 索引表登记一行；")
    print("编号 001-005 为无独立文件的历史决策，由脚本例外表维护。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
