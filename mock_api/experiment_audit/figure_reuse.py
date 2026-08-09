"""P0-9 曲线/图片复用候选检测（论文内部）。

两级管线（指南 Step 7）：
1. pHash 快速召回：论文内所有 figure 两两汉明距离 < 阈值 → 候选对。
2. SIFT + BFMatcher(ratio) + RANSAC findHomography 精确验证：
   inliers > 阈值 → FIGURE_REUSE_CANDIDATE（severity=low，人工终审）。

防御：des 为 None / 特征点不足 / 单面板图自身匹配均跳过。
"""

from __future__ import annotations

import logging
from itertools import combinations
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..pdf_parser import _get_uploads_dir
from .schemas import make_finding

logger = logging.getLogger(__name__)

DEFAULT_PHASH_RECALL_THRESHOLD = 15
DEFAULT_RATIO = 0.75
DEFAULT_MIN_GOOD_MATCHES = 10
DEFAULT_MIN_RANSAC_INLIERS = 5


def _figure_label(fig: PaperFigure) -> str:
    return (
        f"Figure {fig.figure_number}"
        if fig.figure_number
        else f"Figure(p{fig.page}#{fig.figure_index})"
    )


def _resolve_figure_path(fig: PaperFigure, paper_id: str) -> Path | None:
    """figure_path 可能是相对 uploads/figures/<paper_id>/ 的文件名或完整相对路径。"""
    if not fig.figure_path:
        return None
    base = _get_uploads_dir() / "figures" / paper_id
    candidates = [base / Path(fig.figure_path).name, Path(fig.figure_path)]
    for c in candidates:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return None


def detect_figure_reuse(
    db: Session,
    paper_id: str,
    *,
    phash_threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD,
    min_ransac_inliers: int = DEFAULT_MIN_RANSAC_INLIERS,
) -> list[dict]:
    """论文内图片复用检测。依赖缺失（cv2/imagehash）时 fail-open 返回空。"""
    try:
        import cv2
        import imagehash
        import numpy as np
        from PIL import Image
    except ImportError as e:
        logger.warning("P0-9 依赖缺失，曲线复用检测跳过: %s", e)
        return []

    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page, PaperFigure.figure_index)
        .all()
    )
    resolved: list[tuple[PaperFigure, Path]] = []
    for f in figs:
        p = _resolve_figure_path(f, paper_id)
        if p is not None:
            resolved.append((f, p))
    if len(resolved) < 2:
        return []

    # ── 1. pHash 召回 ──
    hashed: list[tuple[PaperFigure, Path, object]] = []
    for f, p in resolved:
        try:
            with Image.open(p) as im:
                hashed.append((f, p, imagehash.phash(im)))
        except Exception:  # noqa: BLE001 - 坏图跳过
            continue
    pairs: list[tuple[PaperFigure, Path, PaperFigure, Path, int]] = []
    for (f1, p1, h1), (f2, p2, h2) in combinations(hashed, 2):
        dist = h1 - h2
        if dist < phash_threshold:
            pairs.append((f1, p1, f2, p2, dist))
    if not pairs:
        return []

    # ── 2. SIFT + RANSAC 验证 ──
    sift = cv2.SIFT_create()
    bf = cv2.BFMatcher()
    findings: list[dict] = []
    for f1, p1, f2, p2, dist in pairs:
        img1 = cv2.imread(str(p1), cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imread(str(p2), cv2.IMREAD_GRAYSCALE)
        if img1 is None or img2 is None:
            continue
        kp1, des1 = sift.detectAndCompute(img1, None)
        kp2, des2 = sift.detectAndCompute(img2, None)
        if des1 is None or des2 is None or len(kp1) < 5 or len(kp2) < 5:
            continue
        try:
            matches = bf.knnMatch(des1, des2, k=2)
        except cv2.error:
            continue
        good = [
            pair[0]
            for pair in matches
            if len(pair) == 2 and pair[0].distance < DEFAULT_RATIO * pair[1].distance
        ]
        if len(good) < DEFAULT_MIN_GOOD_MATCHES:
            continue
        src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        inliers = int(mask.sum()) if mask is not None else 0
        if M is None or inliers < min_ransac_inliers:
            continue
        findings.append(
            make_finding(
                "FIGURE_REUSE_CANDIDATE",
                title=f"{_figure_label(f1)} 与 {_figure_label(f2)} 局部高度匹配",
                page=f1.page,
                claim=f"pHash 距离 {dist}，SIFT 好匹配 {len(good)} 个",
                computed=f"RANSAC inliers = {inliers}（阈值 {min_ransac_inliers}）",
                method="pHash 召回 + SIFT(BFMatcher ratio 0.75) + RANSAC 验证",
                evidence_sources=[
                    {"type": "figure", "figure_id": _figure_label(f1), "page": f1.page},
                    {"type": "figure", "figure_id": _figure_label(f2), "page": f2.page},
                ],
                normal_explanation=(
                    "同一实验的不同视图/子图共享曲线属正常；复用是否不当需人工对照两图语义"
                ),
                needs_human_review=True,
            )
        )
    return findings
