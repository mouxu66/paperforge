"""P2-3 + P2-4 surgical plumbing — stable ASCII anchor version.

Targets ONLY:
  - mock_api/settings.py: validator whitelist + cache backend fields
  - mock_api/depth_eval_v4.py: llm_cache import + DepthReviewer._compute_mode
  - mock_api/depth_tasks.py: signature + DepthReviewer kwarg passthrough

No fast-path runtime behavior — pure plumbing. Run pytest to verify.

Run from repo root:
    python scripts/_p2_3_p2_4_fix.py
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write(p: Path, src: str) -> None:
    p.write_text(src, encoding="utf-8")


def _replace(src: str, old: str, new: str, *, count: int = 1) -> tuple[str, bool]:
    if old not in src:
        return src, False
    if src.count(old) < count:
        return src, False
    return src.replace(old, new, count), True


# ============================================================================
# Step 1 — settings.py
# ============================================================================
def step_settings() -> None:
    p = REPO / "mock_api" / "settings.py"
    src = _read(p)
    if "[P2-3 SETTINGS_FAST_VALIDATOR]" in src:
        print("[settings] already patched; skip")
        return

    # 1a. validator whitelist (ASCII parens anchor, verified)
    src, ok = _replace(
        src,
        'if v not in ("speed", "deep"):',
        'if v not in ("fast", "speed", "deep"):  # [P2-3 SETTINGS_FAST_VALIDATOR]',
    )
    if not ok:
        print("[settings] FAIL: validator anchor not found")
        return

    # 1b. error message (paired change)
    src, ok = _replace(
        src,
        "compute_mode 必须为 'speed' 或 'deep'",
        "compute_mode 必须为 'fast'/'speed'/'deep'",
    )
    if not ok:
        print("[settings] FAIL: error msg anchor not found")
        return

    # 1c. Add llm_cache_backend + redis_url fields RIGHT AFTER llm_cache_ttl block.
    #     Anchor: end of llm_cache_ttl Field declaration. We use a pair of lines
    #     preceding `provider_timeout:` (verified at L109).
    cache_block_existing = (
        "    llm_cache_ttl: float = Field(\n"
        "        default=300.0,\n"
        '        description="LLM 响应缓存 TTL（秒），0=禁用。",\n'
        "        ge=0,\n"
        "    )\n"
        "    provider_timeout: int = Field(\n"
    )
    cache_block_new = (
        "    llm_cache_ttl: float = Field(\n"
        "        default=300.0,\n"
        '        description="LLM 响应缓存 TTL（秒），0=禁用。",\n'
        "        ge=0,\n"
        "    )\n"
        "    llm_cache_backend: str = Field(  # [P2-3 SETTINGS_CACHE_BACKEND]\n"
        '        default="inprocess",\n'
        '        description="LLM 缓存 backend: inprocess / redis（redis 不可用时降级到 inprocess）。",\n'
        "    )\n"
        "    redis_url: str | None = Field(\n"
        "        default=None,\n"
        '        description="Redis 连接 URL，仅在 llm_cache_backend=redis 时生效。",\n'
        "    )\n"
        "    provider_timeout: int = Field(\n"
    )
    src, ok = _replace(src, cache_block_existing, cache_block_new, count=1)
    if not ok:
        print("[settings] FAIL: llm_cache_ttl block anchor not found")
        return

    _write(p, src)
    print("[settings] OK: validator + cache backend fields")


# ============================================================================
# Step 2 — depth_eval_v4.py
# ============================================================================
def step_depth_eval_v4() -> None:
    p = REPO / "mock_api" / "depth_eval_v4.py"
    src = _read(p)
    if "[P2-3 DLLM_CACHE_IMPORT]" in src:
        print("[depth_eval_v4] already patched; skip")
        return

    # 2a. Add llm_cache import after `from .llm import ...` line.
    src, ok = _replace(
        src,
        "from .llm import ChatMessage, get_factory\n",
        "from .llm import ChatMessage, get_factory\n"
        "from .llm_cache import cache_key as _llm_cache_key, get_cache_backend  # [P2-3 DLLM_CACHE_IMPORT]\n",
        count=1,
    )
    if not ok:
        print("[depth_eval_v4] FAIL: llm import anchor not found")
        return

    # 2b. Add self._compute_mode after the `self._llm = ...` line in DepthReviewer.__init__.
    init_anchor = (
        "        self._llm = llm_func or call_llm\n"
        "        self._logs: list[str] = []\n"
    )
    init_new = (
        "        self._llm = llm_func or call_llm\n"
        "        # [P2-3 COMPUTE_MODE_FLAG] 'fast' 将在 review() 中 short-circuit Q5 trio；"
        " 'speed'/'deep' 走完整流水线。\n"
        "        self._compute_mode = (compute_mode or \"deep\").strip().lower()\n"
        "        self._logs: list[str] = []\n"
    )
    src, ok = _replace(src, init_anchor, init_new, count=1)
    if not ok:
        print("[depth_eval_v4] FAIL: __init__ self._llm anchor not found")
        return

    _write(p, src)
    print("[depth_eval_v4] OK: import + self._compute_mode")


# ============================================================================
# Step 3 — depth_tasks.py
# ============================================================================
def step_depth_tasks() -> None:
    p = REPO / "mock_api" / "depth_tasks.py"
    src = _read(p)
    if "[P2-3 DTASKS_PASSTHROUGH]" in src:
        print("[depth_tasks] already patched; skip")
        return

    # 3a. Extend signature.
    old_sig = (
        "def run_depth_review_sync(paper_id: str) -> str:\n"
    )
    new_sig = (
        "def run_depth_review_sync(\n"
        "    paper_id: str,\n"
        "    compute_mode: str | None = None,  # [P2-3 DTASKS_PASSTHROUGH]\n"
        ") -> str:\n"
    )
    src, ok = _replace(src, old_sig, new_sig, count=1)
    if not ok:
        print("[depth_tasks] FAIL: signature anchor not found")
        return

    # 3b. Replace `DepthReviewer()` -> `DepthReviewer(compute_mode=compute_mode)`.
    # Only ONE such instance exists in this file (line 258 in baseline).
    src, ok = _replace(
        src,
        "reviewer = DepthReviewer()",
        "reviewer = DepthReviewer(compute_mode=compute_mode)",
        count=1,
    )
    if not ok:
        print("[depth_tasks] FAIL: DepthReviewer() anchor not found or multiple")
        return

    _write(p, src)
    print("[depth_tasks] OK: signature + DepthReviewer kwarg")


# ============================================================================
# Verification
# ============================================================================
def verify() -> None:
    import subprocess

    print("\n=== verification (compile + import smoke) ===")
    r = subprocess.run(
        [
            "python",
            "-c",
            (
                "import os;"
                "from mock_api.settings import Settings, get_settings, reset_settings;"
                "from mock_api.llm_cache import get_cache_backend, cache_key, reset_cache_backend_for_testing, InProcessLLMCacheBackend;"
                "reset_cache_backend_for_testing();"
                # validator: fast/speed/deep all accepted; other rejected
                "for cm in ('fast','speed','deep'):"
                "    os.environ['PAPERFORGE_COMPUTE_MODE']=cm;"
                "    reset_settings();"
                "    s=Settings(); assert s.compute_mode==cm, ('FAIL',cm,s.compute_mode);"
                "print('validator whitelist OK');"
                "os.environ.pop('PAPERFORGE_COMPUTE_MODE', None);"
                "reset_settings();"
                # cache backend
                "s=Settings(); assert s.llm_cache_backend=='inprocess'; assert s.redis_url is None;"
                "print('cache backend default OK');"
                # backend instance works (uses reset to avoid env leakage)
                "reset_cache_backend_for_testing();"
                "b=get_cache_backend(); assert isinstance(b, InProcessLLMCacheBackend) and b.name=='inprocess';"
                "print('cache backend singleton OK');"
                # depth_eval_v4 imports OK even with llm_cache import
                "from mock_api.depth_eval_v4 import DepthReviewer, evaluate_paper_v4, call_llm;"
                "r=DepthReviewer(compute_mode='fast'); assert r._compute_mode=='fast';"
                "r=DepthReviewer(compute_mode='deep'); assert r._compute_mode=='deep';"
                "print('DepthReviewer(compute_mode=) OK');"
                # depth_tasks signature
                "from mock_api.depth_tasks import run_depth_review_sync;"
                "import inspect; sig=inspect.signature(run_depth_review_sync);"
                "assert 'compute_mode' in sig.parameters;"
                "print('run_depth_review_sync signature OK');"
                # workers + schemas
                "from mock_api.workers.reviews import depth_review_worker, v4_batch_review_worker;"
                "from mock_api.schemas import DepthScoreRequest, DepthV4ReviewSelectedRequest;"
                "assert 'compute_mode' in DepthScoreRequest.model_fields;"
                "assert 'compute_mode' in DepthV4ReviewSelectedRequest.model_fields;"
                "print('workers + schemas OK');"
            ),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    print("stdout:", r.stdout.strip() or "(empty)")
    print("stderr (last 600):", (r.stderr or "")[-600:])
    print("return code:", r.returncode)
    return r.returncode == 0


def main() -> None:
    print("=== P2-3 + P2-4 surgical plumbing ===")
    step_settings()
    step_depth_eval_v4()
    step_depth_tasks()
    ok = verify()
    print("\n=== overall:", "PASS" if ok else "FAIL", "===")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
