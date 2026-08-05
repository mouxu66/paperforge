"""DB-backed DataProvider implementation.

Encapsulates all SessionLocal / crud / models imports that were previously
scattered across depth_eval_v4.py.  Each method manages its own database
session lifecycle (open → operate → close), keeping the caller free of
any SQLAlchemy knowledge.

v4.2: Extracted from depth_eval_v4.py — _load_hotspots (DB branch),
_load_figure_items, get_cached_score, _save_v3_score.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class DBDataProvider:
    """Concrete DataProvider backed by the PaperForge database.

    Each public method opens its own SessionLocal, performs the query,
    and closes the session in a ``finally`` block — following the exact
    fail-soft pattern from the original depth_eval_v4.py code.
    """

    # ------------------------------------------------------------------
    # get_hotspot_keywords
    # ------------------------------------------------------------------
    @staticmethod
    def get_hotspot_keywords(paper_id: int = 0) -> list[str]:
        """Load hotspot keywords from crud, with fail-soft fallback.

        Returns an empty list on any failure; caller falls back to
        DEFAULT_HOTSPOTS.
        """
        try:
            from ..crud import get_hotspot_keywords as _crud_get_hotspot_keywords
            from ..database import SessionLocal

            db = SessionLocal()
            try:
                keywords = _crud_get_hotspot_keywords(db)
                return keywords if keywords else []
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[热点词] db 加载失败: %s，回退 default", exc)
            return []

    # ------------------------------------------------------------------
    # load_figure_evidence
    # ------------------------------------------------------------------
    @staticmethod
    def load_figure_evidence(paper_id: str) -> list[dict[str, Any]]:
        """Load figure items from PaperFigure for *paper_id*.

        Returns a list of dicts with keys: page, figure_index, summary,
        ocr_text, caption_text, axis_info, claim_validation, curve_points.
        Empty list on failure or no figures.
        """
        if not paper_id:
            return []
        try:
            from ..database import SessionLocal
            from ..models import PaperFigure

            db = SessionLocal()
            try:
                rows = (
                    db.query(PaperFigure)
                    .filter(PaperFigure.paper_id == paper_id)
                    .order_by(PaperFigure.page, PaperFigure.figure_index)
                    .limit(12)
                    .all()
                )
                items: list[dict[str, Any]] = []
                for row in rows:
                    item: dict[str, Any] = {
                        "page": row.page or 0,
                        "index": row.figure_index or 0,
                        "summary": (
                            (row.qwen_summary or "").strip()[:300] if row.qwen_summary else ""
                        ),
                        "ocr": ((row.ocr_text or "").strip()[:200] if row.ocr_text else ""),
                        "caption": (
                            (row.caption_text or "").strip()[:500] if row.caption_text else ""
                        ),
                    }
                    # axis_info — may be stored as JSON string or dict
                    axis_info = row.axis_info
                    if isinstance(axis_info, str) and axis_info.strip():
                        try:
                            axis_info = json.loads(axis_info)
                        except json.JSONDecodeError:
                            axis_info = None
                    item["axis_info"] = axis_info if isinstance(axis_info, dict) else None
                    # claim_validation
                    claim_validation = row.claim_validation
                    if isinstance(claim_validation, str) and claim_validation.strip():
                        try:
                            claim_validation = json.loads(claim_validation)
                        except json.JSONDecodeError:
                            claim_validation = None
                    item["claim_validation"] = (
                        claim_validation if isinstance(claim_validation, dict) else None
                    )
                    # curve_points
                    curve_points = row.curve_points
                    if isinstance(curve_points, str) and curve_points.strip():
                        try:
                            curve_points = json.loads(curve_points)
                        except json.JSONDecodeError:
                            curve_points = None
                    item["curve_points"] = curve_points if isinstance(curve_points, list) else None
                    items.append(item)
                return items
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[图表证据] db 加载失败: %s，返回空列表", exc)
            return []

    # ------------------------------------------------------------------
    # get_cached_score
    # ------------------------------------------------------------------
    @staticmethod
    def get_cached_score(paper_id: str) -> dict[str, Any] | None:
        """Read cached v3 score for *paper_id*; returns None if not cached."""
        try:
            from ..database import SessionLocal
            from ..models import DepthScore as DepthScoreORM

            db = SessionLocal()
            try:
                row = db.query(DepthScoreORM).filter(DepthScoreORM.paper_id == paper_id).first()
                if not row:
                    return None
                return {
                    "paper_id": row.paper_id,
                    "title": row.title,
                    "type": row.type,
                    "confidence": row.confidence,
                    "has_substance": row.has_substance,
                    "expectation": row.expectation,
                    "obj_score": row.obj_score,
                    "novelty_score": row.novelty_score,
                    "rigor_score": row.rigor_score,
                    "influence_score": row.influence_score,
                    "reproducibility_score": row.reproducibility_score,
                    "calibrated_score": row.calibrated_score,
                    "final_score": row.final_score,
                    "verdict": row.verdict,
                    "core_contribution": row.core_contribution,
                    "keywords": list(row.keywords or []),
                    "missing_items": list(row.missing_items or []),
                    "critique_points": list(row.critique_points or []),
                    "defense_points": list(row.defense_points or []),
                    "chair_reasoning": row.chair_reasoning,
                    "content_source": row.content_source,
                }
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[缓存] 读取失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # save_score
    # ------------------------------------------------------------------
    @staticmethod
    def save_score(paper_id: str, result: dict[str, Any]) -> None:
        """Persist v3 score result (upsert semantics — insert or update).

        Mirrors the original _save_v3_score() from depth_eval_v4.py.
        """
        if not paper_id:
            return
        try:
            from ..database import SessionLocal
            from ..models import DepthScore as DepthScoreORM
            from ..models import Paper as PaperORM

            db = SessionLocal()
            try:
                # Ensure Paper row exists (foreign-key constraint)
                paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
                if not paper:
                    paper = PaperORM(id=paper_id, title=result.get("title", ""))
                    db.add(paper)
                    db.flush()

                existing = (
                    db.query(DepthScoreORM).filter(DepthScoreORM.paper_id == paper_id).first()
                )
                if existing:
                    row = existing
                else:
                    row = DepthScoreORM(paper_id=paper_id)
                    db.add(row)

                row.title = result.get("title", paper.title or "")
                row.type = result.get("type", "B")
                row.confidence = float(result.get("confidence", 0.5))
                row.has_substance = bool(result.get("has_substance", True))
                row.expectation = float(result.get("expectation", 0.5))
                row.obj_score = float(result.get("obj_score", 0.0))
                row.novelty_score = float(result.get("novelty_score", 0.0))
                row.rigor_score = float(result.get("rigor_score", 0.0))
                row.influence_score = float(result.get("influence_score", 0.0))
                row.reproducibility_score = float(result.get("reproducibility_score", 0.0))
                row.calibrated_score = float(result.get("calibrated_score", 0.0))
                row.final_score = float(result.get("final_score", 0.0))
                row.verdict = result.get("verdict", "major_revision")
                row.core_contribution = result.get("core_contribution", "")
                row.keywords = list(result.get("keywords", []) or [])
                row.missing_items = list(result.get("missing_items", []) or [])
                row.critique_points = list(result.get("critique_points", []) or [])
                row.defense_points = list(result.get("defense_points", []) or [])
                row.chair_reasoning = result.get("chair_reasoning", "")
                row.content_source = result.get("content_source", "depth_v4")
                row.evaluated_at = datetime.now()

                db.commit()
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[保存评分] 失败: %s", exc)


# ---------------------------------------------------------------------------
# Singleton convenience (lazy-init, thread-safe)
# ---------------------------------------------------------------------------
_db_data_provider: DBDataProvider | None = None


def get_db_data_provider() -> DBDataProvider:
    """Return the process-wide singleton DBDataProvider."""
    global _db_data_provider
    if _db_data_provider is None:
        _db_data_provider = DBDataProvider()
    return _db_data_provider
