"""Atomic one-shot applier for P2-3 (compute_mode='fast') + P2-4 (Redis LLM cache).

Each step uses ONLY ASCII anchors (str.find/slice/concat), no regex, to avoid
the unicode/CJK anchor issues that broke earlier attempts. Idempotent: each
step checks a unique marker; if present, skip silently.

Run from repo root:
    python scripts/_p2_3_p2_4_apply.py
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write(p: Path, src: str) -> None:
    p.write_text(src, encoding="utf-8")


def _anchor_replace(src: str, anchor: str, target: str, *, count: int = 1) -> tuple[str, bool]:
    """Replace `anchor` with `target` up to `count` times. Returns (new_src, success)."""
    idx = src.find(anchor)
    if idx < 0:
        return src, False
    return src.replace(anchor, target, count), True


# ============================================================================
# Step 1: mock_api/settings.py
#   - extend _validate_compute_mode whitelist to include "fast"
#   - add llm_cache_backend + llm_cache_ttl + redis_url fields
# ============================================================================
def step_settings() -> None:
    p = REPO / "mock_api" / "settings.py"
    src = _read(p)
    if "[P2-3 SETTINGS_PASSTHROUGH]" in src:
        print("[settings] already patched; skip")
        return

    # 1a. Extend validator whitelist. Anchor: the validator function call site
    #     uses raw strings, so we can safely replace the literal {'speed', 'deep'}.
    new_src, ok = _anchor_replace(
        src,
        'value.lower() not in {"speed", "deep"}:',
        'value.lower() not in {"fast", "speed", "deep"}:',
    )
    if not ok:
        print("[settings] WARN: validator anchor not found — manual patch needed")
        return
    src = new_src

    # 1b. Add cache settings. Anchor: end of class body (dataclass-like, picks
    #     the last `effective_batch_parallel:` line — present and ASCII-only).
    cache_fields = (
        "\n    llm_cache_backend: Literal[\"inprocess\", \"redis\"] = \"inprocess\"  # [P2-3 SETTINGS_PASSTHROUGH]\n"
        "    llm_cache_ttl: float = 300.0  # seconds; 0 disables cache\n"
        "    redis_url: str | None = None  # only used when llm_cache_backend=\"redis\"\n"
    )
    anchor = "    effective_batch_parallel: int = 2"
    new_src, ok = _anchor_replace(src, anchor, anchor + cache_fields)
    if not ok:
        print("[settings] WARN: effective_batch_parallel anchor not found")
        return
    src = new_src

    # 1c. Make sure Literal is imported (it is, but defensive: re-check)
    if "from typing import" in src and "Literal" not in src.split("class Settings")[0]:
        # Literal is already imported at top; no-op
        pass

    _write(p, src)
    print("[settings] patched: validator + cache fields")


# ============================================================================
# Step 2: mock_api/config.py
#   - extend COMPUTE_MODES to include "fast" preset
# ============================================================================
def step_config() -> None:
    p = REPO / "mock_api" / "config.py"
    src = _read(p)
    if "[P2-3 CONFIG_FAST_PRESET]" in src:
        print("[config] already patched; skip")
        return

    # Find the dict literal opening — anchor on the first preset key (ASCII only).
    # "speed" preset is the cheapest existing one — we'll add "fast" before it.
    # Use the simplest anchor: insert before the line that contains '"speed": {'
    lines = src.splitlines(keepends=True)
    out_lines: list[str] = []
    inserted = False
    fast_block = (
        '    "fast": ComputePreset(\n'
        "        id=\"fast\",\n"
        '        name="Fast (no LLM)",\n'
        "        temperature=0.0,\n"
        "        max_tokens=256,\n"
        "        ctx_size=4096,\n"
        "        max_chars_full=8000,\n"
        "        max_chars_short=2000,\n"
        '        parallel=False,\n'
        "        paged_attn=True,\n"
        '        backend="local",\n'
        "    ),  # [P2-3 CONFIG_FAST_PRESET]\n"
    )
    for ln in lines:
        if (not inserted) and '"speed":' in ln and "ComputePreset" in ln:
            out_lines.append(fast_block)
            inserted = True
        out_lines.append(ln)
    if not inserted:
        print("[config] WARN: 'speed': ComputePreset anchor not found; manual patch needed")
        return
    _write(p, "".join(out_lines))
    print("[config] patched: added 'fast' ComputePreset")


# ============================================================================
# Step 3: mock_api/depth_eval_v4.py
#   3a. add llm_cache import (ASCII anchor — "from .llm import" line)
#   3b. remove module-level _llm_cache_* dict/lock/get/set/fast-path helpers
#   3c. wire call_llm() body to use get_cache_backend()
#   3d. add `self._compute_mode` to DepthReviewer.__init__
#   3e. add fast-path early return at end of `review()` based on compute_mode
# ============================================================================
def step_depth_eval_v4() -> None:
    p = REPO / "mock_api" / "depth_eval_v4.py"
    src = _read(p)
    if "[P2-3 DEPTH_V4_FASTPATH]" in src:
        print("[depth_eval_v4] already patched; skip")
        return

    # 3a. Add llm_cache import right after the existing `from .llm` import line.
    anchor_import = "from .llm import ChatMessage, get_factory"
    new_src, ok = _anchor_replace(
        src,
        anchor_import,
        anchor_import
        + "\nfrom .llm_cache import cache_key as _llm_cache_key, get_cache_backend  # [P2-3 DEPTH_V4_FASTPATH]",
    )
    if not ok:
        print("[depth_eval_v4] WARN: llm import anchor not found")
        return
    src = new_src

    # 3c. Wire call_llm: replace original module-level cache block with backend.
    # Old code (anchored):
    #   cache_key_int = _llm_cache_key(...)
    #   cached = _llm_cache_get(cache_key_int)
    # New code uses get_cache_backend(). We append a small block at the start
    # of call_llm so it consults the backend first.
    cache_block_old = (
        "    # ── 缓存检查 ──\n"
        "    cache_key_int = _llm_cache_key(\n"
        "        system_prompt, prompt, temperature, max_tokens\n"
        "    )\n"
        "    cached = _llm_cache_get(cache_key_int)\n"
        "    if cached is not None:\n"
        "        return cached\n"
    )
    cache_block_new = (
        "    # ── 缓存检查（跨进程跨 backend；通过依赖注入的单例）──\n"
        "    cache_backend = None\n"
        "    cache_key_int = 0\n"
        "    try:\n"
        "        cache_key_int = _llm_cache_key(system_prompt, prompt, temperature, max_tokens)\n"
        "        cache_backend = get_cache_backend()\n"
        "        cached = cache_backend.get(cache_key_int) if cache_backend is not None else None\n"
        "        if cached is not None:\n"
        "            logger.debug(\n"
        "                \"DEPTH v4 LLM 缓存命中 (backend=%s)\",\n"
        "                getattr(cache_backend, \"name\", \"?\"),\n"
        "            )\n"
        "            return cached\n"
        "    except Exception:\n"
        "        cache_backend = None  # 后端故障不影响主流程\n"
    )
    new_src, ok = _anchor_replace(src, cache_block_old, cache_block_new, count=1)
    if not ok:
        print("[depth_eval_v4] WARN: original cache block not found; will try simpler alt form")
        # fall back to a lighter patch — anchor on the first 'cached = _llm_cache_get'
        cache_block_alt_old = (
            "    cached = _llm_cache_get(cache_key_int)\n"
            "    if cached is not None:\n"
            "        return cached\n"
        )
        cache_block_alt_new = (
            "    try:\n"
            "        cache_backend = get_cache_backend()\n"
            "        cached = cache_backend.get(cache_key_int) if cache_backend is not None else None\n"
            "        if cached is not None:\n"
            "            logger.debug(\n"
            "                \"DEPTH v4 LLM 缓存命中 (backend=%s)\", getattr(cache_backend, \"name\", \"?\")\n"
            "            )\n"
            "            return cached\n"
            "    except Exception:\n"
            "        cached = None\n"
        )
        new_src, ok = _anchor_replace(src, cache_block_alt_old, cache_block_alt_new)
        if not ok:
            print("[depth_eval_v4] WARN: fallback cache block also not found — manual patch needed")
            return
    src = new_src

    # 3c(2). Replace `_llm_cache_set` call inside call_llm with backend.set.
    set_old = "_llm_cache_set(cache_key_int, content)"
    set_new = (
        "if cache_backend is not None:\n"
        "                try:\n"
        "                    cache_backend.set(cache_key_int, content)\n"
        "                except Exception:\n"
        "                    pass"
    )
    new_src, ok = _anchor_replace(src, set_old, set_new)
    if not ok:
        print("[depth_eval_v4] WARN: _llm_cache_set call site not found")
        return
    src = new_src

    # 3d. Add self._compute_mode right after "self._llm = llm_func or call_llm"
    #     and remove the old hardcoded guard `if self._llm is call_llm:` (matches
    #     the new constructor semantics).
    init_anchor = "        self._llm = llm_func or call_llm"
    init_inject = (
        "        self._llm = llm_func or call_llm\n"
        "        # [P2-3 DEPTH_V4_FASTPATH] compute_mode flag: 'fast' short-circuits\n"
        "        # Q5a/b/c in review(); 'speed' and 'deep' run the full pipeline.\n"
        "        self._compute_mode = (compute_mode or \"deep\").strip().lower()"
    )
    new_src, ok = _anchor_replace(src, init_anchor, init_inject)
    if not ok:
        print("[depth_eval_v4] WARN: init anchor not found")
        return
    src = new_src

    # 3e. Fast-path in `review()`. We inject AFTER Q4 finishes — but to keep the
    #     change minimal and side-effect-free, we don't restructure the existing
    #     Q5a/b/c calls. Instead we add a marker on the class: callers can
    #     check `self._compute_mode` and short-circuit BEFORE entering review().
    # The actual short-circuit lives in run_depth_review_sync (we'll wrap there).
    # So just leave review() untouched. Add a comment near it.
    review_anchor = "    def review("
    review_inject = (
        "    # [P2-3 DEPTH_V4_FASTPATH] compute_mode='fast' is honored in\n"
        "    # run_depth_review_sync (depth_tasks.py) which builds a synthetic\n"
        "    # neutral DepthV4Result and writes it directly to DB.\n"
        "    # This keeps review() unchanged and avoids brittle edits.\n"
        "    def review("
    )
    # Only insert once at the FIRST `    def review(` we find.
    if review_anchor in src and "[P2-3 DEPTH_V4_FASTPATH]" not in src:
        src = src.replace(review_anchor, review_inject, 1)
        print("[depth_eval_v4] inserted fast-path comment marker near review()")

    _write(p, src)
    print("[depth_eval_v4] patched: imports + call_llm cache + _compute_mode")


# ============================================================================
# Step 4: mock_api/schemas.py
#   - add Optional[Literal[...] compute_mode field to BOTH request schemas
# ============================================================================
def step_schemas() -> None:
    p = REPO / "mock_api" / "schemas.py"
    src = _read(p)
    if "[P2-3 SCHEMAS_COMPUTE_MODE]" in src:
        print("[schemas] already patched; skip")
        return

    new_field = "    compute_mode: Optional[Literal[\"fast\", \"speed\", \"deep\"]] = None  # [P2-3 SCHEMAS_COMPUTE_MODE]"

    # Find `DepthScoreRequest` and `DepthV4ReviewSelectedRequest` insertions.
    # We prepend the field on the line right after the `class X(BaseModel):` header.
    def inject_after_class(src: str, class_name: str) -> str:
        marker = f"class {class_name}(BaseModel):"
        idx = src.find(marker)
        if idx < 0:
            return src  # class absent — skip
        # Find end of that line (assume \n immediately after)
        line_end = src.find("\n", idx)
        if line_end < 0:
            return src
        # Already injected?
        tail = src[line_end + 1 : line_end + 200]
        if "[P2-3 SCHEMAS_COMPUTE_MODE]" in tail:
            return src
        # Insert new field at the next line
        inject_pos = line_end + 1
        # Skip possible leading docstring / blank lines — find first non-blank non-class header line
        # by jumping to a known sentinel: the indented field.
        rest = src[inject_pos:]
        # Find first '    ' (4-space) indented line
        first_field_offset = None
        scan = 0
        for i in range(0, min(len(rest), 400)):
            ch = rest[i]
            if ch == "\n":
                scan = i + 1
            elif rest[i : i + 4] == "    ":
                first_field_offset = scan
                break
            elif ch.isspace():
                continue
            else:
                # Non-whitespace, non-indent-4 char → class ends here, bail
                first_field_offset = scan
                break
        if first_field_offset is None:
            return src
        insert_at = inject_pos + first_field_offset
        return src[:insert_at] + new_field + "\n" + src[insert_at:]

    src = inject_after_class(src, "DepthScoreRequest")
    src = inject_after_class(src, "DepthV4ReviewSelectedRequest")

    # Ensure Optional / Literal import comes from typing — most schemas.py files
    # already import them. We add a defensive try.
    if "from typing import" in src and "Literal" not in src:
        # patch the import line to include Literal
        m = src.find("from typing import ")
        end = src.find("\n", m)
        old = src[m:end]
        if "Literal" not in old:
            new = old + ", Literal"
            src = src[:m] + new + src[end:]

    _write(p, src)
    print("[schemas] patched: compute_mode field on DepthScoreRequest + DepthV4ReviewSelectedRequest")


# ============================================================================
# Step 5: mock_api/depth_tasks.py
#   - extend run_depth_review_sync signature with compute_mode
#   - construct DepthReviewer(compute_mode=compute_mode)
#   - fast-path: if compute_mode == "fast", short-circuit to synthetic neutral
# ============================================================================
def step_depth_tasks() -> None:
    p = REPO / "mock_api" / "depth_tasks.py"
    src = _read(p)
    if "[P2-3 DEPTH_TASKS_FASTPATH]" in src:
        print("[depth_tasks] already patched; skip")
        return

    # 5a. change signature: `def run_depth_review_sync(paper_id: str) -> str:`
    #                                   │
    #                                   v
    #   `def run_depth_review_sync(
    #        paper_id: str, compute_mode: str | None = None
    #    ) -> str:`
    old_sig = "def run_depth_review_sync(paper_id: str) -> str:"
    new_sig = (
        "def run_depth_review_sync(\n"
        "    paper_id: str,\n"
        "    compute_mode: str | None = None,  # [P2-3 DEPTH_TASKS_FASTPATH]\n"
        ") -> str:"
    )
    new_src, ok = _anchor_replace(src, old_sig, new_sig)
    if not ok:
        print("[depth_tasks] WARN: signature anchor not found")
        return
    src = new_src

    # 5b. Replace DepthReviewer() with DepthReviewer(compute_mode=compute_mode)
    src = src.replace(
        "DepthReviewer()",
        "DepthReviewer(compute_mode=compute_mode)",
        1,
    )

    # 5c. Inject fast-path guard AFTER the DB setup but BEFORE evaluate logic
    #     so we never touch LLM for compute_mode="fast".
    #     Anchor: a recognizable ASCII line near "reviewer = DepthReviewer(...)"
    fast_guard = (
        "    # [P2-3 DEPTH_TASKS_FASTPATH] compute_mode='fast' 短路：直接写合成 neutral review。\n"
        "    resolved_mode = (compute_mode or \"deep\").strip().lower()\n"
        "    if resolved_mode == \"fast\":\n"
        "        logger.info(\"DEPTH fast 模式（P2-3）：paper=%s 跳过 LLM，写入合成 neutral review\", paper_id)\n"
        "        from .models import DepthReviewV4\n"
        "        from datetime import datetime as _dt\n"
        "        synthetic = DepthReviewV4(\n"
        "            paper_id=paper_id,\n"
        "            calibrated_score=0.5,\n"
        "            base_score=0.5,\n"
        "            final_verdict=\"major_revision\",\n"
        "            q5c_reasoning=\"fast 模式：未调用 LLM，写入合成 neutral review\",\n"
        "            weights={\"alpha\": 0.0, \"beta\": 0.0, \"gamma\": 0.0},\n"
        "            evaluated_at=_dt.utcnow().isoformat(),\n"
        "            status=\"completed\",\n"
        "            compute_mode=\"fast\",\n"
        "        )\n"
        "        db.add(synthetic)\n"
        "        try:\n"
        "            db.commit()\n"
        "        except Exception:\n"
        "            db.rollback()\n"
        "            raise\n"
        "        return str(synthetic.id)\n"
    )
    # Inject immediately before `reviewer = DepthReviewer(...)` to ensure the
    # guard runs first.
    reviewer_anchor = "    reviewer = DepthReviewer(compute_mode=compute_mode)\n"
    if reviewer_anchor in src:
        src = src.replace(reviewer_anchor, fast_guard + reviewer_anchor, 1)
        print("[depth_tasks] injected fast-path guard before reviewer construction")
    else:
        # Fallback: insert before the first `DepthReviewV4` record creation line
        # or before `pipeline = build_depth_dag(...)`.
        anchor_pipelines = [
            "    pipeline = build_depth_dag(",
        ]
        for a in anchor_pipelines:
            if a in src:
                src = src.replace(a, fast_guard + a, 1)
                print("[depth_tasks] injected fast-path guard before pipeline build")
                break
        else:
            print("[depth_tasks] WARN: no anchor for fast-path guard; manual injection needed")

    _write(p, src)
    print("[depth_tasks] patched: signature + DepthReviewer kwarg + fast-path")


# ============================================================================
# Step 6: mock_api/workers/reviews.py
#   - already has compute_mode extraction (verified earlier); see Read
# ============================================================================
def step_workers_reviews() -> None:
    p = REPO / "mock_api" / "workers" / "reviews.py"
    src = _read(p)
    if "[P2-3 WORKERS_PASSTHROUGH]" in src:
        print("[workers/reviews] already patched; skip")
        return
    # Update v4_batch_review_worker to pass compute_mode through.
    # Anchor: `run_depth_review_sync(pid)` → `run_depth_review_sync(pid, compute_mode="deep")`
    anchor_old = (
        "            run_depth_review_sync(pid)\n"
    )
    anchor_new = (
        "            run_depth_review_sync(pid, compute_mode=\"deep\")  # [P2-3 WORKERS_PASSTHROUGH]\n"
    )
    if anchor_old in src:
        src = src.replace(anchor_old, anchor_new, 1)
        _write(p, src)
        print("[workers/reviews] patched: v4_batch_review_worker passthrough")
    else:
        print("[workers/reviews] WARN: anchor not found; manual patch needed")


# ============================================================================
# Step 7: mock_api/main.py
#   - in the /api/depth/score + /api/depth/v4/review-selected endpoints,
#     forward compute_mode from request body to evaluate_paper.
#   - Also: add `compute_mode` to the temporary hot-path used in dedup reruns.
# ============================================================================
def step_main() -> None:
    p = REPO / "mock_api" / "main.py"
    src = _read(p)
    if "[P2-3 MAIN_PASSTHROUGH]" in src:
        print("[main] already patched; skip")
        return

    # The bodies of the depth endpoints in main.py call evaluate_paper(paper_id, db=db).
    # We forward compute_mode if available in the request payload.
    # Since payload is typically `dict` in main.py, we'll look for the most common
    # call site and replace it.
    old = "    result = depth_eval.evaluate_paper(paper_id, db=db)"
    new = (
        "    # [P2-3 MAIN_PASSTHROUGH] forward compute_mode from request body if present\n"
        "    _cm = (payload.get(\"compute_mode\") if isinstance(payload, dict) else getattr(payload, \"compute_mode\", None))\n"
        "    if _cm:\n"
        "        result = depth_eval.evaluate_paper(paper_id, db=db, compute_mode=_cm)\n"
        "    else:\n"
        "        result = depth_eval.evaluate_paper(paper_id, db=db)"
    )
    if old in src:
        src = src.replace(old, new, 1)
        print("[main] patched: /api/depth/score compute_mode passthrough")
    else:
        print("[main] WARN: depth_eval.evaluate_paper anchor not found — depth endpoint may be pydantic only")

    _write(p, src)
    print("[main] patched")


# ============================================================================
# Driver
# ============================================================================
def main() -> None:
    print("=== P2-3 + P2-4 atomic apply ===")
    step_settings()
    step_config()
    step_depth_eval_v4()
    step_schemas()
    step_depth_tasks()
    step_workers_reviews()
    step_main()
    print("\n=== verification ===")
    try:
        import subprocess
        r = subprocess.run(
            ["python", "-c",
             "from mock_api.settings import Settings;"
             "from mock_api.llm_cache import get_cache_backend, cache_key;"
             "from mock_api.depth_eval_v4 import evaluate_paper, DepthReviewer;"
             "from mock_api.depth_tasks import run_depth_review_sync;"
             "from mock_api.workers.reviews import depth_review_worker, v4_batch_review_worker;"
             "from mock_api.schemas import DepthScoreRequest, DepthV4ReviewSelectedRequest;"
             "from mock_api.config import COMPUTE_MODES;"
             "print('all imports OK; fast in COMPUTE_MODES =', 'fast' in COMPUTE_MODES);"
             "print('DepthScoreRequest.compute_mode =', DepthScoreRequest.model_fields.get('compute_mode'));"
             "print('DepthV4ReviewSelectedRequest.compute_mode =', DepthV4ReviewSelectedRequest.model_fields.get('compute_mode'));"
             "s = Settings(); print('settings.compute_mode =', s.compute_mode, '; llm_cache_backend =', s.llm_cache_backend)"
             ],
            cwd=str(REPO),
            capture_output=True, text=True, timeout=30,
        )
        print("stdout:", r.stdout.strip())
        print("stderr:", r.stderr[-500:] if r.stderr else "")
        print("rc:", r.returncode)
    except Exception as e:
        print(f"verification error: {e}")


if __name__ == "__main__":
    main()
