"""Tests for paper CRUD and WP-5.2 duplicate merge functionality."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mock_api.crud.papers import find_duplicate_groups, merge_papers
from mock_api.models import Paper
from sqlalchemy.orm import Session


def _make_paper(
    db: Session,
    paper_id: str,
    title: str = "Test Paper",
    authors: list[str] | None = None,
    year: int = 2024,
    source: str = "arxiv",
) -> Paper:
    paper = Paper(
        id=paper_id,
        title=title,
        authors=authors or ["Alice"],
        year=year,
        abstract="Abstract text",
        category="arxiv",
        tags=["ai"],
        citations=10,
        chunk_count=5,
        index_size=1024,
        pdf_url="",
        source=source,
    )
    db.add(paper)
    db.commit()
    return paper


class TestFindDuplicateGroups:
    def test_no_duplicates(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Paper One", authors=["Alice"])
        _make_paper(db_session, "p2", title="Paper Two", authors=["Bob"])
        groups = find_duplicate_groups(db_session)
        assert groups == []

    def test_finds_duplicate_group(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Duplicate Title", authors=["Alice", "Bob"])
        _make_paper(db_session, "p2", title="Duplicate Title", authors=["Bob", "Carol"])
        groups = find_duplicate_groups(db_session)
        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert {p.id for p in groups[0]} == {"p1", "p2"}

    def test_title_too_short_ignored(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="ABC", authors=["Alice"])
        _make_paper(db_session, "p2", title="ABC", authors=["Alice"])
        groups = find_duplicate_groups(db_session)
        assert groups == []

    def test_no_author_overlap_ignored(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Duplicate Title", authors=["Alice"])
        _make_paper(db_session, "p2", title="Duplicate Title", authors=["Bob"])
        groups = find_duplicate_groups(db_session)
        assert groups == []

    def test_transitive_closure(self, db_session: Session) -> None:
        """A~B and B~C should be grouped together even if A and C don't share authors."""
        _make_paper(db_session, "p1", title="Same Title", authors=["Alice"])
        _make_paper(db_session, "p2", title="Same Title", authors=["Alice", "Bob"])
        _make_paper(db_session, "p3", title="Same Title", authors=["Bob"])
        groups = find_duplicate_groups(db_session)
        assert len(groups) == 1
        assert {p.id for p in groups[0]} == {"p1", "p2", "p3"}


class TestMergePapers:
    def test_merge_basic_fields(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Title A", authors=["Alice"], year=2023)
        _make_paper(db_session, "p2", title="Title B", authors=["Bob"], year=2024)

        merged = merge_papers(
            db_session,
            target_id="p1",
            source_ids=["p2"],
            field_sources={"title": "p2", "year": "p2"},
        )

        assert merged is not None
        assert merged.id == "p1"
        assert merged.title == "Title B"
        assert merged.year == 2024
        # p2 should be deleted
        assert db_session.query(Paper).filter(Paper.id == "p2").first() is None

    def test_merge_keeps_unspecified_fields_from_target(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Title A", authors=["Alice"], year=2023)
        _make_paper(db_session, "p2", title="Title B", authors=["Bob"], year=2024)

        merged = merge_papers(
            db_session,
            target_id="p1",
            source_ids=["p2"],
            field_sources={"title": "p2"},
        )

        assert merged is not None
        assert merged.year == 2023  # from target
        assert merged.authors == ["Alice"]  # from target

    def test_merge_missing_target_returns_none(self, db_session: Session) -> None:
        result = merge_papers(db_session, target_id="missing", source_ids=["p2"], field_sources={})
        assert result is None

    def test_merge_missing_source_ignored(self, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Title A", authors=["Alice"])
        merged = merge_papers(db_session, target_id="p1", source_ids=["missing"], field_sources={})
        assert merged is not None
        assert merged.id == "p1"

    def test_merge_aggregates_citations_and_index(self, db_session: Session) -> None:
        p1 = _make_paper(db_session, "p1", title="Same", authors=["Alice"], year=2023)
        p1.citations = 5
        p1.chunk_count = 3
        p1.index_size = 100
        p2 = _make_paper(db_session, "p2", title="Same", authors=["Alice"], year=2024)
        p2.citations = 15
        p2.chunk_count = 7
        p2.index_size = 200
        db_session.commit()

        merged = merge_papers(db_session, target_id="p1", source_ids=["p2"], field_sources={})

        assert merged is not None
        assert merged.citations == 15
        assert merged.chunkCount == 7
        assert merged.indexSize == 200


class TestDuplicateGroupsEndpoint:
    def test_list_duplicate_groups(self, client: TestClient, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Duplicate Title", authors=["Alice"])
        _make_paper(db_session, "p2", title="Duplicate Title", authors=["Alice"])

        resp = client.get("/api/papers/duplicates")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups" in data
        assert len(data["groups"]) == 1
        assert len(data["groups"][0]["papers"]) == 2

    def test_list_duplicate_groups_empty(self, client: TestClient) -> None:
        resp = client.get("/api/papers/duplicates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["groups"] == []


class TestMergeEndpoint:
    def test_merge_endpoint(self, client: TestClient, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Duplicate Title", authors=["Alice"])
        _make_paper(db_session, "p2", title="Duplicate Title", authors=["Alice"])

        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": "p1",
                "source_ids": ["p2"],
                "field_sources": {"title": "p1", "year": "p1"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["target_id"] == "p1"
        assert data["deleted_ids"] == ["p2"]

    def test_merge_endpoint_target_not_in_group(self, client: TestClient, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Title A", authors=["Alice"])
        _make_paper(db_session, "p2", title="Title B", authors=["Bob"])

        resp = client.post(
            "api/papers/merge",
            json={
                "target_id": "p1",
                "source_ids": ["p2"],
                "field_sources": {},
            },
        )
        assert resp.status_code == 400

    def test_merge_endpoint_missing_target(self, client: TestClient) -> None:
        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": "missing",
                "source_ids": ["p2"],
                "field_sources": {},
            },
        )
        assert resp.status_code == 404

    def test_merge_endpoint_source_equals_target(self, client: TestClient, db_session: Session) -> None:
        _make_paper(db_session, "p1", title="Duplicate Title", authors=["Alice"])

        resp = client.post(
            "/api/papers/merge",
            json={
                "target_id": "p1",
                "source_ids": ["p1"],
                "field_sources": {},
            },
        )
        assert resp.status_code == 400
