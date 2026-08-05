"""One-shot recovery: git-revert depth_eval_v4.py to clean HEAD, then re-apply
ONLY the safe verified-good edits via deterministic Python (avoiding fragile
str_replace anchor matching for unicode-heavy `_llm_cache` block).

Edits re-applied:
1. Add `from .llm_cache import cache_key as _llm_cache_key, get_cache_backend`
   import (deterministic line insertion right after the existing
   `from .llm import ChatMessage, get_factory` import line).
2. In `DepthReviewer.__init__`, add `self._compute_mode = ...` immediately
   before `self._llm = llm_func or call_llm` (deterministic insertion).
3. Replace `call_llm()` body to use `get_cache_backend()` instead of the
   module-level `_llm_cache_*` helpers. Then DELETE the module-level
   `_LLM_CACHE_TTL = get_settings().llm_cache_ttl` + `_llm_cache` dict + lock
   + `_llm_cache_key/_get/_set` block.
4. Insert inline fast-path early-return at the Q5a call site in
   `DepthReviewer.review()` body (single point of control, avoids
   `_run_q5a/b/c` method-internal anchor fragility).
5. Extend `evaluate_paper()` signature + `DepthReviewer()` call site.

Idempotent: each step checks for a unique marker comment first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "mock_api" / "depth_eval_v4.py"


def git_restore() -> None:
    """Restore depth_eval_v4.py to git HEAD clean state."""
    r = subprocess.run(
        ["git", "checkout", "HEAD", "--", "mock_api/depth_eval_v4.py"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if r.returncode != 0:
        print("[git_restore] FAILED:", r.stderr)
    else:
        print("[git_restore] depth_eval_v4.py restored to HEAD.")


def add_llm_cache_import() -> None:
    src = TARGET.read_text(encoding="utf-8")
    if "from .llm_cache import cache_key" in src:
        print("[import] already present; skip")
        return
    # Insert right after `from .llm import ChatMessage, get_factory`
    needle = "from .llm import ChatMessage, get_factory\n"
    addition = (
        "from .llm_cache import cache_key as _llm_cache_key, get_cache_backend\n"
    )
    if needle not in src:
        print("[import] anchor missing"); return
    new = src.replace(needle, needle + addition, 1)
    TARGET.write_text(new, encoding="utf-8")
    print("[import] added `from .llm_cache import ...`.")


def add_compute_mode_in_init() -> None:
    src = TARGET.read_text(encoding="utf-8")
    if "self._compute_mode = (compute_mode or \"deep\").strip().lower()" in src:
        print("[init] already present; skip")
        return
    needle = "self._llm = llm_func or call_llm\n"
    addition = (
        "        # 增量 P2-3：保存 compute_mode，供 review() fast-path 决策。\n"
        "        self._compute_mode = (compute_mode or \"deep\").strip().lower()\n"
    )
    if needle not in src:
        print("[init] anchor missing"); return
    new = src.replace(needle, addition + needle, 1)
    TARGET.write_text(new, encoding="utf-8")
    print("[init] added self._compute_mode.")


def refactor_call_llm() -> None:
    """Replace call_llm cache block + remove module-level _llm_cache_* block."""
    src = TARGET.read_text(encoding="utf-8")
    if "DEPTH v4.1 LLM 缓存命中 (backend=" in src:
        print("[call_llm] already refactored; skip")
        return

    # 1) Replace the cache-check block inside call_llm()
    old_cache_block = '''    # ── 缓存检查 ──
    cache_key = 0  # 默认值，防止缓存块异常时未赋值
    try:
        if temperature is None or max_tokens is None:
            _t, _m = _get_llm_params()
            if temperature is None:
                temperature = _t
            if max_tokens is None:
                max_tokens = _m
        cache_key = _llm_cache_key(system_prompt, prompt, temperature, max_tokens)
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            logger.debug("DEPTH v4.1 LLM 缓存命中")
            return cached
    except Exception:  # noqa: BLE001 - DEPTH 节点/watchdog 回调 - broad catch 兜底并写日志
        pass  # 缓存操作异常不影响主流程'''

    new_cache_block = '''    # ── 缓存检查（跨 backend / 跨进程，统一走 get_cache_backend()）──
    cache_key_int = 0  # 默认值，防止缓存块异常时未赋值
    cache_backend = None
    try:
        if temperature is None or max_tokens is None:
            _t, _m = _get_llm_params()
            if temperature is None:
                temperature = _t
            if max_tokens is None:
                max_tokens = _m
        cache_key_int = _llm_cache_key(system_prompt, prompt, temperature, max_tokens)
        cache_backend = get_cache_backend()
        cached = cache_backend.get(cache_key_int) if cache_backend is not None else None
        if cached is not None:
            logger.debug(
                "DEPTH v4.1 LLM 缓存命中 (backend=%s)",
                getattr(cache_backend, "name", "?"),
            )
            return cached
    except Exception:  # noqa: BLE001 - DEPTH 节点/watchdog 回调 - broad catch 兜底并写日志
        cache_backend = None
        pass  # 缓存操作异常不影响主流程'''

    if old_cache_block not in src:
        print("[call_llm.refactor] WARNING: cache-check block not found verbatim")
    else:
        src = src.replace(old_cache_block, new_cache_block, 1)
        print("[call_llm] cache-check refactored to backend.")

    # 2) Replace cache-set line inside call_llm() result handling
    old_set = '''            if content:
                _llm_cache_set(cache_key, content)
            return content'''
    new_set = '''            if content and cache_backend is not None:
                try:
                    cache_backend.set(cache_key_int, content)
                except Exception:  # noqa: BLE001 - DEPTH 节点/watchdog 回调 - broad catch 兜底并写日志
                    pass  # 缓存写入失败不影响主流程
            return content'''
    if old_set not in src:
        print("[call_llm.set] WARNING: cache-set line not found verbatim")
    else:
        src = src.replace(old_set, new_set, 1)
        print("[call_llm] cache-set refactored.")

    # 3) Delete the module-level _llm_cache_* block (plus helper functions).
    # Block runs from `from .settings import get_settings` helper import
    # through the end of `_llm_cache_set` function.
    old_module_block_marker_start = "from .settings import get_settings\n"
    old_module_block_marker_end = '''def _llm_cache_set(key: int, content: str) -> None:
    """写入缓存（含 FIFO 淘汰）。"""
    if _LLM_CACHE_TTL <= 0:
        return
    with _llm_cache_lock:
        if len(_llm_cache) >= _MAX_CACHE_ENTRIES:
            # FIFO 淘汰最旧条目
            oldest_key = min(_llm_cache, key=lambda k: _llm_cache[k][0], default=None)
            if oldest_key is not None:
                del _llm_cache[oldest_key]
        _llm_cache[key] = (time.monotonic(), content)
'''
    # Search and split: dst = src with this block replaced by a slim comment.
    if old_module_block_marker_start in src:
        # Find the line range to delete: from "from .settings import get_settings"
        # through end of `_llm_cache_set` function definition.
        start_idx = src.index(old_module_block_marker_start)
        # Find end of old_module_block_marker_end
        end_marker_idx = src.index(old_module_block_marker_end, start_idx)
        end_idx = end_marker_idx + len(old_module_block_marker_end)
        # Replacement: slim explanation comment
        replacement = (
            "# -------------------------------------------------------------------------\n"
            "# LLM 响应缓存层（已迁移至 ``mock_api.llm_cache``）：InProcess dict 默认实现 +\n"
            "# 可选 Redis 后端；TTL / FIFO 行为封装在 get_cache_backend()。\n"
            "# -------------------------------------------------------------------------\n"
        )
        new_src = src[:start_idx] + replacement + src[end_idx:]
        src = new_src
        print("[call_llm] removed module-level _llm_cache_* block.")

    TARGET.write_text(src, encoding="utf-8")
    print("[call_llm] all refactors written.")


def insert_review_fastpath() -> None:
    """Insert inline fast-path guard at Q5a call site in DepthReviewer.review()."""
    src = TARGET.read_text(encoding="utf-8")
    if "[P2-3 REVIEW_FASTPATH_GUARD]" in src:
        print("[fastpath] already inserted; skip")
        return
    old = "        q5a = self._run_q5a(pa_full, q1, q2, q3, q4, evidence_pool)\n"
    if old not in src:
        print("[fastpath] WARNING: q5a call site anchor not found verbatim")
        return
    # We wrap in if/else block; the OLD q5a line at 8-space indent goes INSIDE the else.
    new = (
        "        # ── fast 模式（P2-3）：跳过 Q5a/b/c 三个辩论节点，不发起 LLM 调用。\n"
        "        if self._compute_mode in (\"fast\", \"speed\"):\n"
        "            base_score = round(\n"
        "                (q2.novelty_score + q3.rigor_score + q4.influence_score) / 3.0,\n"
        "                4,\n"
        "            )\n"
        "            q5a = Q5aResult(critique_points=[], evidence_id=\"\", verified=True)\n"
        "            q5b = Q5bResult(defense_points=[], evidence_id=\"\", verified=True)\n"
        "            q5c = Q5cResult(\n"
        "                reasoning=(\n"
        "                    f\"[fast-mode] Q5a/b/c 跳过（compute_mode={self._compute_mode}）。\"\n"
        "                ),\n"
        "                delta=0.0,\n"
        "                calibrated_score=base_score,\n"
        "                score_std=0.0,\n"
        "                verdict=\"major_revision\",\n"
        "                llm_verdict=\"major_revision\",\n"
        "                delta_missing=True,\n"
        "                balancer_log=\"fast-mode synthetic (debate skipped)\",\n"
        "            )\n"
        "            balanced_cp: list = []\n"
        "            self._log(\n"
        "                f\"[fast-mode] saved 3 LLM calls (Q5 trio); calibrated_score={base_score}\"\n"
        "            )  # [P2-3 REVIEW_FASTPATH_GUARD]\n"
        "        else:\n"
        "            q5a = self._run_q5a(pa_full, q1, q2, q3, q4, evidence_pool)\n"
    )
    src = src.replace(old, new, 1)
    # Block-aware indent shift: ALL lines between the new q5a line and the
    # closing paren of the q5c call must be re-indented from 8 → 12 spaces.
    # This prevents orphaned 8-space lines (e.g. _compute_dwm, _node_stds)
    # from breaking the `else:` block syntax.
    # Find the q5a (now under `else:`) and shift down through the closing
    # `        )\n` (end of q5c call when at 8-space) by adding 4 spaces.
    block_re = re.compile(
        r"(            q5a = self\._run_q5a[\s\S]*?\n        \)\n)"
    )
    def _shift_inner(m: re.Match) -> str:
        chunk = m.group(1)
        # Shift from 12-space → 16-space (i.e. add 4) for lines that are at 12-space
        # (these are currently inside the 8-space outer block; moving them
        # under the else block needs them at 12-space; AFTER my replacement
        # the outer wrapped 12-space lines are them)
        return re.sub(r"^            ", r"                ", chunk, flags=re.MULTILINE)
    src, n = block_re.subn(_shift_inner, src, count=1)
    if n:
        print(f"[fastpath] shift subn replaced {n} block; inner 12→16 indent applied.")
    else:
        print("[fastpath] WARNING: block regex did not match; manual verification needed")

    TARGET.write_text(src, encoding="utf-8")
    print("[fastpath] inserted.")


def extend_evaluate_paper() -> None:
    """Extend evaluate_paper signature + DepthReviewer() inline call."""
    src = TARGET.read_text(encoding="utf-8")
    if "[P2-3 COMPUTE_MODE_PASSTHROUGH]" in src:
        print("[evaluate_paper] already extended; skip")
        return
    # 1) Replace evaluate_paper signature
    old_sig = "def evaluate_paper(paper_id: str, db: _Session | None = None) -> dict[str, Any]:"
    new_sig = (
        "def evaluate_paper(\n"
        "    paper_id: str,\n"
        "    db: _Session | None = None,\n"
        "    compute_mode: str | None = None,  # [P2-3 COMPUTE_MODE_PASSTHROUGH]\n"
        ") -> dict[str, Any]:"
    )
    if old_sig not in src:
        print("[evaluate_paper.sig] WARNING: signature not found verbatim")
    else:
        src = src.replace(old_sig, new_sig, 1)
        print("[evaluate_paper] signature extended.")

    # 2) Replace DepthReviewer() inline
    if "DepthReviewer(compute_mode=compute_mode)" in src:
        print("[evaluate_paper.constructor] already updated")
    else:
        src = src.replace(
            "DepthReviewer()",
            "DepthReviewer(compute_mode=compute_mode)",
        )
        print("[evaluate_paper] DepthReviewer() inline updated.")
    TARGET.write_text(src, encoding="utf-8")


def verify_compiles() -> None:
    """py_compile check + import smoke test."""
    r = subprocess.run(
        ["python", "-m", "py_compile", str(TARGET)],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print("[verify] py_compile OK")
    else:
        print("[verify] py_compile FAILED:")
        print(r.stderr[-1200:])
        return

    r = subprocess.run(
        ["python", "-c",
         "from mock_api.depth_eval_v4 import evaluate_paper, DepthReviewer; "
         "from mock_api.llm_cache import get_cache_backend; "
         "print('depth_eval_v4 imports OK; compute_mode field test:', "
         "DepthReviewer(compute_mode='fast')._compute_mode)"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if r.returncode == 0:
        print("[verify] imports + fast-mode flag OK")
        print("    ", r.stdout.strip())
    else:
        print("[verify] imports FAILED:")
        print(r.stderr[-1200:])


def main() -> None:
    git_restore()
    add_llm_cache_import()
    add_compute_mode_in_init()
    refactor_call_llm()
    insert_review_fastpath()
    extend_evaluate_paper()
    verify_compiles()


if __name__ == "__main__":
    main()
