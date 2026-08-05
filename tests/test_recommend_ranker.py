"""Tests for mock_api.recommend_ranker scoring utilities."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest
from mock_api.recommend_ranker import (
    RecommendConfig,
    RecommendResult,
    compute_authority,
    compute_citable,
    compute_match,
    compute_recency,
    mmr_rerank,
    recommend,
)


class TestComputeMatch:
    """compute_match clamps cosine similarity from [-1, 1] to [0, 1]."""

    def test_negative_cosine_clamped_to_zero(self, monkeypatch):
        """Cosine similarity -1.0 maps to 0.0."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: -1.0
        )
        result = compute_match([1.0, 0.0], [0.0, 1.0], "", "")
        assert result == pytest.approx(0.0)

    def test_positive_cosine_clamped_to_one(self, monkeypatch):
        """Cosine similarity 1.0 maps to 1.0."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: 1.0
        )
        result = compute_match([1.0, 0.0], [1.0, 0.0], "", "")
        assert result == pytest.approx(1.0)

    def test_zero_cosine_maps_to_half(self, monkeypatch):
        """Cosine similarity 0.0 maps to 0.5."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: 0.0
        )
        result = compute_match([1.0, 0.0], [1.0, 1.0], "", "")
        assert result == pytest.approx(0.5)

    def test_negative_cosine_maps_linearly(self, monkeypatch):
        """Cosine similarity -0.5 maps to 0.25."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: -0.5
        )
        result = compute_match([1.0, 0.0], [0.0, 1.0], "", "")
        assert result == pytest.approx(0.25)

    def test_fallback_jaccard_when_no_vectors(self):
        """When vectors are unavailable, fall back to keyword Jaccard overlap."""
        context = "transformer attention mechanism"
        abstract = "we propose a transformer attention mechanism for nlp"
        result = compute_match(None, None, context, abstract)
        assert 0.0 < result <= 1.0


class TestComputeAuthority:
    """compute_authority returns values clamped to [0, 1]."""

    def test_authority_with_max_citations(self):
        """When citations equal the max, c_norm=1 and authority is bounded."""
        config = RecommendConfig()
        score, _ = compute_authority(100, "Nature", 100, config)
        assert 0.0 <= score <= 1.0

    def test_authority_with_zero_citations(self):
        """Zero citations yields c_norm=0 but journal tier keeps score >= 0."""
        config = RecommendConfig()
        score, _ = compute_authority(0, "NeurIPS", 100, config)
        assert 0.0 <= score <= 1.0

    def test_authority_with_missing_journal(self):
        """Missing journal falls back to base score without exceeding 1."""
        config = RecommendConfig()
        score, _ = compute_authority(50, "", 100, config)
        assert 0.0 <= score <= 1.0

    def test_authority_disabled_oim(self):
        """When OIM is disabled, authority returns 0."""
        config = RecommendConfig(oim_enabled=False)
        score, _ = compute_authority(100, "Nature", 100, config)
        assert score == 0.0


class TestMmrRerank:
    """mmr_rerank edge cases for lambda values."""

    def test_lambda_one_returns_pure_relevance_order(self, monkeypatch):
        """lambda=1 should sort items strictly by score descending."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: 0.0
        )
        items = [("a", 0.3, None), ("b", 0.9, None), ("c", 0.5, None)]
        result = mmr_rerank(items, 1.0)
        assert result == ["b", "c", "a"]

    def test_lambda_zero_prefers_diversity(self, monkeypatch):
        """lambda=0 should pick the most dissimilar item next."""

        def fake_sim(a, b):
            # a and b are highly similar; c is dissimilar from both
            if tuple(a) == (1.0,) and tuple(b) == (2.0,):
                return 0.9
            if tuple(a) == (2.0,) and tuple(b) == (1.0,):
                return 0.9
            return 0.1

        monkeypatch.setattr("mock_api.recommend_ranker.cosine_similarity", fake_sim)
        items = [("a", 0.5, [1.0]), ("b", 0.5, [2.0]), ("c", 0.5, [3.0])]
        result = mmr_rerank(items, 0.0)
        assert result[0] == "a"  # first item when all scores equal
        assert result[1] == "c"  # c is most dissimilar to a
        assert result[2] == "b"

    def test_lambda_zero_single_item(self, monkeypatch):
        """lambda=0 with a single item returns that item."""
        monkeypatch.setattr(
            "mock_api.recommend_ranker.cosine_similarity", lambda a, b: 0.0
        )
        items = [("only", 0.5, None)]
        result = mmr_rerank(items, 0.0)
        assert result == ["only"]


class TestComputeRecency:
    """compute_recency returns exponential decay based on publication year."""

    def test_recent_paper_high_recency(self):
        """A paper from the current year should have recency close to 1.0."""
        config = RecommendConfig()
        score = compute_recency(2026, config, now_year=2026)
        assert score == pytest.approx(1.0)

    def test_old_paper_lower_recency(self):
        """A paper from 5 years ago should decay according to tau."""
        config = RecommendConfig(oim_recency_tau=5)
        score = compute_recency(2021, config, now_year=2026)
        assert score == pytest.approx(math.exp(-1))

    def test_future_year_clamped_to_now(self):
        """A paper with a future year is treated as published this year."""
        config = RecommendConfig()
        score = compute_recency(2030, config, now_year=2026)
        assert score == pytest.approx(1.0)

    def test_missing_year_returns_zero(self):
        """Missing year returns 0 recency."""
        config = RecommendConfig()
        score = compute_recency(None, config, now_year=2026)
        assert score == 0.0

    def test_oim_disabled_returns_zero(self):
        """When OIM is disabled, recency returns 0."""
        config = RecommendConfig(oim_enabled=False)
        score = compute_recency(2026, config, now_year=2026)
        assert score == 0.0


class TestComputeCitable:
    """compute_citable returns a score and note for citation suitability."""

    def test_missing_meta_returns_low_score(self):
        """When meta is None, citable returns a low fallback score."""
        score, note = compute_citable(None, None)
        assert 0.0 <= score <= 0.5
        assert "无元数据" in note

    def test_heuristic_with_method_keywords(self):
        """Abstract with method keywords scores higher."""
        meta = {"abstract": "We propose a new method and achieve 95% accuracy."}
        score, note = compute_citable(meta, None)
        assert score > 0.4
        assert "启发式" in note

    def test_heuristic_without_abstract_returns_low(self):
        """Abstract missing returns low citable score."""
        meta = {"abstract": ""}
        score, note = compute_citable(meta, None)
        assert score <= 0.35
        assert "无摘要" in note

    def test_llm_model_id_uses_stub_and_falls_back(self, monkeypatch):
        """When model_id is provided, the LLM stub returns None and falls back."""
        meta = {"abstract": "We propose a transformer model."}
        score, note = compute_citable(meta, "gpt-4")
        assert 0.0 <= score <= 1.0
        assert "启发式" in note


class TestRecommend:
    """Full recommend() flow with mocked database and vector dependencies."""

    def test_empty_candidates_returns_empty(self):
        """Empty candidate list returns empty results."""
        db = MagicMock()
        results = recommend(db, "context", [], RecommendConfig())
        assert results == []

    def test_recommend_returns_results(self, monkeypatch):
        """ recommend() returns scored and tiered results for candidates."""
        db = MagicMock()
        config = RecommendConfig()

        # Mock _get_paper_meta to return deterministic metadata
        def fake_meta(db, pid):
            return {
                "id": pid,
                "title": f"Paper {pid}",
                "abstract": "We propose a method and achieve 95% accuracy.",
                "year": 2026,
                "citations": 10,
                "journal": "Nature",
            }

        monkeypatch.setattr("mock_api.recommend_ranker._get_paper_meta", fake_meta)
        monkeypatch.setattr("mock_api.recommend_ranker._get_embedding_by_ids", lambda db, ids: {})
        monkeypatch.setattr("mock_api.recommend_ranker.vector_available", lambda: False)

        results = recommend(db, "context", ["p1", "p2"], config)

        assert len(results) == 2
        for r in results:
            assert isinstance(r, RecommendResult)
            assert 0.0 <= r.recommend_score <= 1.0
            assert r.tier in {"strong", "good", "optional"}
            assert "match" in r.dims
            assert "citable" in r.dims

    def test_recommend_mmr_reorders_results(self, monkeypatch):
        """MMR diversity reorders results when vectors are available."""
        db = MagicMock()
        config = RecommendConfig(diversity_mmr_lambda=0.0)

        def fake_meta(db, pid):
            return {
                "id": pid,
                "title": f"Paper {pid}",
                "abstract": "method and results",
                "year": 2026,
                "citations": 0,
                "journal": "",
            }

        monkeypatch.setattr("mock_api.recommend_ranker._get_paper_meta", fake_meta)
        # p1 and p2 are similar; p3 is dissimilar to both
        monkeypatch.setattr(
            "mock_api.recommend_ranker._get_embedding_by_ids",
            lambda db, ids: {"p1": [1.0], "p2": [1.0], "p3": [0.0]},
        )
        monkeypatch.setattr("mock_api.recommend_ranker.vector_available", lambda: True)

        # Mock cosine_similarity so p1/p2 are similar and p3 is dissimilar
        def fake_cosine(a, b):
            if a[0] == b[0]:
                return 1.0
            if (a[0] == 1.0 and b[0] == 0.0) or (a[0] == 0.0 and b[0] == 1.0):
                return 0.0
            return 0.9

        monkeypatch.setattr("mock_api.recommend_ranker.cosine_similarity", fake_cosine)
        # Mock embed_text so context_vec is produced
        monkeypatch.setattr("mock_api.recommend_ranker.embed_text", lambda x: [1.0])

        results = recommend(db, "context", ["p1", "p2", "p3"], config)
        assert len(results) == 3
        # With pure diversity (lambda=0), after picking p1, p3 is more diverse than p2
        assert results[0].paper_id == "p1"
        assert results[1].paper_id == "p3"
        assert results[2].paper_id == "p2"

    def test_recommend_excludes_missing_meta(self, monkeypatch):
        """Candidates with missing metadata are excluded from results."""
        db = MagicMock()

        def fake_meta(db, pid):
            if pid == "p1":
                return None
            return {
                "id": pid,
                "title": f"Paper {pid}",
                "abstract": "method and results",
                "year": 2026,
                "citations": 0,
                "journal": "",
            }

        monkeypatch.setattr("mock_api.recommend_ranker._get_paper_meta", fake_meta)
        monkeypatch.setattr("mock_api.recommend_ranker._get_embedding_by_ids", lambda db, ids: {})
        monkeypatch.setattr("mock_api.recommend_ranker.vector_available", lambda: False)

        results = recommend(db, "context", ["p1", "p2"], RecommendConfig())
        assert len(results) == 1
        assert results[0].paper_id == "p2"
