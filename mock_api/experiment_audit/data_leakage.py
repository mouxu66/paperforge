"""P0-7 数据泄漏初筛（train/test 数据集目录比对）。

独立于 PDF：用户提供数据集目录路径，端点触发。
两级检测：
1. SHA-256 精确碰撞 → severity high（完全相同文件出现在 train/test）。
2. pHash 汉明距离 ≤ 阈值 → severity medium（近似重复，可能裁剪/重压缩）。

O(N*M) 比对对大数据集较慢，调用方可通过 limit 控制样本上限。
"""

from __future__ import annotations

import hashlib
import logging
from glob import glob
from pathlib import Path

from .schemas import make_finding

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif", ".gif"}

DEFAULT_PHASH_THRESHOLD = 10
DEFAULT_LIMIT = 2000  # 每侧最多比对的图片数（防止 O(N*M) 爆炸）


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_images(directory: str, limit: int) -> list[str]:
    paths: list[str] = []
    for p in sorted(glob(str(Path(directory) / "**" / "*"), recursive=True)):
        if Path(p).suffix.lower() in _IMAGE_EXTS:
            paths.append(p)
            if len(paths) >= limit:
                break
    return paths


def check_data_leakage(
    train_dir: str,
    test_dir: str,
    *,
    exact_hash: bool = True,
    phash_threshold: int = DEFAULT_PHASH_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """train/test 泄漏初筛。目录不存在时抛 ValueError（端点层转 400）。"""
    if not Path(train_dir).is_dir():
        raise ValueError(f"train 目录不存在: {train_dir}")
    if not Path(test_dir).is_dir():
        raise ValueError(f"test 目录不存在: {test_dir}")

    train_paths = _list_images(train_dir, limit)
    test_paths = _list_images(test_dir, limit)
    findings: list[dict] = []
    if not train_paths or not test_paths:
        return findings

    # ── 1. 精确 hash 碰撞 ──
    collisions: list[tuple[str, str]] = []
    if exact_hash:
        train_hashes: dict[str, str] = {}
        for p in train_paths:
            try:
                train_hashes.setdefault(_sha256_file(p), p)
            except OSError as e:
                logger.debug("[audit] SHA256 计算失败 (train): %s - %s", p, e)
                continue
        for p in test_paths:
            try:
                h = _sha256_file(p)
            except OSError as e:
                logger.debug("[audit] SHA256 计算失败 (test): %s - %s", p, e)
                continue
            if h in train_hashes:
                collisions.append((train_hashes[h], p))
        if collisions:
            findings.append(
                make_finding(
                    "DATA_LEAKAGE_CANDIDATE",
                    severity="high",
                    title=f"train/test 存在 {len(collisions)} 个完全相同文件",
                    claim=f"SHA-256 碰撞样本: {collisions[0][0]} ↔ {collisions[0][1]}",
                    computed=f"共 {len(collisions)} 对（train {len(train_paths)} 张 / test {len(test_paths)} 张）",
                    method="SHA-256 精确比对",
                    evidence_sources=[
                        {
                            "type": "text",
                            "snippet": f"{a} ↔ {b}"[:400],
                        }
                        for a, b in collisions[:20]
                    ],
                    normal_explanation=(
                        "可能是数据划分脚本未去重，或测试集由训练集派生；需确认划分逻辑"
                    ),
                    needs_human_review=True,
                )
            )

    # ── 2. pHash 近似重复（已精确碰撞的文件不再参与，避免双重上报）──
    collided_test = {b for _, b in (collisions if exact_hash else [])}
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        logger.warning("imagehash/Pillow 未安装，pHash 近似泄漏检测跳过")
        return findings

    def _phash_map(paths: list[str]) -> list[tuple]:
        out = []
        for p in paths:
            try:
                with Image.open(p) as im:
                    out.append((imagehash.phash(im), p))
            except Exception as e:  # noqa: BLE001 - 坏图跳过
                logger.debug("[audit] pHash 计算失败 (坏图跳过): %s - %s", p, e)
                continue
        return out

    train_ph = _phash_map(train_paths)
    test_ph = _phash_map([p for p in test_paths if p not in collided_test])
    near_dupes: list[tuple[str, str, int]] = []
    for t_hash, t_path in test_ph:
        for tr_hash, tr_path in train_ph:
            dist = t_hash - tr_hash
            if dist <= phash_threshold:
                near_dupes.append((tr_path, t_path, dist))
    if near_dupes:
        findings.append(
            make_finding(
                "DATA_LEAKAGE_CANDIDATE",
                severity="medium",
                title=f"train/test 存在 {len(near_dupes)} 对高度相似图像",
                claim=(
                    f"最相似样本距离 {min(d for _, _, d in near_dupes)}（阈值 {phash_threshold}）"
                ),
                computed=f"pHash 汉明距离 ≤ {phash_threshold}",
                method="imagehash.phash 感知哈希比对",
                evidence_sources=[
                    {"type": "text", "snippet": f"{a} ↔ {b} (dist={d})"[:400]}
                    for a, b, d in near_dupes[:30]
                ],
                normal_explanation=(
                    "相似可能是同一场景的不同视角/增强样本；pHash 对裁剪和重压缩敏感，需人工确认"
                ),
                needs_human_review=True,
            )
        )
    return findings
