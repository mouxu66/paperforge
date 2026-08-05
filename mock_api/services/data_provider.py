"""Data Provider Protocol — abstracts data-layer operations away from DAG review logic.

v4.2: Extracted from depth_eval_v4.py to eliminate direct database access
(SessionLocal / crud / models) in the DepthReviewer core.  The reviewer
depends on this protocol, not on concrete database imports.
"""

from __future__ import annotations

from typing import Any, Protocol


class DataProvider(Protocol):
    """Abstract interface for data-layer operations needed by DepthReviewer.

    Implementations (e.g. DBDataProvider) encapsulate all database access,
    keeping the review engine portable and testable with in-memory stubs.
    """

    def get_hotspot_keywords(self, paper_id: int = 0) -> list[str]:
        """Return hotspot keywords for the given paper_id.

        paper_id may be ignored by providers that load a global hotspot list.
        Returns an empty list on failure; caller should fall back to
        DEFAULT_HOTSPOTS when the list is empty.
        """
        ...

    def load_figure_evidence(self, paper_id: str) -> list[dict[str, Any]]:
        """Return figure-evidence items from PaperFigure for paper_id.

        Each item is a dict with keys: page, figure_index, summary, ocr_text,
        caption_text, axis_info, claim_validation, curve_points.

        Returns an empty list on failure or when figure evidence is unavailable.
        """
        ...

    def get_cached_score(self, paper_id: str) -> dict[str, Any] | None:
        """Return cached v3 score dict for paper_id, or None if not cached."""
        ...

    def save_score(self, paper_id: str, result: dict[str, Any]) -> None:
        """Persist the v3 score result for paper_id (upsert semantics).

        Covers both insert (new paper) and update (existing record).
        """
        ...
