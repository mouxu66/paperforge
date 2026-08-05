"""One-time rescue: remove malformed fast-guard injection from _run_q5a/b/c.

The earlier `scripts/_inject_fast_mode_guards.py` broke syntax by inserting
8-space-indented "if self._compute_mode in (...)" + return block INSIDE
the method's multi-line signature (between `def _run_q5X(` and `self,`).
Result: `SyntaxError: invalid syntax` at the misplaced `if` statement.

This script removes the broken lines by pattern detection:
- Scan `mock_api/depth_eval_v4.py` for any line that has the unique broken
  signature: `        if self._compute_mode in ("fast", "speed"):` AND the
  preceding line `        # ── fast 模式（P2-3 性能优化）：跳过 LLM 调用，直接构造合成 neutral critique。`
  (or similar text variants for q5b/q5c) — and the following 4-8 lines
  containing the `return Q5XResult(...)` block.

After fix:
- Each _run_q5a/b/c method reverts to its native (unpatched) state.
- Fast-path is then re-implemented at `DepthReviewer.review()` entry
  (single point of control), avoiding any per-method anchor fragility.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "mock_api" / "depth_eval_v4.py"


# Marker lines that the broken injection produced.
BROKEN_MARKERS = [
    "# ── fast 模式（P2-3 性能优化）：跳过 LLM 调用，直接构造合成 neutral critique。",
    "# ── fast 模式：跳过 LLM 调用，直接构造合成 neutral defense。",
    "# ── fast 模式：跳过 LLM 调用，对 Q2/Q3/Q4 三维均值做校准。",
    "if self._compute_mode in (\"fast\", \"speed\"):",
    "return Q5aResult(",
    "return Q5bResult(",
    "return ("  # for q5c early-return of tuple
]


def repair() -> int:
    src = TARGET.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)

    # Find method defs and walk through them, looking for the broken-block
    # pattern between the `def _run_q5X(` opening line and the `self,` line
    # (first parameter at 8-space indent).
    out: list[str] = []
    i = 0
    removed_count = 0

    while i < len(lines):
        line = lines[i]

        # Detect the start of a broken method: a `def _run_q5X(` line followed
        # by lines containing the broken markers before the canonical `self,`.
        if re.match(r"\s*def _run_q5[abc]\(", line):
            method_def_idx = i
            # Find canonical `self,` line at 8-space indent after this def.
            j = i + 1
            while j < len(lines) and not re.match(r"\s{8}self,\s*\n?$", lines[j]):
                # If we encounter the body's first 8-space comment (passing
                # docstring/spaces), still treat as signature continuation.
                j += 1
                if j - method_def_idx > 50:  # defensive: don't scan infinitely
                    break
            # If we found a `self,`, everything between method_def_idx (inclusive
            # of `def`) and just before `self,` could have broken injection.
            # Identify removable vs safe lines in that range.
            if j < len(lines) and re.match(r"\s{8}self,\s*\n?$", lines[j]):
                # Init signature buffer: keep def line, drop until self, then
                # replace with a properly-formatted signature start.
                sig_lines = [lines[method_def_idx]]
                # Walk from method_def_idx+1 to j-1, drop any line that is
                # clearly part of the broken injection (matches BROKEN_MARKERS
                # text content, or its content matches one of those).
                for k in range(method_def_idx + 1, j):
                    candidate = lines[k]
                    is_broken = any(marker in candidate for marker in BROKEN_MARKERS)
                    # Drop the line if it's the broken guard content.
                    if is_broken:
                        removed_count += 1
                        continue
                    sig_lines.append(candidate)
                sig_lines.append(lines[j])  # the `self,` line
                out.extend(sig_lines)
                i = j + 1
                continue
        # Default: keep the line
        out.append(line)
        i += 1

    new_src = "".join(out)
    TARGET.write_text(new_src, encoding="utf-8")
    print(f"[rescue] removed {removed_count} broken lines from {TARGET}")
    return removed_count


if __name__ == "__main__":
    repair()
