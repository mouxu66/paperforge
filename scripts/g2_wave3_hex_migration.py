#!/usr/bin/env python3
"""G2 Wave 3: batch replace Group A neutral hex colors with --pf-* CSS tokens.

Usage:
    python scripts/g2_wave3_hex_migration.py [file1.tsx file2.tsx ...]

The script only touches the Group A UI semantic colors listed in the G2 migration
guide.  Group B/C (antd palette, chart data colors, alpha variants) are left alone.

Caveats:
- #fff is mapped to var(--pf-bg-primary).  If a file uses #fff as a text color,
  the resulting var(--pf-bg-primary) is wrong and must be manually changed to
  var(--pf-text-primary).  After running, verify with:

      grep -rn "color:.*var(--pf-bg-primary)" src/

- #1e293b is mapped to var(--pf-text-primary); review any non-text usages.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Hex -> CSS var mapping (Group A only).  Order matters: longer/more specific first.
REPLACEMENTS: dict[str, str] = {
    # Backgrounds
    "#f8fafc": "var(--pf-bg-tertiary)",
    "#f1f5f9": "var(--pf-bg-tertiary)",
    "#fafafa": "var(--pf-bg-secondary)",
    "#f5f5f5": "var(--pf-bg-secondary)",
    "#f0f0f0": "var(--pf-bg-secondary)",
    "#e6f0ff": "var(--pf-primary-soft)",
    # Text
    "#1a1a2e": "var(--pf-text-primary)",
    "#475569": "var(--pf-text-secondary)",
    "#64748b": "var(--pf-text-muted)",
    "#94a3b8": "var(--pf-text-placeholder)",
    "#334155": "var(--pf-text-secondary)",
    "#1e293b": "var(--pf-text-primary)",
    "#666": "var(--pf-text-muted)",
    "#999": "var(--pf-text-placeholder)",
    # Brand / primary
    "#1e40af": "var(--pf-primary)",
    "#3b82f6": "var(--pf-primary)",
    # Borders
    "#e8ecf1": "var(--pf-border)",
    "#e2e8f0": "var(--pf-border-light)",
    "#cbd5e1": "var(--pf-scrollbar)",
    # States
    "#16a34a": "var(--pf-success)",
    "#dc2626": "var(--pf-error)",
    "#faad14": "var(--pf-warning)",
    # White background (dark mode bug)
    "#fff": "var(--pf-bg-primary)",
}

# Compile a single regex that matches any of the hex values (whole-word-ish).
# We require a word boundary after the hex so #fff doesn't match #ffffff.
_PATTERN = re.compile(
    "(" + "|".join(re.escape(h) for h in sorted(REPLACEMENTS, key=len, reverse=True)) + r")\b"
)


def _replace_in_file(path: Path) -> tuple[int, int]:
    """Return (replacements, skipped_alpha_matches)."""
    content = path.read_text(encoding="utf-8")
    original = content

    def _repl(match: re.Match) -> str:
        return REPLACEMENTS[match.group(1)]

    new_content, count = _PATTERN.subn(_repl, content)

    if new_content != original:
        path.write_text(new_content, encoding="utf-8")

    return count, 0


def main() -> int:
    if not sys.argv[1:]:
        print("Usage: python scripts/g2_wave3_hex_migration.py <file1.tsx> [file2.tsx ...]")
        return 1

    total = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"[skip] {path} does not exist")
            continue
        count, _ = _replace_in_file(path)
        if count:
            print(f"[replaced {count:3d}] {path}")
            total += count
        else:
            print(f"[none       ] {path}")

    print(f"\nTotal replacements: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
