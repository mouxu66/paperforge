"""One-time batch plumb of compute_mode through schemas / depth_tasks / workers / main / evaluate_paper / tests.

Why script vs str_replace:
- Anchor text contains unicode (CJK + fullwidth parens + em dash);
  str_replace exact-match is fragile to encoding mismatches between tools.
- This script uses pattern-based block-level rewrites for robustness.

Idempotent: each step checks for a marker string first; if present, skip.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------
# Step 1 — depth_eval_v4.py: evaluate_paper signature + DepthReviewer() inline
# ----------------------------------------------------------------------------
def patch_evaluate_paper() -> None:
    path = REPO / "mock_api" / "depth_eval_v4.py"
    src = path.read_text(encoding="utf-8")
    if "[P2-3 COMPUTE_MODE_PASSTHROUGH]" in src:
        print("[evaluate_paper] already patched; skip")
        return
    # 1a. change signature
    src = src.replace(
        "def evaluate_paper(paper_id: str, db: _Session | None = None) -> dict[str, Any]:",
        "def evaluate_paper(\n"
        "    paper_id: str,\n"
        "    db: _Session | None = None,\n"
        "    compute_mode: str | None = None,  # [P2-3 COMPUTE_MODE_PASSTHROUGH]\n"
        ") -> dict[str, Any]:",
        1,
    )
    # 1b. inject near end of evaluate_paper, before final return: pass compute_mode
    # We don't know the exact body ahead; instead, find "DepthReviewer()" and
    # add compute_mode= arg.
    pattern_depthreviewer = re.compile(r"DepthReviewer\(\)")
    # find after "evaluate_paper" def line — search inside the function body
    needle = "def evaluate_paper("
    idx = src.find(needle)
    if idx < 0:
        print("[evaluate_paper] function not found; manual patch needed")
        return
    # Find the *first* DepthReviewer() AFTER the def position
    next_drev = pattern_depthreviewer.search(src, idx)
    if next_drev is None:
        print("[evaluate_paper] DepthReviewer() call site not found")
        return
    # Replace DepthReviewer() -> DepthReviewer(compute_mode=...)
    # We use a unique marker just before the search target to anchor the patch
    # Easier: just replace ALL `DepthReviewer()` literals (since the only
    # sites we own — evaluate_paper / run_depth_review_sync via the worker —
    # are the ones we want to upgrade).  In tests depth_eval_v4 also has
    # DepthReviewer(compute_mode="deep") already — those are unaffected.
    src = src.replace("DepthReviewer()", "DepthReviewer(compute_mode=compute_mode)")
    path.write_text(src, encoding="utf-8")
    print("[evaluate_paper] patched signature + DepthReviewer() call → compute_mode=compute_mode")


# ----------------------------------------------------------------------------
# Step 2 — depth_tasks.py: run_depth_review_sync signature pass-through
# ----------------------------------------------------------------------------
def patch_run_depth_review_sync() -> None:
    path = REPO / "mock_api" / "depth_tasks.py"
    src = path.read_text(encoding="utf-8")
    if "compute_mode: str = \"deep\"" in src:
        print("[run_depth_review_sync] already patched; skip")
        return
    # 2a. signature change
    src = src.replace(
        "def run_depth_review_sync(paper_id: str) -> str:",
        "def run_depth_review_sync(\n"
        "    paper_id: str,\n"
        "    compute_mode: str = \"deep\",  # [P2-3 COMPUTE_MODE_PASSTHROUGH]\n"
        ") -> str:",
        1,
    )
    # 2b. DepthReviewer() → DepthReviewer(compute_mode=compute_mode) inside this file
    src = src.replace(
        "reviewer = DepthReviewer()",
        "reviewer = DepthReviewer(compute_mode=compute_mode)",
        1,
    )
    path.write_text(src, encoding="utf-8")
    print("[run_depth_review_sync] patched signature + DepthReviewer() call")


# ----------------------------------------------------------------------------
# Step 3 — workers/reviews.py: depth_review_worker extracts computeMode param
# ----------------------------------------------------------------------------
def patch_reviews_worker() -> None:
    path = REPO / "mock_api" / "workers" / "reviews.py"
    src = path.read_text(encoding="utf-8")
    if "computeMode" in src and "compute_mode=mode" in src:
        print("[reviews worker] already patched; skip")
        return
    # 3a. After `paper_id = params.get("paperId", "")` add extraction
    src = src.replace(
        'paper_id = params.get("paperId", "")',
        (
            'paper_id = params.get("paperId", "")\n'
            '    compute_mode = (params.get("computeMode") or params.get("compute_mode") or "deep")'
            '  # [P2-3 COMPUTE_MODE_PASSTHROUGH]\n'
            '    compute_mode = str(compute_mode).strip().lower() or "deep"'
        ),
        1,
    )
    # 3b. replace `run_depth_review_sync(paper_id)` → pass compute_mode
    # Each worker fn has its own call site; we replace all occurrences
    src = re.sub(
        r"run_depth_review_sync\(\s*paper_id\s*\)",
        "run_depth_review_sync(paper_id, compute_mode=compute_mode)",
        src,
    )
    path.write_text(src, encoding="utf-8")
    print("[reviews worker] patched computeMode extraction + run_depth_review_sync call")


# ----------------------------------------------------------------------------
# Step 4 — main.py: 3 endpoint bodies plumb compute_mode
# ----------------------------------------------------------------------------
def patch_main_endpoints() -> None:
    path = REPO / "mock_api" / "main.py"
    src = path.read_text(encoding="utf-8")
    if "[COMPUTE_MODE_ENDPOINT_PASSTHROUGH]" in src:
        print("[main endpoints] already patched; skip")
        return
    # 4a. depth_score_single: pass payload.compute_mode to evaluate_paper
    src = src.replace(
        "result = depth_eval.evaluate_paper(paper_id, db=db)",
        (
            "result = depth_eval.evaluate_paper(\n"
            "        paper_id, db=db, compute_mode=payload.compute_mode  # [COMPUTE_MODE_ENDPOINT_PASSTHROUGH]\n"
            "    )"
        ),
        1,
    )
    # 4b. _submit_single_v4_review: pass computeMode into TaskManager params
    # The current signature is `_submit_single_v4_review(paper_id, db)`;
    # we extend to accept computeMode.
    src = src.replace(
        "def _submit_single_v4_review(paper_id: str, db: Session) -> tuple[str | None, str | None]:",
        (
            "def _submit_single_v4_review(\n"
            "    paper_id: str,\n"
            "    db: Session,\n"
            "    compute_mode: str = \"deep\",  # [COMPUTE_MODE_ENDPOINT_PASSTHROUGH]\n"
            ") -> tuple[str | None, str | None]:"
        ),
        1,
    )
    # 4c. Inside _submit_single_v4_review, params dict includes computeMode
    src = src.replace(
        'params={"paperId": paper_id},',
        'params={"paperId": paper_id, "computeMode": compute_mode},',
        1,
    )
    # 4d. depth_v4_start_review endpoint: now reads ?compute_mode= query param
    # Insertion: change signature to accept compute_mode: str = "deep"
    src = src.replace(
        "async def depth_v4_start_review(paper_id: str, db: Session = Depends(get_db)) -> dict:",
        (
            "async def depth_v4_start_review(\n"
            "    paper_id: str,\n"
            "    compute_mode: str = Query(\"deep\", description=\"算力模式：deep/speed/fast；fast 跳过 Q5a/b/c\"),\n"
            "    db: Session = Depends(get_db),\n"
            ") -> dict:"
        ),
        1,
    )
    src = src.replace(
        "task_id, skip_reason = _submit_single_v4_review(paper_id, db)",
        "task_id, skip_reason = _submit_single_v4_review(paper_id, db, compute_mode=compute_mode)",
        1,
    )
    # 4e. depth_v4_review_selected: insert compute_mode param into payload body
    # The function takes `payload: DepthV4ReviewSelectedRequest`. We add field accessor.
    src = src.replace(
        "async def depth_v4_review_selected(\n    request: DepthV4ReviewSelectedRequest,",
        (
            "async def depth_v4_review_selected(\n"
            "    request: DepthV4ReviewSelectedRequest,\n"
            "    compute_mode: str = Query(\"deep\", description=\"算力模式：deep/speed/fast\"),"
        ),
        1,
    )
    # 4f. Inside depth_v4_review_selected, params dict includes computeMode
    src = src.replace(
        'params={"paperId": p.id},',
        'params={"paperId": p.id, "computeMode": compute_mode},',
    )
    path.write_text(src, encoding="utf-8")
    print("[main endpoints] plumbed compute_mode through 3 endpoints")


# ----------------------------------------------------------------------------
# Step 5 — schemas.py: add compute_mode to DepthScoreRequest + add field to
# DepthV4ReviewSelectedRequest (revert earlier accidental DepthTaskModeLiteral)
# ----------------------------------------------------------------------------
def patch_schemas() -> None:
    path = REPO / "mock_api" / "schemas.py"
    src = path.read_text(encoding="utf-8")
    if "[DEPTH_COMPUTE_MODE_FIELD]" in src:
        print("[schemas] already patched; skip")
        return
    # 5a. revert accidental DepthTaskModeLiteral top-level literal if present
    src = src.replace(
        "# 拓扑模式: 用于 depth_v4 review-selected (推导 task_mode 决定后续调度)\n"
        "DepthTaskModeLiteral = Literal[\"deep\", \"speed\", \"fast\"]\n\n\n",
        "",
        1,
    )
    # 5b. add compute_mode to DepthScoreRequest (use add-after-pattern)
    src = src.replace(
        "class DepthScoreRequest(BaseModel):\n    paper_id: str",
        (
            "class DepthScoreRequest(BaseModel):\n"
            "    paper_id: str\n"
            "    # [DEPTH_COMPUTE_MODE_FIELD] 性能 P2-3\n"
            "    compute_mode: Literal[\"deep\", \"speed\", \"fast\"] | None = Field(\n"
            "        default=None,\n"
            "        description=\"算力模式。None=用 settings.compute_mode 全局默认；fast 跳过 Q5a/b/c。\",\n"
            "    )"
        ),
        1,
    )
    # 5c. add compute_mode to DepthV4ReviewSelectedRequest
    src = src.replace(
        'class DepthV4ReviewSelectedRequest(BaseModel):\n    paper_ids: list[str] = Field(',
        (
            'class DepthV4ReviewSelectedRequest(BaseModel):\n'
            '    paper_ids: list[str] = Field('
        ),
        1,
    )
    # find paper_ids Field() closing - inject after paper_ids Field
    src = src.replace(
        (
            "    paper_ids: list[str] = Field(\n"
            "        default_factory=list,\n"
            "        description=\"待审稿论文 ID 列表（1--50 个）\",\n"
            "        min_length=1,\n"
            "        max_length=50,\n"
            "    )"
        ),
        (
            "    paper_ids: list[str] = Field(\n"
            "        default_factory=list,\n"
            "        description=\"待审稿论文 ID 列表（1--50 个）\",\n"
            "        min_length=1,\n"
            "        max_length=50,\n"
            "    )\n"
            "    # [DEPTH_COMPUTE_MODE_FIELD]\n"
            "    compute_mode: Literal[\"deep\", \"speed\", \"fast\"] | None = Field(\n"
            "        default=None,\n"
            "        description=\"算力模式，None=全局默认；fast 跳过 Q5a/b/c。\",\n"
            "    )"
        ),
        1,
    )
    path.write_text(src, encoding="utf-8")
    print("[schemas] added compute_mode fields")


def main() -> None:
    patch_evaluate_paper()
    patch_run_depth_review_sync()
    patch_reviews_worker()
    patch_main_endpoints()
    patch_schemas()
    print("[done] all compute_mode plumbs applied (idempotent safe to re-run)")


if __name__ == "__main__":
    main()
