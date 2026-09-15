"""P0-9 曲线/图片复用候选检测（论文内 + 跨论文）。

论文内复用两级管线（指南 Step 7）：
1. pHash 快速召回：论文内所有 figure 两两汉明距离 < 阈值 → 候选对。
2. SIFT + BFMatcher(ratio) + RANSAC findHomography 精确验证：
   inliers ≥ 绝对下限 且 inlier/good 比例 ≥ 0.7（实测标定，防相似图假阳性）
   → FIGURE_REUSE_CANDIDATE（severity=low，人工终审）。

跨论文：
- detect_cross_paper_reuse：整图复用（pHash 召回 + 可选 SIFT 整图验证）。
- detect_cross_paper_subpanel_reuse：子面板（单 panel）复用——合成图中被
  复用的单个区域整图 pHash/比例都分不开，改看局部空间内 inlier 密度。
- detect_cross_paper_band_reuse：条带（western blot band）级复用——细条带
  SIFT 特征点不足，改用条带提取 + NCC 像素比对（如 AMPK 条带被改标为 GAPDH）。
- detect_relabeled_image_reuse：改标复用证据链——NCC 像素复用 + VLM 目标蛋白
  标签比对，两图目标蛋白无交集时出 RELABELED_IMAGE_REUSE（高严重度）。

防御：des 为 None / 特征点不足 / 单面板图自身匹配均跳过。
"""

from __future__ import annotations

import logging
import re
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..pdf_parser import _get_uploads_dir
from .schemas import make_finding

logger = logging.getLogger(__name__)

DEFAULT_PHASH_RECALL_THRESHOLD = 15
DEFAULT_RATIO = 0.75
DEFAULT_MIN_GOOD_MATCHES = 10
DEFAULT_MIN_RANSAC_INLIERS = 5
# SIFT/RANSAC 几何一致性比例下限（实测标定，2026-08-14）：
# 相似风格但非复用的图（Berberine vs KJPP 48 对）inliers ≤127、ratio ≤0.50；
# 真复用（同图 ± 轻微压缩/缩放）ratio ≥0.90。0.70 取两者间带余量的分界，
# 尺度无关地挡住「相似图表低量级匹配」的假阳性。
DEFAULT_MIN_INLIER_RATIO = 0.7
# ── 子面板（单 panel）跨论文复用检测 ──────────────────────────────
# 合成图中被复用的常是单个子面板（如 GAPDH 内参条带），整图 pHash 因布局
# 不同分不开、整图 RANSAC inlier 比例被其余不匹配区域稀释到 ~0.2。子面板
# 检测改看「局部空间内 inlier 密度」：真实像素级复制的子面板会在一个小窗口
# 内聚集大量几何一致 inlier；重画/相似风格图 inlier 稀疏散布，局部密度低。
# 实测（Berberine vs KJPP 45 对有匹配对，window_frac=0.15）局部密度封顶 67，
# 而合成像素级子面板复制（~150×150 面板）密度 100+，故默认下限取 100。
DEFAULT_SUBPANEL_WINDOW_FRAC = 0.15
DEFAULT_MIN_LOCAL_INLIERS = 100
# ── 条带（western blot band）级跨论文复用 ─────────────────────────
# SIFT 对细条带（~40×20px）特征点不足（实测 AMPK/GAPDH 条带局部 inlier 密度仅 67），
# 改用归一化互相关（NCC）直接比对条带像素。实测（Berberine vs KJPP，4998 对条带）：
# 无关条带 NCC 集中在 0.5–0.85（p99≈0.89），真实复用的 AMPK→GAPDH 条带 NCC=0.965。
DEFAULT_BAND_NCC_THRESHOLD = 0.92


def _geometrically_consistent(
    inliers: int,
    good_matches: int,
    *,
    min_inliers: int,
    min_ratio: float,
) -> bool:
    """SIFT/RANSAC 几何一致性判定：绝对 inlier 下限 + inlier/good 比例下限。

    比例下限是主要尺度无关判别器（相似图低量级匹配比例低，真复用接近 1.0）；
    绝对下限防退化（匹配数极少时比例虚高）。
    """
    if inliers < min_inliers or good_matches <= 0:
        return False
    return (inliers / good_matches) >= min_ratio


def _flip_aware_phashes(im) -> tuple[object, object, object]:
    """返回 (原图, 水平翻转, 垂直翻转) 三种 pHash。

    镜像翻转显著改变感知哈希（实测翻转对 pHash 距离 ~32，远超召回阈值 15），
    若只对原图哈希，翻转复用会在召回阶段就被漏掉。三种假设各自哈希后，
    任一对距离 < 阈值即召回（见 _min_hash_distance）。
    """
    import imagehash
    from PIL import Image as _PILImage

    return (
        imagehash.phash(im),
        imagehash.phash(im.transpose(_PILImage.FLIP_LEFT_RIGHT)),
        imagehash.phash(im.transpose(_PILImage.FLIP_TOP_BOTTOM)),
    )


def _min_hash_distance(
    hashes_a: tuple[object, object, object],
    hashes_b: tuple[object, object, object],
) -> int:
    """两图各自 (原图/H翻转/V翻转) 哈希集合间的最小汉明距离。"""
    return min(ha - hb for ha in hashes_a for hb in hashes_b)


def _flip_aware_sift_match(cv2: Any, img1: Any, img2: Any) -> list[dict]:
    """SIFT 匹配 + RANSAC 几何验证，含 img2 的 原图/H翻转/V翻转 三假设。

    返回各假设的 ``{good, inliers, ratio, src, dst}``，按 inlier/good 比例降序
    排列；``src``/``dst`` 为 RANSAC inlier 关键点坐标（(n,2) float）。普通
    findHomography 无法拟合镜像（反射，行列式为负），且 SIFT 描述子非镜像
    不变，必须在翻转后的图上重新提特征才能正确匹配——因此对每个翻转假设
    重跑 detectAndCompute。无有效假设时返回空列表。
    """
    sift = cv2.SIFT_create()
    bf = cv2.BFMatcher()
    kp1, des1 = sift.detectAndCompute(img1, None)
    if des1 is None or len(kp1) < 5:
        return []

    results: list[dict] = []
    for var in (img2, cv2.flip(img2, 1), cv2.flip(img2, 0)):
        kp2, des2 = sift.detectAndCompute(var, None)
        if des2 is None or len(kp2) < 5:
            continue
        try:
            matches = bf.knnMatch(des1, des2, k=2)
        except cv2.error as e:
            logger.debug("[audit] SIFT knnMatch 失败: %s", e)
            continue
        good = [
            m[0] for m in matches if len(m) == 2 and m[0].distance < DEFAULT_RATIO * m[1].distance
        ]
        if len(good) < DEFAULT_MIN_GOOD_MATCHES:
            continue
        src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        try:
            M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        except cv2.error as e:
            logger.debug("[audit] findHomography 失败: %s", e)
            continue
        inliers = int(mask.sum()) if mask is not None else 0
        mask_arr = mask.ravel().astype(bool) if mask is not None else np.zeros(len(good), bool)
        results.append(
            {
                "good": len(good),
                "inliers": inliers,
                "ratio": inliers / len(good),
                "src": src_pts[mask_arr].reshape(-1, 2),
                "dst": dst_pts[mask_arr].reshape(-1, 2),
            }
        )
    results.sort(key=lambda r: r["ratio"], reverse=True)
    return results


def _flip_aware_sift_verify(cv2: Any, img1: Any, img2: Any) -> tuple[int, int]:
    """整图复用验证入口：返回 inlier/good 比例最高假设的 (inliers, good_matches)。

    是 _flip_aware_sift_match 的薄封装，调用方用 _geometrically_consistent 施加
    inliers 下限 + 比例下限。
    """
    results = _flip_aware_sift_match(cv2, img1, img2)
    if not results:
        return 0, 0
    best = results[0]
    return best["inliers"], best["good"]


def _max_local_inlier_density(
    src_inliers: np.ndarray,
    img_shape: tuple[int, int],
    window_frac: float = DEFAULT_SUBPANEL_WINDOW_FRAC,
) -> int:
    """滑窗式局部 inlier 密度：以任一 inlier 为中心的圆窗（直径=window_frac×对角线）
    内能容纳的最大 inlier 数。

    这是子面板复用的核心判别量：像素级复制的子面板，其 RANSAC inlier 聚集在一个
    小区域（高密度）；重画/相似风格图的 inlier 稀疏散布在整张图面（低密度）。
    O(n²) 但 n 通常 < 几千，分块计算避免超大方阵。
    """
    n = int(src_inliers.shape[0])
    if n == 0:
        return 0
    h, w = int(img_shape[0]), int(img_shape[1])
    radius = window_frac * float(np.hypot(h, w))
    pts = src_inliers.astype(np.float64)
    best = 0
    chunk = 512
    for i in range(0, n, chunk):
        d = np.linalg.norm(pts[i : i + chunk, None, :] - pts[None, :, :], axis=-1)
        counts = (d <= radius).sum(axis=1)
        if counts.size:
            best = max(best, int(counts.max()))
    return best


def _subpanel_local_cluster(
    cv2: Any,
    img1: Any,
    img2: Any,
    *,
    window_frac: float = DEFAULT_SUBPANEL_WINDOW_FRAC,
) -> tuple[int, int, int, float]:
    """子面板匹配：返回所有翻转假设中局部 inlier 密度最大的
    (local_inliers, good_matches, ransac_inliers, inlier_ratio)。

    与整图验证不同，这里按「局部密度」而非「全局比例」选假设——子面板复用的
    最优翻转假设可能全局比例低、局部密度高。
    """
    results = _flip_aware_sift_match(cv2, img1, img2)
    best: tuple[int, int, int, float] = (0, 0, 0, 0.0)
    for r in results:
        local = _max_local_inlier_density(r["src"], img1.shape, window_frac)
        if local > best[0]:
            best = (local, r["good"], r["inliers"], r["ratio"])
    return best


def reuse_deps_available() -> bool:
    """cv2/imagehash/Pillow/numpy 是否可用（P0-9 图片复用检测的硬依赖）。"""
    try:
        import cv2  # noqa: F401
        import imagehash  # noqa: F401
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401

        return True
    except ImportError:
        return False


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
        except OSError as e:
            logger.debug("[audit] 图片路径检查失败: %s - %s", c, e)
            continue
    return None


def resolve_paper_figure_paths(db: Session, paper_id: str) -> list[Path]:
    """解析论文已注册 figure 的磁盘路径（按 page/figure_index 排序、去重）。

    供跨论文检测入口（API/脚本）把「论文 → 图路径列表」喂给
    detect_cross_paper_* / detect_relabeled_image_reuse。路径无法解析的
    记录静默跳过（与 detect_figure_reuse 的容错行为一致）。
    """
    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page, PaperFigure.figure_index)
        .all()
    )
    seen: set[str] = set()
    out: list[Path] = []
    for f in figs:
        p = _resolve_figure_path(f, paper_id)
        if p is not None:
            key = str(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def detect_figure_reuse(
    db: Session,
    paper_id: str,
    *,
    phash_threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD,
    min_ransac_inliers: int = DEFAULT_MIN_RANSAC_INLIERS,
    min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
) -> list[dict]:
    """论文内图片复用检测。依赖缺失（cv2/PIL）时 fail-open 返回空。"""
    try:
        import cv2
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

    # ── 1. pHash 召回（原图 + H/V 翻转三假设）──
    hashed: list[tuple[PaperFigure, Path, tuple[object, object, object]]] = []
    for f, p in resolved:
        try:
            with Image.open(p) as im:
                hashed.append((f, p, _flip_aware_phashes(im)))
        except Exception as e:  # noqa: BLE001 - 坏图跳过
            logger.debug("[audit] pHash 计算失败 (坏图跳过): %s - %s", p, e)
            continue
    pairs: list[tuple[PaperFigure, Path, PaperFigure, Path, int]] = []
    for (f1, p1, h1), (f2, p2, h2) in combinations(hashed, 2):
        dist = _min_hash_distance(h1, h2)
        if dist < phash_threshold:
            pairs.append((f1, p1, f2, p2, dist))
    if not pairs:
        return []

    # ── 2. SIFT + RANSAC 验证（含镜像/翻转假设）──
    findings: list[dict] = []
    for f1, p1, f2, p2, dist in pairs:
        img1 = cv2.imread(str(p1), cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imread(str(p2), cv2.IMREAD_GRAYSCALE)
        if img1 is None or img2 is None:
            continue
        inliers, good_count = _flip_aware_sift_verify(cv2, img1, img2)
        if not _geometrically_consistent(
            inliers,
            good_count,
            min_inliers=min_ransac_inliers,
            min_ratio=min_inlier_ratio,
        ):
            continue
        findings.append(
            make_finding(
                "FIGURE_REUSE_CANDIDATE",
                title=f"{_figure_label(f1)} 与 {_figure_label(f2)} 局部高度匹配",
                page=f1.page,
                claim=f"pHash 距离 {dist}，SIFT 好匹配 {good_count} 个",
                computed=(
                    f"RANSAC inliers = {inliers}（阈值 {min_ransac_inliers}），"
                    f"inlier 比例 {inliers / good_count:.2f}（阈值 {min_inlier_ratio}）"
                ),
                method="pHash 召回 + SIFT(BFMatcher ratio 0.75) + RANSAC 验证（含镜像/翻转假设）",
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


def detect_cross_paper_reuse(
    figures_by_paper: dict[str, list[str | Path]],
    phash_threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD,
    verify_sift: bool = False,
    min_ransac_inliers: int = DEFAULT_MIN_RANSAC_INLIERS,
    min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
) -> list[dict]:
    """跨论文图片复用召回（pHash 粗筛 + 可选 SIFT 验证）。

    输入：paper_id → figure 路径列表（如 uploads/figures/<paper_id>/ 下的 PNG）。
    输出：不同 paper 之间的 pHash 近重复候选对
    ``{paper_a, fig_a, paper_b, fig_b, dist, sift_verified, ransac_inliers}``。

    verify_sift=True 时对 pHash 候选对做 SIFT+RANSAC 验证（与论文内复用对齐）。
    依赖缺失（PIL/cv2）fail-open 返回空。
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("P0-9 依赖缺失，跨论文复用召回跳过")
        return []

    # SIFT 验证需要 cv2
    cv2 = None
    if verify_sift:
        try:
            import cv2 as _cv2

            cv2 = _cv2
        except ImportError:
            logger.warning("cv2 未安装，SIFT 验证跳过，仅返回 pHash 候选")

    entries: list[tuple[str, str, tuple[object, object, object]]] = []
    for paper_id, paths in figures_by_paper.items():
        for p in paths:
            try:
                with Image.open(p) as im:
                    entries.append((str(paper_id), str(p), _flip_aware_phashes(im)))
            except Exception as e:  # noqa: BLE001 - 坏图跳过
                logger.debug("[audit] 跨论文 pHash 计算失败 (坏图跳过): %s - %s", p, e)
                continue

    candidates: list[dict] = []
    for i in range(len(entries)):
        pa, fp_a, ha = entries[i]
        for j in range(i + 1, len(entries)):
            pb, fp_b, hb = entries[j]
            if pa == pb:
                continue
            dist = _min_hash_distance(ha, hb)
            if dist < phash_threshold:
                candidates.append(
                    {
                        "paper_a": pa,
                        "fig_a": fp_a,
                        "paper_b": pb,
                        "fig_b": fp_b,
                        "dist": dist,
                        "sift_verified": False,
                        "ransac_inliers": 0,
                    }
                )

    # SIFT 验证（可选）
    if verify_sift and cv2 is not None and candidates:
        candidates = _verify_cross_paper_with_sift(
            cv2, candidates, min_ransac_inliers, min_inlier_ratio
        )

    return candidates


def _verify_cross_paper_with_sift(
    cv2: Any,
    candidates: list[dict],
    min_ransac_inliers: int,
    min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
) -> list[dict]:
    """对跨论文 pHash 候选对做 SIFT+RANSAC 验证（含镜像/翻转假设）。"""
    verified: list[dict] = []
    for cand in candidates:
        p1, p2 = cand["fig_a"], cand["fig_b"]
        img1 = cv2.imread(str(p1), cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imread(str(p2), cv2.IMREAD_GRAYSCALE)
        if img1 is None or img2 is None:
            continue
        inliers, good_count = _flip_aware_sift_verify(cv2, img1, img2)
        if _geometrically_consistent(
            inliers,
            good_count,
            min_inliers=min_ransac_inliers,
            min_ratio=min_inlier_ratio,
        ):
            cand["sift_verified"] = True
            cand["ransac_inliers"] = inliers
            cand["inlier_ratio"] = inliers / good_count
            verified.append(cand)
    return verified


def detect_cross_paper_subpanel_reuse(
    figures_by_paper: dict[str, list[str | Path]],
    *,
    min_local_inliers: int = DEFAULT_MIN_LOCAL_INLIERS,
    window_frac: float = DEFAULT_SUBPANEL_WINDOW_FRAC,
) -> list[dict]:
    """跨论文子面板（单 panel）复用检测。

    针对「合成图里被复用的单个子面板」——如论文工厂把同一张 GAPDH 内参条带
    塞进不同布局的合成图。这类复用整图 pHash 分不开、整图 RANSAC inlier 比例
    被其余不匹配区域稀释，detect_cross_paper_reuse 抓不到。本检测对所有跨论文
    图对做翻转感知 SIFT + RANSAC，找局部空间内高密度 inlier 簇，密度 ≥
    min_local_inliers 即出候选。

    输出每对: ``{paper_a, fig_a, paper_b, fig_b, local_inliers, good_matches,
    ransac_inliers, inlier_ratio, subpanel_verified}``。

    注意：直接对全部跨论文图对跑 SIFT 是 O(N²)（N=图数），适合「几篇候选论文」
    的核对场景；全库规模（数百篇）应先走 pHash/词袋倒排索引粗筛（后续路线）。
    cv2 缺失时 fail-open 返回空。
    """
    try:
        import cv2
    except ImportError:
        logger.warning("cv2 未安装，跨论文子面板复用检测跳过")
        return []

    entries: list[tuple[str, str]] = []
    for paper_id, paths in figures_by_paper.items():
        for p in paths:
            entries.append((str(paper_id), str(p)))

    candidates: list[dict] = []
    for i in range(len(entries)):
        pa, fp_a = entries[i]
        for j in range(i + 1, len(entries)):
            pb, fp_b = entries[j]
            if pa == pb:
                continue
            img1 = cv2.imread(fp_a, cv2.IMREAD_GRAYSCALE)
            img2 = cv2.imread(fp_b, cv2.IMREAD_GRAYSCALE)
            if img1 is None or img2 is None:
                continue
            local, good, inliers, ratio = _subpanel_local_cluster(
                cv2, img1, img2, window_frac=window_frac
            )
            if local < min_local_inliers:
                continue
            candidates.append(
                {
                    "paper_a": pa,
                    "fig_a": fp_a,
                    "paper_b": pb,
                    "fig_b": fp_b,
                    "local_inliers": local,
                    "good_matches": good,
                    "ransac_inliers": inliers,
                    "inlier_ratio": round(ratio, 4),
                    "subpanel_verified": True,
                }
            )
    return candidates


def _detect_band_regions(cv2: Any, img: Any) -> list[tuple[int, int, int, int]]:
    """检测 western-blot 式横向条带区域，返回去重后的 (x, y, w, h) 列表。

    覆盖两种方向：暗带白底（img < 110）与反相白带黑底（img > 145）。连通域需
    满足「宽 > 高 + 高度 6–100 + 面积 200–8000」，过滤掉文字/网格线/大块背景；
    反相方向下纯白/纯黑背景会被面积上限自然排除，不会误当条带。
    """
    masks = ((img < 110), (img > 145))
    seen: set[tuple[int, int, int, int]] = set()
    regions: list[tuple[int, int, int, int]] = []
    for m in masks:
        n, _, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if w > h and 6 <= h <= 100 and 200 <= area <= 8000:
                key = (int(x), int(y), int(w), int(h))
                if key not in seen:
                    seen.add(key)
                    regions.append(key)
    return regions


def _resized_ncc(
    cv2: Any,
    a: Any,
    b: Any,
    size: tuple[int, int] = (32, 64),
) -> float:
    """两图缩放到同尺寸后的归一化互相关（零均值 NCC）。

    用固定尺寸（而非保持宽高比）以容忍两期刊对同一张条带的不同裁剪/缩放——
    实测 AMPK→GAPDH 条带两处裁剪宽高比不同（95×23 vs 46×20），固定尺寸 NCC=0.965，
    而保持宽高比会因裁剪差异把真复用压到 0.89 以下。
    """
    ra = cv2.resize(a, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    rb = cv2.resize(b, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = float(np.linalg.norm(ra) * np.linalg.norm(rb))
    if denom < 1e-9:
        return 0.0
    return float((ra * rb).sum() / denom)


# ── 主链路：单篇审计的跨论文复用（T1）────────────────────
# 进程内缓存：库内图路径集合不变时，重复审计不重建 pHash 索引。
_CORPUS_INDEX_CACHE: tuple[tuple[str, ...], Any] | None = None


def _corpus_figures_by_paper(db: Session, exclude_paper_id: str) -> dict[str, list[str]]:
    """库内其他论文的已注册图 → 可解析路径。"""
    rows = (
        db.query(PaperFigure.paper_id)
        .filter(PaperFigure.paper_id != exclude_paper_id)
        .distinct()
        .all()
    )
    out: dict[str, list[str]] = {}
    for (pid,) in rows:
        paths = resolve_paper_figure_paths(db, pid)
        if paths:
            out[pid] = [str(p) for p in paths]
    return out


def _cached_corpus_index(figures_by_paper: dict[str, list[str]]):
    """按全库图路径指纹缓存 pHash 索引（库不变时不重建，审计批跑不重复开销）。"""
    global _CORPUS_INDEX_CACHE
    from .figure_index import build_index

    fingerprint = tuple(sorted(p for paths in figures_by_paper.values() for p in paths))
    if _CORPUS_INDEX_CACHE is not None and _CORPUS_INDEX_CACHE[0] == fingerprint:
        return _CORPUS_INDEX_CACHE[1]
    index = build_index(figures_by_paper)
    _CORPUS_INDEX_CACHE = (fingerprint, index)
    return index


def detect_cross_paper_reuse_in_corpus(
    db: Session,
    paper_id: str,
    *,
    phash_threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD,
    min_ransac_inliers: int = DEFAULT_MIN_RANSAC_INLIERS,
    min_inlier_ratio: float = DEFAULT_MIN_INLIER_RATIO,
    top_k_per_figure: int = 3,
) -> list[dict]:
    """主链路：审计某篇论文时，将其图与库内其他论文的图做跨论文复用候选检测。

    两级管线：全库 pHash 索引召回（秒级、进程内缓存）→ 候选对 SIFT/RANSAC
    几何验证（含镜像/翻转假设）。产出 FIGURE_REUSE_CANDIDATE（与 P0-9 同型，
    claim/computed 标注对方论文与图）。硬依赖缺失 / 无图 / 无候选均 fail-open
    返回空，不阻塞审计。
    """
    try:
        import cv2
        import imagehash  # noqa: F401 - 依赖探测
    except ImportError:
        logger.warning("cv2/imagehash 未安装，跨论文复用检测跳过")
        return []

    from .figure_index import IndexEntry, _entry_hashes, _min_dist

    figs = resolve_paper_figure_paths(db, paper_id)
    if not figs:
        return []
    corpus = _corpus_figures_by_paper(db, paper_id)
    if not corpus:
        return []
    index = _cached_corpus_index(corpus)
    index.threshold = phash_threshold

    findings: list[dict] = []
    for fig in figs:
        hashes = _entry_hashes(fig)
        if hashes is None:
            continue
        hits = index.recall(hashes)
        if not hits:
            continue
        img1 = cv2.imread(str(fig), cv2.IMREAD_GRAYSCALE)
        if img1 is None:
            continue
        verified: list[tuple[float, IndexEntry]] = []
        for hit in hits:
            img2 = cv2.imread(hit.path, cv2.IMREAD_GRAYSCALE)
            if img2 is None:
                continue
            inliers, good = _flip_aware_sift_verify(cv2, img1, img2)
            if not _geometrically_consistent(
                inliers,
                good,
                min_inliers=min_ransac_inliers,
                min_ratio=min_inlier_ratio,
            ):
                continue
            ratio = inliers / good if good else 0.0
            verified.append((ratio, hit))
        verified.sort(key=lambda x: x[0], reverse=True)
        for ratio, hit in verified[:top_k_per_figure]:
            dist = _min_dist(hashes, hit.hashes)
            findings.append(
                make_finding(
                    "FIGURE_REUSE_CANDIDATE",
                    title=(
                        f"{Path(fig).name} 与库内论文「{hit.paper_id}」的 "
                        f"{Path(hit.path).name} 高度匹配"
                    ),
                    claim=(
                        f"跨论文复用候选：pHash 距离 {dist}，SIFT/RANSAC inlier 比例 "
                        f"{ratio:.2f}（阈值 ≥{min_inlier_ratio}，绝对下限 {min_ransac_inliers}）"
                    ),
                    computed=(
                        f"other_paper_id={hit.paper_id}\nthis_figure={fig}\nother_figure={hit.path}"
                    ),
                    method=(
                        "全库 pHash 索引召回 + SIFT(BFMatcher ratio 0.75) + "
                        "RANSAC 验证（含镜像/翻转假设）"
                    ),
                    evidence_sources=[
                        {"type": "figure", "figure_id": Path(fig).name},
                        {
                            "type": "figure",
                            "figure_id": Path(hit.path).name,
                            "snippet": f"other_paper_id={hit.paper_id}",
                        },
                    ],
                    normal_explanation=(
                        "相似风格图表/共享模板可能产生几何一致的局部匹配；是否跨论文"
                        "复用需人工对照两篇全文与原始数据确认"
                    ),
                    needs_human_review=True,
                )
            )
    return findings


def detect_cross_paper_band_reuse(
    figures_by_paper: dict[str, list[str | Path]],
    *,
    ncc_threshold: float = DEFAULT_BAND_NCC_THRESHOLD,
) -> list[dict]:
    """跨论文 western-blot 条带（band）级复用检测。

    针对「同一张条带图被论文工厂塞进不同布局的合成图」——SIFT 子面板检测
    （detect_cross_paper_subpanel_reuse）对细条带特征点不足，本检测改走
    条带提取 + NCC 像素比对。输出每对（按 NCC 降序）:
    ``{paper_a, fig_a, band_a, paper_b, fig_b, band_b, ncc}``。

    阈值默认 0.92（实测无关条带 p99≈0.89、真实复用 0.965）。O(N²)（N=条带数），
    适合「几篇候选论文」核对；cv2 缺失时 fail-open 返回空。
    """
    try:
        import cv2
    except ImportError:
        logger.warning("cv2 未安装，跨论文条带复用检测跳过")
        return []

    entries: list[tuple[str, str, tuple[int, int, int, int], Any]] = []
    for paper_id, paths in figures_by_paper.items():
        for p in paths:
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            for bbox in _detect_band_regions(cv2, img):
                x, y, w, h = bbox
                entries.append((str(paper_id), str(p), bbox, img[y : y + h, x : x + w]))

    candidates: list[dict] = []
    for i in range(len(entries)):
        pa, fp_a, bbox_a, crop_a = entries[i]
        for j in range(i + 1, len(entries)):
            pb, fp_b, bbox_b, crop_b = entries[j]
            if pa == pb:
                continue
            ncc = _resized_ncc(cv2, crop_a, crop_b)
            if ncc < ncc_threshold:
                continue
            candidates.append(
                {
                    "paper_a": pa,
                    "fig_a": fp_a,
                    "band_a": bbox_a,
                    "paper_b": pb,
                    "fig_b": fp_b,
                    "band_b": bbox_b,
                    "ncc": round(ncc, 4),
                }
            )
    candidates.sort(key=lambda c: c["ncc"], reverse=True)
    return candidates


_RELABEL_PROTEIN_PROMPT = (
    "这张实验图的检测目标蛋白/分子是什么？只回答蛋白名或分子名，多个用英文逗号"
    "分隔（例如：AMPK,p-AMPK,GAPDH）。如果不是蛋白/分子检测图（如纯柱状图/示意"
    "图），回答「无」。"
)


def _ask_vision(image_path: str, prompt: str, timeout: int = 120) -> str:
    """调视觉模型（Qwen3-VL）回答问题；不可用/失败返回空串（fail-open）。"""
    path = Path(image_path)
    if not path.exists():
        return ""
    try:
        import base64

        import requests

        from ..settings import get_settings
        from ..vram_scheduler import vram_guard

        st = get_settings()
        vision_url = st.vision_http_url
        if not vision_url:
            return ""
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        payload = {
            "model": st.vision_model or "qwen3-vl",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 256,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if st.vision_api_key:
            headers["Authorization"] = f"Bearer {st.vision_api_key}"
        url = f"{vision_url.rstrip('/')}/v1/chat/completions"
        with vram_guard("vision"):
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - VLM 不可用属正常降级
        logger.warning("[audit] 视觉语义调用失败: %s", exc)
        return ""


def _extract_target_proteins(image_path: str, timeout: int = 120) -> set[str]:
    """VLM 提取图内检测目标蛋白/分子，归一化为小写 token 集合。"""
    raw = _ask_vision(image_path, _RELABEL_PROTEIN_PROMPT, timeout)
    if not raw:
        return set()
    raw_stripped = raw.strip()
    if raw_stripped in {"无", "无。", "None", "N/A", "n/a"}:
        return set()
    tokens: set[str] = set()
    for part in re.split(r"[，,、;；/\s]+", raw_stripped):
        p = part.strip().lower()
        if p and p not in {"无", "蛋白", "分子", "检测", "图"}:
            tokens.add(p)
    return tokens


def detect_relabeled_image_reuse(
    figures_by_paper: dict[str, list[str | Path]],
    *,
    ncc_threshold: float = DEFAULT_BAND_NCC_THRESHOLD,
    max_pairs: int = 20,
) -> list[dict]:
    """「改标图片复用」证据链：跨论文 NCC 条带复用（像素）+ VLM 语义标签比对。

    同一张条带/图片被像素级复用，但两篇论文标注的目标蛋白/分子不同 → 改标复用
    （论文工厂典型手法，如把 AMPK 条带改标为 GAPDH）。证据链三步：
    1. 像素层：detect_cross_paper_band_reuse 的 NCC（确定性）。
    2. 语义层：VLM 提取两图各自的目标蛋白集合。
    3. 判定：NCC ≥ 阈值 且 蛋白集合无交集 → RELABELED_IMAGE_REUSE。

    VLM 不可用时语义层降级 → 不出「改标」结论（返回空），因为缺了标签不一致
    这一半证据。返回 make_finding 结构的 list[dict]。
    """
    band_cands = detect_cross_paper_band_reuse(figures_by_paper, ncc_threshold=ncc_threshold)
    if not band_cands:
        return []

    # 去重到 (fig_a, fig_b) 图对，保留最高 NCC
    pair_best: dict[tuple[str, str], dict] = {}
    for c in band_cands:
        key = (c["fig_a"], c["fig_b"])
        if key not in pair_best or c["ncc"] > pair_best[key]["ncc"]:
            pair_best[key] = c

    findings: list[dict] = []
    for (fig_a, fig_b), cand in list(pair_best.items())[:max_pairs]:
        prots_a = _extract_target_proteins(fig_a)
        prots_b = _extract_target_proteins(fig_b)
        # 任一侧提取失败/无蛋白 → 无法判定改标，跳过（fail-open）
        if not prots_a or not prots_b:
            continue
        if prots_a & prots_b:
            # 标签一致（同一蛋白）→ 只是复用，不是改标，不属于本检测
            continue
        # 图标识带论文名：同名的图（如 p3_i0.png）可能同时存在于多篇论文目录
        # （副本论文），只用 basename 会让跨论文证据链在报告/CSV 里不可区分。
        fig_a_label = f"{cand['paper_a']}/{Path(fig_a).name}"
        fig_b_label = f"{cand['paper_b']}/{Path(fig_b).name}"
        findings.append(
            make_finding(
                "RELABELED_IMAGE_REUSE",
                title=(f"{fig_a_label} 与 {fig_b_label} 条带像素复用但标签不一致"),
                claim=(
                    f"跨论文条带 NCC={cand['ncc']:.3f}（像素级复用），但论文 A 标注 "
                    f"{sorted(prots_a)}，论文 B 标注 {sorted(prots_b)}，目标蛋白/分子"
                    f"无交集，疑似同一图片改标复用"
                ),
                computed=(
                    f"像素证据：NCC={cand['ncc']:.3f}，band A={cand['band_a']}，"
                    f"band B={cand['band_b']}\n"
                    f"语义证据：{fig_a_label} → {sorted(prots_a)}；"
                    f"{fig_b_label} → {sorted(prots_b)}"
                ),
                method=("跨论文 NCC 条带复用（像素层）+ Qwen3-VL 目标蛋白提取（语义层）"),
                evidence_sources=[
                    {
                        "type": "figure",
                        "figure_id": fig_a_label,
                        "other_figure_id": fig_b_label,
                    },
                    {
                        "type": "figure",
                        "figure_id": fig_b_label,
                        "other_figure_id": fig_a_label,
                    },
                ],
                normal_explanation=(
                    "不同蛋白的条带在相似实验条件下外观可能相近；是否真为改标复用"
                    "需人工对照两篇全文与原始数据确认"
                ),
                needs_human_review=True,
            )
        )
    return findings


# ── P0-14 单图内 copy-move / 条带克隆检测 ───────────────────────
# 现有复用检测只查「图与图之间」；单图内把一块克隆到另一处（copy-move）或
# 把一条 western blot 条带在同图内复制（条带克隆）都抓不到。本段补上：
# - copy-move：SIFT 自匹配 → estimateAffine2D 几何验证（旋转/缩放/平移不变）。
#   镜像翻转（克隆后左右/上下翻）需在 原图/H翻转/V翻转 三假设上重提特征再匹配
#   （SIFT 非镜像不变，与跨图 _flip_aware_sift_match 同思路）。全局对称/重复纹理
#   会让 RANSAC 收敛到整图自映射退化解，改在源点最密集局部簇重拟合恢复。
#   分离/紧凑/IoU 三重校验防重复纹理误报。
# - 条带克隆：复用 _detect_band_regions + NCC，同图内两条带像素级重复。
DEFAULT_CMFD_RATIO = 0.75
DEFAULT_CMFD_MIN_MATCHES = 10
# 绝对 inlier 下限：网格线/图表刻度等重复对称结构会在翻转假设下产生 ~8-9 个
# 伪一致 inlier（实测 320px 网格纹理），而真克隆（含 1.8x 上采样克隆 14 个）
# 至少 ~14 个。取 12 挡在噪声与真克隆之间。
DEFAULT_CMFD_MIN_INLIERS = 12
DEFAULT_CMFD_MIN_SEPARATION = 0.15  # 源/目标平均位移 ≥ 15% 图像对角线（旋转/缩放也适用）
DEFAULT_CMFD_MAX_REGION_FRAC = 0.30  # 克隆区域面积 ≤ 30% 图像
DEFAULT_CMFD_MAX_BBOX_OVERLAP = 0.3  # 源/目标区域 IoU 上限（防重叠自匹配）
DEFAULT_CMFD_LOCAL_WINDOW_FRAC = 0.15  # 局部簇恢复窗口（复用子面板密度启发）
DEFAULT_CMFD_SAME_LOCATION_DIST = 4.0  # 翻转假设「同位置」匹配过滤半径 (px)


def _points_bbox(points: np.ndarray) -> tuple[int, int, int, int]:
    """点集边界框 (x, y, w, h)。"""
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def _bbox_iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    """两个 (x, y, w, h) 边界框的 IoU。"""
    x1, y1, w1, h1 = a
    x2, y2, w2, h2 = b
    ix = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    iy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    inter = ix * iy
    union = w1 * h1 + w2 * h2 - inter
    return inter / union if union > 0 else 0.0


def _spatial_skip(kp_q: Any, kp_t: Any, unflip) -> Any:
    """翻转假设的「同位置」匹配过滤：unflip 后与 query 点重合 < 阈值视为平凡匹配。"""

    def skip(m: Any) -> bool:
        qx, qy = kp_q[m.queryIdx].pt
        tx, ty = unflip(*kp_t[m.trainIdx].pt)
        return float(np.hypot(qx - tx, qy - ty)) < DEFAULT_CMFD_SAME_LOCATION_DIST

    return skip


def _ratio_good_matches(cv2: Any, des_q: Any, des_t: Any, skip) -> list:
    """BFMatcher ratio 测试，skip 谓词剔除平凡匹配（自身 / 同位置镜像）。

    k=4 预留候选余量：identity 假设需跳过 1 个自身匹配；翻转假设下与自身位置
    重合的镜像匹配数不固定。
    """
    bf = cv2.BFMatcher()
    try:
        knn = bf.knnMatch(des_q, des_t, k=4)
    except cv2.error as e:  # noqa: BLE001 - 自匹配失败按无篡改处理
        logger.debug("[audit] 单图自匹配 knnMatch 失败: %s", e)
        return []
    good: list = []
    for cands in knn:
        kept = [m for m in cands if not skip(m)]
        if len(kept) < 2:
            continue
        m, n = kept[0], kept[1]
        if n.distance <= 0:
            continue  # 描述子完全相同，无法区分孪生与噪声
        if m.distance < DEFAULT_CMFD_RATIO * n.distance:
            good.append(m)
    return good


def _copy_move_match_hypotheses(cv2: Any, img: Any):
    """单图内 copy-move 的匹配假设：identity / 水平翻转 / 垂直翻转。

    每个假设 yield (src_pts, dst_pts)——两者均为 img 坐标系的 (n,1,2) 数组：
    翻转假设的 train 点已 unflip 回 img 坐标。SIFT 描述子非镜像不变（且
    estimateAffine2D 无法拟合反射），镜像克隆须在翻转图上重提特征再匹配——
    因此对每个翻转假设重跑 detectAndCompute。
    """
    h, w = img.shape[:2]
    sift = cv2.SIFT_create()
    kp, des = sift.detectAndCompute(img, None)
    if des is None or len(kp) < 2 * DEFAULT_CMFD_MIN_MATCHES:
        return

    variants: list[tuple[Any, Any]] = [
        (img, lambda x, y: (x, y)),
        (cv2.flip(img, 1), lambda x, y: (w - 1 - x, y)),
        (cv2.flip(img, 0), lambda x, y: (x, h - 1 - y)),
    ]
    for variant, unflip in variants:
        if variant is img:
            kp_t, des_t = kp, des
            skip = lambda m: m.trainIdx == m.queryIdx  # noqa: E731
        else:
            kp_t, des_t = sift.detectAndCompute(variant, None)
            if des_t is None or len(kp_t) < 2 * DEFAULT_CMFD_MIN_MATCHES:
                continue
            skip = _spatial_skip(kp, kp_t, unflip)
        good = _ratio_good_matches(cv2, des, des_t, skip)
        if len(good) < DEFAULT_CMFD_MIN_MATCHES:
            continue
        src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([unflip(*kp_t[m.trainIdx].pt) for m in good]).reshape(-1, 1, 2)
        yield src, dst


def _fit_affine(cv2: Any, src: Any, dst: Any) -> tuple[Any, Any, int, Any] | None:
    """RANSAC 仿射拟合（estimateAffine2D，6 自由度：旋转+缩放+剪切+平移）。

    返回 (src_in, dst_in, inliers, M) 或 None。仿射比 8 自由度单应更贴合
    「克隆→平移/旋转/缩放」的真实变换，也更难拟合重复纹理的退化解。
    """
    try:
        M, mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    except cv2.error as e:  # noqa: BLE001 - 几何验证失败按无篡改处理
        logger.debug("[audit] 单图自匹配 estimateAffine2D 失败: %s", e)
        return None
    if mask is None:
        return None
    mask_arr = mask.ravel().astype(bool)
    inliers = int(mask_arr.sum())
    if inliers < DEFAULT_CMFD_MIN_INLIERS:
        return None
    return src[mask_arr].reshape(-1, 2), dst[mask_arr].reshape(-1, 2), inliers, M


def _fit_local_cluster(
    cv2: Any,
    src: Any,
    dst: Any,
    img_shape: tuple[int, int],
) -> tuple[Any, Any, int, Any] | None:
    """在源点最密集的局部簇上重新拟合。

    全局对称/重复纹理会让首次 RANSAC 收敛到「整图自映射」退化解（inlier 遍布
    全图、bbox≈整图），把真实克隆吞掉。改在源点最密集窗口内重拟合可恢复克隆
    区域（实测 180° 旋转克隆：全局拟合被对称网格纹理吞掉，局部簇重拟合恢复）。
    """
    n = len(src)
    if n < DEFAULT_CMFD_MIN_INLIERS:
        return None
    h, w = img_shape
    radius = DEFAULT_CMFD_LOCAL_WINDOW_FRAC * float(np.hypot(h, w))
    pts = src.reshape(-1, 2).astype(np.float64)
    best = 0
    center: Any = None
    for i in range(n):
        d = np.linalg.norm(pts - pts[i], axis=1)
        c = int((d <= radius).sum())
        if c > best:
            best = c
            center = pts[i]
    if center is None or best < DEFAULT_CMFD_MIN_INLIERS:
        return None
    sel = np.linalg.norm(pts - center, axis=1) <= radius
    return _fit_affine(cv2, src[sel], dst[sel])


def _validate_copy_move(
    src_in: Any,
    dst_in: Any,
    diag: float,
    h: int,
    w: int,
) -> dict | None:
    """分离 / 紧凑 / IoU 三重校验，通过返回 finding 字段 dict，否则 None。

    分离改用「平均位移」而非质心距离：质心距离对绕中心的旋转不敏感（源/目标
    质心可能几乎重合），平均位移在任何平移/旋转/缩放下都反映克隆被挪开的距离。
    """
    disp = float(np.mean(np.linalg.norm(dst_in - src_in, axis=1)))
    if disp < DEFAULT_CMFD_MIN_SEPARATION * diag:
        return None
    bbox_src = _points_bbox(src_in)
    bbox_dst = _points_bbox(dst_in)
    if (
        bbox_src[2] * bbox_src[3] > DEFAULT_CMFD_MAX_REGION_FRAC * h * w
        or bbox_dst[2] * bbox_dst[3] > DEFAULT_CMFD_MAX_REGION_FRAC * h * w
    ):
        return None
    if _bbox_iou(bbox_src, bbox_dst) > DEFAULT_CMFD_MAX_BBOX_OVERLAP:
        return None
    return {
        "src_bbox": bbox_src,
        "dst_bbox": bbox_dst,
        "inliers": len(src_in),
        "separation": round(disp, 1),
    }


def _affine_rotation_scale(M: Any) -> tuple[float, float]:
    """从 2x3 仿射矩阵提取旋转角（度）与近似缩放。"""
    a, c = float(M[0, 0]), float(M[1, 0])
    angle = float(np.degrees(np.arctan2(c, a)))
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    return round(angle, 1), round(scale, 3)


def _detect_copy_move_in_image(cv2: Any, img: Any) -> dict | None:
    """单图内 SIFT 自匹配 copy-move 检测（平移/旋转/缩放 + 镜像翻转）。

    返回 ``{src_bbox, dst_bbox, good_matches, inliers, separation, rotation,
    scale}`` 或 None。

    - 旋转/缩放：SIFT 描述子旋转+尺度不变，estimateAffine2D（仿射）拟合。
    - 镜像翻转：SIFT 非镜像不变，改在 原图/H翻转/V翻转 三假设上重提特征
      自匹配（与跨图 _flip_aware_sift_match 同思路）。
    - 退化解恢复：全局对称/重复纹理让 RANSAC 收敛到整图自映射时，改在源点
      最密集局部簇重拟合。
    identity 假设须显式剔除 trainIdx==queryIdx（自身匹配距离 0 且未必排首位）；
    翻转假设须剔除「同位置」匹配（unflip 后与 query 点重合 < 4px）。
    """
    h, w = img.shape[:2]
    diag = float(np.hypot(h, w))
    best: dict | None = None
    for src, dst in _copy_move_match_hypotheses(cv2, img):
        good_count = len(src)
        candidates = [
            _fit_affine(cv2, src, dst),
            _fit_local_cluster(cv2, src, dst, (h, w)),
        ]
        for cand in candidates:
            if cand is None:
                continue
            src_in, dst_in, inliers, M = cand
            res = _validate_copy_move(src_in, dst_in, diag, h, w)
            if res is None:
                continue
            if best is None or inliers > best["inliers"]:
                res["good_matches"] = good_count
                res["rotation"], res["scale"] = _affine_rotation_scale(M)
                best = res
    return best


def _detect_intra_image_band_duplication(
    cv2: Any,
    img: Any,
    *,
    ncc_threshold: float = DEFAULT_BAND_NCC_THRESHOLD,
) -> dict | None:
    """同图内 western-blot 条带克隆：两条带 NCC 像素级重复且位置不同。

    SIFT 对细条带特征点不足，条带克隆走 NCC 自比对（与跨论文条带复用同判据）。
    返回 ``{band_a, band_b, ncc}``（取最高 NCC 的一对）或 None。
    """
    regions = _detect_band_regions(cv2, img)
    if len(regions) < 2:
        return None
    best: dict | None = None
    for (x1, y1, w1, h1), (x2, y2, w2, h2) in combinations(regions, 2):
        # 同一条带的两个连通域会位置重叠，跳过
        if abs(x1 - x2) < max(w1, w2) and abs(y1 - y2) < max(h1, h2):
            continue
        crop1 = img[y1 : y1 + h1, x1 : x1 + w1]
        crop2 = img[y2 : y2 + h2, x2 : x2 + w2]
        ncc = _resized_ncc(cv2, crop1, crop2)
        if ncc < ncc_threshold:
            continue
        cand = {
            "band_a": (int(x1), int(y1), int(w1), int(h1)),
            "band_b": (int(x2), int(y2), int(w2), int(h2)),
            "ncc": round(ncc, 4),
        }
        if best is None or ncc > best["ncc"]:
            best = cand
    return best


def detect_intra_image_copy_move(
    db: Session,
    paper_id: str,
    *,
    min_ransac_inliers: int = DEFAULT_CMFD_MIN_INLIERS,
    ncc_threshold: float = DEFAULT_BAND_NCC_THRESHOLD,
) -> list[dict]:
    """P0-14 入口：对论文每张图做单图内 copy-move / 条带克隆检测。

    产出 IMAGE_TAMPERING_CANDIDATE（severity=high，needs_human_review=True）。
    cv2 缺失 / 无图 / 无可疑区域均 fail-open 返回空，不阻塞审计。
    """
    try:
        import cv2
    except ImportError:
        logger.warning("cv2 未安装，单图内篡改检测跳过")
        return []

    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page, PaperFigure.figure_index)
        .all()
    )
    findings: list[dict] = []
    for fig in figs:
        p = _resolve_figure_path(fig, paper_id)
        if p is None:
            continue
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        label = _figure_label(fig)

        cm = _detect_copy_move_in_image(cv2, img)
        if cm and cm["inliers"] >= min_ransac_inliers:
            x0, y0, xw, yh = cm["src_bbox"]
            findings.append(
                make_finding(
                    "IMAGE_TAMPERING_CANDIDATE",
                    title=f"{label} 图内疑似复制-粘贴（copy-move）",
                    page=fig.page,
                    bbox=[float(x0), float(y0), float(x0 + xw), float(y0 + yh)],
                    claim=(
                        f"单图内 SIFT 自匹配发现 {cm['inliers']} 个几何一致匹配，"
                        f"源区域 {cm['src_bbox']} 与目标区域 {cm['dst_bbox']} 平均位移"
                        f" {cm['separation']}px"
                    ),
                    computed=(
                        f"仿射 inliers={cm['inliers']}（≥{DEFAULT_CMFD_MIN_INLIERS}），"
                        f"好匹配 {cm['good_matches']}，"
                        f"平均位移 {cm['separation']}px，"
                        f"旋转 {cm['rotation']}°、缩放 ×{cm['scale']}"
                    ),
                    method=(
                        "SIFT 自匹配（原图/水平翻转/垂直翻转三假设）+ estimateAffine2D "
                        "RANSAC 几何验证 + 局部簇退化恢复 + 分离/紧凑/IoU 校验"
                    ),
                    evidence_sources=[{"type": "figure", "figure_id": label, "page": fig.page}],
                    normal_explanation=(
                        "重复纹理/对称图案可能被误判；是否真为复制-粘贴篡改需人工对照原始图像确认"
                    ),
                    needs_human_review=True,
                )
            )

        band = _detect_intra_image_band_duplication(cv2, img, ncc_threshold=ncc_threshold)
        if band:
            x0, y0, xw, yh = band["band_a"]
            findings.append(
                make_finding(
                    "IMAGE_TAMPERING_CANDIDATE",
                    title=f"{label} 图内条带疑似克隆",
                    page=fig.page,
                    bbox=[float(x0), float(y0), float(x0 + xw), float(y0 + yh)],
                    claim=(
                        f"两条带 NCC={band['ncc']}（像素级重复），"
                        f"位置 {band['band_a']} 与 {band['band_b']} 不同"
                    ),
                    computed=(
                        f"NCC={band['ncc']}（阈值 {ncc_threshold}），"
                        f"band A={band['band_a']}，band B={band['band_b']}"
                    ),
                    method="western blot 条带提取 + 同图条带 NCC 自比对",
                    evidence_sources=[{"type": "figure", "figure_id": label, "page": fig.page}],
                    normal_explanation=(
                        "同一样本重复上样（技术重复）也可能产生相似条带；是否克隆"
                        "需人工对照原始数据确认"
                    ),
                    needs_human_review=True,
                )
            )
    return findings
