"""One-time injection of compute_mode='fast' guards into _run_q5a/b/c.

Why not str_replace:
- Anchor lines contain non-ASCII unicode (CJK + fullwidth parens);
  str_replace's exact-match can fail due to subtle encoding issues.
- This script locates methods by signature regex on the AST and inserts
  an early-return block at the start of each method body (after the docstring,
  if any). Idempotent: skips if the guard text is already present.

Usage:
    python scripts/_inject_fast_mode_guards.py [target_file_path]
Default target: mock_api/depth_eval_v4.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


# Guard block prefix (Q5a / Q5b / Q5c each get a variation).
GUARDS: dict[str, str] = {
    # Q5a: 中性 critique_points=[]
    "_run_q5a_for_fast_mode": (
        "        # ── fast 模式（P2-3 性能优化）：跳过 LLM 调用，直接构造合成 neutral critique。\n"
        "        if self._compute_mode in (\"fast\", \"speed\"):\n"
        "            return Q5aResult(\n"
        "                critique_points=[],\n"
        "                evidence_id=\"\",\n"
        "                verified=True,\n"
        "            )\n\n"
    ),
    "_run_q5b_for_fast_mode": (
        "        # ── fast 模式：跳过 LLM 调用，直接构造合成 neutral defense。\n"
        "        if self._compute_mode in (\"fast\", \"speed\"):\n"
        "            return Q5bResult(\n"
        "                defense_points=[],\n"
        "                evidence_id=\"\",\n"
        "                verified=True,\n"
        "            )\n\n"
    ),
    "_run_q5c_for_fast_mode": (
        "        # ── fast 模式：跳过 LLM 调用，对 Q2/Q3/Q4 三维均值做校准。\n"
        "        if self._compute_mode in (\"fast\", \"speed\"):\n"
        "            base_score = round(\n"
        "                (q2.novelty_score + q3.rigor_score + q4.influence_score) / 3.0, 4\n"
        "            )\n"
        "            return (\n"
        "                Q5cResult(\n"
        "                    reasoning=(\n"
        "                        f\"[fast-mode] Q5a/b/c 辩论三节点跳过（compute_mode={self._compute_mode}）。\"\n"
        "                    ),\n"
        "                    delta=0.0,\n"
        "                    calibrated_score=base_score,\n"
        "                    score_std=0.0,\n"
        "                    verdict=\"major_revision\",\n"
        "                    llm_verdict=\"major_revision\",\n"
        "                    delta_missing=True,\n"
        "                    balancer_log=\"fast-mode synthetic (debate skipped)\",\n"
        "                ),\n"
        "                [],  # balanced_cp 空\n"
        "            )\n\n"
    ),
}


def inject(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    # 对每个 method name 找 def 位置
    out = src
    injected: list[str] = []
    skipped: list[str] = []
    for method_name, guard_text in [
        ("_run_q5a", GUARDS["_run_q5a_for_fast_mode"]),
        ("_run_q5b", GUARDS["_run_q5b_for_fast_mode"]),
        ("_run_q5c", GUARDS["_run_q5c_for_fast_mode"]),
    ]:
        # 1. 找方法 def
        pattern = re.compile(rf"^(    def {method_name}\(.*?\n)((?:        .*\n)*)", re.MULTILINE)
        match = pattern.search(out)
        if not match:
            skipped.append(method_name)
            continue
        # 2. 校验是否已注入（防止重复）
        body = match.group(2)
        if "fast 模式" in body or "compute_mode=" in body[:200]:
            skipped.append(f"{method_name} (already injected)")
            continue
        # 3. 在方法 def 后第一行（保持缩进 8 spaces）插入 guard
        method_def = match.group(1)
        rest_body = match.group(2)
        first_line, _, remainder = rest_body.partition("\n")
        # first_line is at 8-space indent + actual body line
        new_body = guard_text + first_line + "\n" + remainder
        new_def_block = method_def + new_body
        out = out.replace(match.group(0), new_def_block, 1)
        injected.append(method_name)
    path.write_text(out, encoding="utf-8")
    print(f"[inject] injected: {injected}")
    print(f"[inject] skipped: {skipped}")
    print(f"[inject] target: {path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mock_api/depth_eval_v4.py")
    inject(target)
