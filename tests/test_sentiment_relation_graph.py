"""Tests for WP-2.2 sentiment extraction and relation graph.

WP-2.2 升级后，重点测试「被引情感」相关逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from mock_api.crud.analysis import (
    _find_citation_contexts,
    extract_citation_sentiments_for_target,
    get_relation_graph,
)
from mock_api.models import CitationSentiment, Paper
from sqlalchemy.orm import Session


class TestGetRelationGraph:
    def test_returns_none_for_missing_paper(self, db_session: Session):
        result = get_relation_graph(db_session, "nonexistent")
        assert result is None

    def test_returns_graph_with_center_node(self, db_session: Session):
        paper = Paper(
            id="p3",
            title="Machine learning for graphs",
            abstract="We propose a novel method.",
            authors=["Alice"],
            year=2024,
        )
        db_session.add(paper)
        db_session.commit()

        result = get_relation_graph(db_session, "p3", top_k=3)
        assert result is not None
        assert len(result["nodes"]) >= 1
        assert result["nodes"][0]["id"] == "p3"
        assert "edges" in result


class TestRelationGraphEndpoint:
    def test_relation_graph_endpoint(self, client: TestClient, db_session: Session):
        paper = Paper(
            id="p4",
            title="Deep learning for NLP",
            abstract="A novel approach.",
            authors=["Bob"],
            year=2024,
        )
        db_session.add(paper)
        db_session.commit()

        response = client.get("/api/papers/p4/relation-graph")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert data["nodes"][0]["id"] == "p4"

    def test_relation_graph_endpoint_not_found(self, client: TestClient):
        response = client.get("/api/papers/nonexistent/relation-graph")
        assert response.status_code == 404


class TestFindCitationContexts:
    def test_title_match(self):
        full_text = (
            "Previous work on neural networks is important. "
            "In this paper we build upon Deep Learning for NLP. "
            "Our results show significant improvements."
        )
        contexts = _find_citation_contexts(full_text, "Deep Learning for NLP", window=50)
        assert len(contexts) == 1
        assert "Deep Learning for NLP" in contexts[0][1]

    def test_author_year_match(self):
        full_text = "As shown by Smith et al. (2020), the method works well."
        contexts = _find_citation_contexts(full_text, "Some Title", ["John Smith"], 2020, window=30)
        assert len(contexts) == 1
        assert "Smith et al. (2020)" in contexts[0][1]

    def test_no_match(self):
        contexts = _find_citation_contexts("Some unrelated text.", "Missing Title")
        assert contexts == []


class TestExtractCitationSentiments:
    def test_extracts_and_saves_sentiment(self, db_session: Session):
        target = Paper(
            id="target1",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        source = Paper(
            id="source1",
            title="Follow-up Study",
            abstract="We build upon Deep Learning for NLP and improve it.",
            authors=["Bob"],
            year=2022,
            full_text="We build upon Deep Learning for NLP and improve it significantly.",
        )
        db_session.add_all([target, source])
        db_session.commit()

        with patch("mock_api.crud.analysis.get_factory") as mock_factory:
            mock_provider = MagicMock()
            mock_provider.chat.return_value = MagicMock(
                content='{"intent": "support", "confidence": 0.9}'
            )
            mock_factory.return_value.get_provider.return_value = mock_provider
            result = extract_citation_sentiments_for_target(db_session, "target1")

        assert result["processed"] == 1
        assert result["saved"] == 1
        assert result["failed"] == 0

        record = db_session.query(CitationSentiment).filter_by(target_paper_id="target1").first()
        assert record is not None
        assert record.sentiment_label == "support"
        assert record.confidence == 0.9
        assert record.source_paper_id == "source1"

    def test_no_full_text_no_processing(self, db_session: Session):
        target = Paper(
            id="target2",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        source = Paper(
            id="source2",
            title="Follow-up Study",
            abstract="No full text.",
            authors=["Bob"],
            year=2022,
        )
        db_session.add_all([target, source])
        db_session.commit()

        result = extract_citation_sentiments_for_target(db_session, "target2")
        assert result["processed"] == 0


class TestRelationGraphWithCitationSentiment:
    def test_uses_citation_sentiment_records(self, db_session: Session):
        target = Paper(
            id="target3",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        source = Paper(
            id="source3",
            title="Follow-up Study",
            abstract="Builds upon target.",
            authors=["Bob"],
            year=2022,
        )
        db_session.add_all([target, source])
        db_session.commit()

        sentiment = CitationSentiment(
            source_paper_id="source3",
            target_paper_id="target3",
            sentiment_label="criticize",
            confidence=0.85,
            context_snippet="We identify several limitations.",
        )
        db_session.add(sentiment)
        db_session.commit()

        result = get_relation_graph(db_session, "target3")
        assert result is not None
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["id"] == "target3"
        assert result["nodes"][1]["id"] == "source3"
        assert result["nodes"][1]["sentimentLabel"] == "criticize"
        assert len(result["edges"]) == 1
        assert result["edges"][0]["type"] == "citation"
        assert result["edges"][0]["sentiment"] == "criticize"

    def test_relation_graph_mode_citation(self, db_session: Session):
        target = Paper(
            id="target_mode_citation",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        source = Paper(
            id="source_mode_citation",
            title="Follow-up Study",
            abstract="Builds upon target.",
            authors=["Bob"],
            year=2022,
        )
        db_session.add_all([target, source])
        db_session.commit()

        sentiment = CitationSentiment(
            source_paper_id="source_mode_citation",
            target_paper_id="target_mode_citation",
            sentiment_label="support",
            confidence=0.9,
            context_snippet="Great work.",
        )
        db_session.add(sentiment)
        db_session.commit()

        result = get_relation_graph(db_session, "target_mode_citation")
        assert result is not None
        assert result["mode"] == "citation"

    def test_relation_graph_mode_similarity_fallback(self, db_session: Session):
        target = Paper(
            id="target_mode_similarity",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        db_session.add(target)
        db_session.commit()

        result = get_relation_graph(db_session, "target_mode_similarity")
        assert result is not None
        assert result["mode"] == "similarity"


class TestCitationSentimentEndpoint:
    def test_extract_citation_sentiment_endpoint(self, client: TestClient, db_session: Session):
        target = Paper(
            id="target4",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        db_session.add(target)
        db_session.commit()

        response = client.post("/api/papers/target4/extract-citation-sentiment")
        assert response.status_code == 200
        data = response.json()
        assert data["paperId"] == "target4"
        assert "taskId" in data

    def test_extract_citation_sentiment_endpoint_deduplication(
        self, client: TestClient, db_session: Session
    ):
        target = Paper(
            id="target4_dup",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        db_session.add(target)
        db_session.commit()

        with patch("mock_api.tasks.TaskManager") as mock_tm:
            existing_task_id = "existing-task-id"
            mock_tm.list.return_value = [
                {
                    "id": existing_task_id,
                    "status": "pending",
                    "params": {"paperId": "target4_dup"},
                }
            ]
            response = client.post("/api/papers/target4_dup/extract-citation-sentiment")
            assert response.status_code == 200
            data = response.json()
            assert data["paperId"] == "target4_dup"
            assert data["taskId"] == existing_task_id
            assert data["status"] == "pending"
            # 确认没有提交新任务
            mock_tm.submit.assert_not_called()

    def test_get_sentiment_stats_endpoint(self, client: TestClient, db_session: Session):
        target = Paper(
            id="target5",
            title="Deep Learning for NLP",
            abstract="Important work.",
            authors=["Alice"],
            year=2020,
        )
        source = Paper(
            id="source5",
            title="Follow-up",
            abstract="Builds upon target.",
            authors=["Bob"],
            year=2022,
        )
        db_session.add_all([target, source])
        db_session.commit()

        sentiment = CitationSentiment(
            source_paper_id="source5",
            target_paper_id="target5",
            sentiment_label="support",
            confidence=0.9,
            context_snippet="Great work.",
        )
        db_session.add(sentiment)
        db_session.commit()

        response = client.get("/api/papers/target5/sentiment")
        assert response.status_code == 200
        data = response.json()
        assert data["paperId"] == "target5"
        assert data["counts"]["support"] == 1
