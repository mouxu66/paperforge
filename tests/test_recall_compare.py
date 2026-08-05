"""retrieve_context recall integration test: old (keyword token matching) vs new (FTS5+RRF).

Runs 15 real queries against the actual database once (module-scoped),
and asserts reasonable recall overlap across multiple thresholds.
Skips if the database is empty (e.g., in CI without seed data).
"""
from __future__ import annotations

import pytest
from mock_api import crud
from mock_api.database import SessionLocal
from mock_api.models import Paper as PaperORM


# -- Old retrieve_context logic (standalone reference implementation) --
def _old_retrieve_context(db, question, paper_ids, top_k=5):
    rows = db.query(PaperORM)
    if paper_ids:
        rows = rows.filter(PaperORM.id.in_(paper_ids))
    rows = rows.all()
    q_lower = (question or "").lower()
    tokens = [t for t in q_lower.split() if len(t) >= 2]

    def score(p):
        s = 0
        title = (p.title or "").lower()
        abstract = (p.abstract or "").lower()
        for t in tokens:
            if t in title:
                s += 3
            if t in abstract:
                s += 1
        return s

    if tokens:
        rows.sort(key=score, reverse=True)
        rows = [p for p in rows if score(p) > 0][:top_k]
    else:
        rows = rows[:top_k]
    return [crud.paper_to_schema(p) for p in rows]


_QUERIES = [
    "attention mechanism",
    "transformer",
    "LoRA fine-tuning",
    "quantization",
    "reinforcement learning human feedback",
    "graph neural network",
    "image generation diffusion",
    "large language model reasoning",
    "LoRa wireless communication",
    "mathematical reasoning",
    "few-shot learning GPT",
    "biomedical question answering",
    "efficient adapter tuning multimodal",
    "deep reinforcement learning",
    "attention",
]

_TOP_K = 5


@pytest.fixture(scope="module")
def recall_results():
    """Run old vs new retrieve_context for all 15 queries once (module-scoped).

    Returns a dict with per-query results and aggregate stats.
    """
    db = SessionLocal()
    try:
        count = db.query(PaperORM).count()
        if count == 0:
            pytest.skip("Database is empty; cannot run recall integration test")

        per_query = []
        total_overlap = 0
        total_old = 0
        total_new = 0
        for q in _QUERIES:
            old = _old_retrieve_context(db, q, [], _TOP_K)
            new = crud.retrieve_context(db, q, [], _TOP_K)
            old_ids = {p.id for p in old}
            new_ids = {p.id for p in new}
            overlap = old_ids & new_ids
            total_overlap += len(overlap)
            total_old += len(old)
            total_new += len(new)
            per_query.append({
                "query": q,
                "old_count": len(old),
                "new_count": len(new),
                "overlap": len(overlap),
                "old_only": len(old_ids - new_ids),
                "new_only": len(new_ids - old_ids),
            })
        denom = max(total_old + total_new - total_overlap, 1)
        overlap_pct = total_overlap / max(total_old, 1) * 100
        jaccard = total_overlap / denom
        return {
            "per_query": per_query,
            "total_overlap": total_overlap,
            "total_old": total_old,
            "total_new": total_new,
            "overlap_pct": overlap_pct,
            "jaccard": jaccard,
        }
    finally:
        db.close()


def test_recall_no_zero_overlap_queries(recall_results):
    """Every query should have at least some overlap between old and new methods."""
    zero = [item["query"] for item in recall_results["per_query"] if item["overlap"] == 0]
    assert zero == [], f"Zero-overlap queries: {zero}"


def test_recall_overlap_rate_acceptable(recall_results):
    """Overall recall overlap between old and new should be >= 30%."""
    pct = recall_results["overlap_pct"]
    assert pct >= 30, f"Recall overlap {pct:.0f}% < 30% threshold"


def test_recall_jaccard_reasonable(recall_results):
    """Jaccard similarity between old and new should be >= 0.15."""
    j = recall_results["jaccard"]
    assert j >= 0.15, f"Jaccard {j:.3f} < 0.15 threshold"
