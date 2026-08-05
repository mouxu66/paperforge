"""论文实验图（figure）抽取结果的存储与检索。

MVP：每张图独立一行（figure 级粒度），OCR 文字 + 向量（bge-small-en-v1.5, 384 维）
存库，使「搜 accuracy curve」能定位到具体论文的具体那张图。

设计取舍：
- 与论文级 PaperEmbedding 解耦，避免污染现有混合检索 RRF 逻辑；figure 检索走
  独立端点 / 函数，便于后续与论文结果做 RRF 融合。
- OCR 文字为空时仍存图（figure_path 可用），但 embedding 为空 → 检索时跳过，
  仅作为「有图但无文字」的元数据。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..semantic_search import _cosine

logger = logging.getLogger(__name__)


def upsert_figure(
    db: Session,
    *,
    paper_id: str,
    page: int,
    figure_index: int,
    figure_path: str,
    ocr_text: str,
    embedding: list[float] | None = None,
    qwen_summary: str | None = None,
    caption_text: str | None = None,
    source: str = "bitmap",
    figure_number: int | None = None,
    match_confidence: float | None = None,
    match_label: str | None = None,
    vlm_decision: str | None = None,
    source_text_span: str | None = None,
    axis_info: dict[str, Any] | None = None,
    claim_validation: dict[str, Any] | None = None,
    curve_points: list[dict[str, Any]] | None = None,
) -> PaperFigure:
    """插入一条 figure 记录。MVP 为纯追加（重处理时由调用方先 delete_figures_by_paper）。"""
    if caption_text is not None and not caption_text.strip():
        caption_text = None
    emb_json = json.dumps(embedding) if embedding else None
    fig = PaperFigure(
        paper_id=paper_id,
        page=page,
        figure_index=figure_index,
        figure_path=figure_path,
        ocr_text=ocr_text,
        embedding=emb_json,
        qwen_summary=qwen_summary,
        caption_text=caption_text,
        figure_number=figure_number,
        source=source,
        match_confidence=match_confidence,
        match_label=match_label,
        vlm_decision=vlm_decision,
        source_text_span=source_text_span,
        axis_info=axis_info,
        claim_validation=claim_validation,
        curve_points=curve_points,
    )
    db.add(fig)
    db.flush()
    return fig


def delete_figures_by_paper(db: Session, paper_id: str) -> int:
    """删除某论文的全部 figure（重处理时清理）。"""
    n = db.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).delete()
    db.flush()
    return n


def get_figures_by_paper(db: Session, paper_id: str) -> list[PaperFigure]:
    return (
        db.execute(
            select(PaperFigure)
            .where(PaperFigure.paper_id == paper_id)
            .order_by(PaperFigure.page, PaperFigure.figure_index)
        )
        .scalars()
        .all()
    )


def search_figures(db: Session, query_vec: list[float], top_k: int = 10) -> list[dict[str, Any]]:
    """按向量余弦相似度检索 figure（与论文级检索同维度，可 RRF 融合）。

    Returns: [{paper_id, page, figure_index, figure_path, ocr_text, score}, ...]
    仅返回有 embedding 的 figure；score 降序。
    """
    rows = db.execute(select(PaperFigure)).scalars().all()
    scored: list[dict[str, Any]] = []
    for r in rows:
        if not r.embedding:
            continue
        try:
            vec = json.loads(r.embedding)
        except Exception:  # noqa: BLE001 - figure crud - 退化到空记录而非失败
            continue
        score = _cosine(query_vec, vec)
        scored.append(
            {
                "paper_id": r.paper_id,
                "page": r.page,
                "figure_index": r.figure_index,
                "figure_path": r.figure_path,
                "ocr_text": r.ocr_text,
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
