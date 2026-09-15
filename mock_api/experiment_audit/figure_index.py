"""全库图片复用粗筛索引（pHash 汉明近邻，替代全库 O(N²) SIFT/NCC 召回）。

召回阶段只算 pHash（整库几百张图秒级），候选对再交 SIFT/RANSAC 或 NCC 精确
验证——「粗筛索引 + 精确验证」两级管线，避免 detect_cross_paper_reuse 的
全库两两 SIFT 开销。与 figure_reuse 的召回口径一致：

- 每条目带「原图 / 水平翻转 / 垂直翻转」三 pHash 假设（镜像复用可召回）；
- 任一对假设汉明距离 < 阈值即命中（复用 _min_hash_distance 语义）。

用途：
1. ``build_index`` 从 figures_by_paper 建库内索引；
2. ``recall`` 单图查库（T1：单篇 run_paper_audit 的跨论文比对）；
3. ``paper_candidates`` 整篇论文对库内其他论文的候选对；
4. ``to_json/from_json`` 离线构建后持久化（scripts/build_figure_index.py）。

依赖缺失（imagehash/Pillow）或坏图一律 fail-open 跳过，不抛异常。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .figure_reuse import _flip_aware_phashes

logger = logging.getLogger(__name__)

DEFAULT_PHASH_RECALL_THRESHOLD = 15


def _hash_to_int(h) -> int:
    """imagehash.ImageHash → int（汉明距离用位运算，比 str 快）。"""
    return int(str(h), 16)


@dataclass
class IndexEntry:
    """单张图的索引条目。hashes = (原图, H翻转, V翻转) 的 int 三假设。"""

    paper_id: str
    path: str
    hashes: tuple[int, int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "path": self.path,
            "hashes": [format(h, "016x") for h in self.hashes],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IndexEntry:
        return cls(
            paper_id=str(d["paper_id"]),
            path=str(d["path"]),
            hashes=tuple(int(h, 16) for h in d["hashes"]),
        )


def _entry_hashes(path: str | Path) -> tuple[int, int, int] | None:
    """计算某图的翻转感知三 pHash；坏图/依赖缺失返回 None。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            ph = _flip_aware_phashes(im)
        return (_hash_to_int(ph[0]), _hash_to_int(ph[1]), _hash_to_int(ph[2]))
    except Exception as exc:  # noqa: BLE001 - 坏图/缺依赖跳过
        logger.debug("[figure-index] pHash 计算失败（跳过）: %s - %s", path, exc)
        return None


def _min_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """两组三假设间的最小汉明距离（与 figure_reuse._min_hash_distance 同口径）。"""
    return min((x ^ y).bit_count() for x in a for y in b)


@dataclass
class FigureIndex:
    """全库图 pHash 索引：entries 全量 + by_paper 分组。"""

    entries: list[IndexEntry] = field(default_factory=list)
    threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD
    _by_paper: dict[str, list[IndexEntry]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._by_paper:
            for e in self.entries:
                self._by_paper.setdefault(e.paper_id, []).append(e)

    # ── 查询 ──────────────────────────────────────────────
    def recall(
        self,
        hashes: tuple[int, int, int],
        *,
        exclude_paths: set[str] | None = None,
    ) -> list[IndexEntry]:
        """单图查库：返回汉明距离 < 阈值（min 三假设）的条目，按距离升序。"""
        exclude = exclude_paths or set()
        hits: list[tuple[int, IndexEntry]] = []
        for e in self.entries:
            if e.path in exclude:
                continue
            d = _min_dist(hashes, e.hashes)
            if d < self.threshold:
                hits.append((d, e))
        hits.sort(key=lambda x: (x[0], x[1].paper_id, x[1].path))
        return [e for _, e in hits]

    def paper_candidates(
        self,
        paper_id: str,
        *,
        threshold: int | None = None,
    ) -> list[tuple[int, IndexEntry, IndexEntry]]:
        """某篇论文的图 vs 库内其他论文：返回 [(dist, 本文条目, 他文条目)]。

        只取跨论文对（排除同论文），每对按 (dist, paper, path) 排序去重。
        """
        if threshold is not None:
            self.threshold = threshold
        mine = self._by_paper.get(paper_id, [])
        exclude = {e.path for e in mine}
        seen: set[tuple[str, str]] = set()
        out: list[tuple[int, IndexEntry, IndexEntry]] = []
        for me in mine:
            for other in self.recall(me.hashes, exclude_paths=exclude):
                key = tuple(sorted((me.path, other.path)))
                if key in seen:
                    continue
                seen.add(key)
                d = _min_dist(me.hashes, other.hashes)
                out.append((d, me, other))
        out.sort(key=lambda x: (x[0], x[1].paper_id, x[1].path))
        return out

    # ── 持久化 ────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FigureIndex:
        return cls(
            entries=[IndexEntry.from_dict(e) for e in d.get("entries", [])],
            threshold=int(d.get("threshold", DEFAULT_PHASH_RECALL_THRESHOLD)),
        )

    def save(self, path: str | Path) -> None:
        import json

        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> FigureIndex:
        import json

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def build_index(
    figures_by_paper: dict[str, list[str | Path]],
    *,
    threshold: int = DEFAULT_PHASH_RECALL_THRESHOLD,
) -> FigureIndex:
    """从 figures_by_paper 构建全库索引（坏图/依赖缺失自动跳过，fail-open）。"""
    entries: list[IndexEntry] = []
    for paper_id, paths in figures_by_paper.items():
        for p in paths:
            hashes = _entry_hashes(p)
            if hashes is None:
                continue
            entries.append(IndexEntry(paper_id=str(paper_id), path=str(p), hashes=hashes))
    return FigureIndex(entries=entries, threshold=threshold)
