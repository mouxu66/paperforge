"""T2 全库图片复用粗筛索引单测（figure_index.py）。

覆盖：
- build_index + recall：跨论文同图召回、镜像翻转召回、无关图不召回
- paper_candidates：只出跨论文对、排除同论文自匹配
- save/load 持久化往返
- 坏图/依赖缺失 fail-open（不抛异常、跳过条目）
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
imagehash = pytest.importorskip("imagehash")  # noqa: F401 - 依赖探测
PIL_Image = pytest.importorskip("PIL.Image")  # noqa: F401 - 依赖探测

from mock_api.experiment_audit.figure_index import (  # noqa: E402
    FigureIndex,
    IndexEntry,
    build_index,
)


def _texture_array(seed: int, size: int = 128) -> np.ndarray:
    """确定性随机纹理图（保证 SIFT/phash 有区分度）。"""
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (size, size), dtype=np.uint8)
    for i in range(0, size, 16):
        img[i : i + 4, :] = 0
        img[:, i : i + 4] = 0
    return img


def _write_image(path, seed: int, size: int = 128):
    cv2.imwrite(str(path), _texture_array(seed, size))


def _two_paper_figures(tmp_path) -> tuple[Path, Path]:
    """paperA/a1.png 与 paperB/b1.png 为同一张图（跨论文复用）。"""
    pa, pb = tmp_path / "paperA", tmp_path / "paperB"
    pa.mkdir()
    pb.mkdir()
    _write_image(pa / "a1.png", seed=5)
    shutil.copy(pa / "a1.png", pb / "b1.png")
    return pa / "a1.png", pb / "b1.png"


class TestBuildIndexRecall:
    def test_same_image_across_papers_recalled(self, tmp_path):
        p_a, p_b = _two_paper_figures(tmp_path)
        idx = build_index({"paperA": [p_a], "paperB": [p_b]})
        assert len(idx.entries) == 2
        hits = idx.recall(idx.entries[0].hashes, exclude_paths={str(p_a)})
        assert len(hits) == 1  # 排除自身后只命中对方论文的图
        assert hits[0].paper_id == "paperB"
        assert hits[0].path == str(p_b)

    def test_flipped_image_recalled(self, tmp_path):
        """水平翻转复用：翻转感知三假设应仍能召回（距离 0）。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        img = cv2.imread(str(pa / "a1.png"), cv2.IMREAD_GRAYSCALE)
        cv2.imwrite(str(pb / "b1.png"), cv2.flip(img, 1))  # 水平镜像
        idx = build_index({"paperA": [pa / "a1.png"], "paperB": [pb / "b1.png"]})
        me = idx.entries[0]
        hits = idx.recall(me.hashes, exclude_paths={str(pa / "a1.png")})
        assert hits and hits[0].paper_id == "paperB"
        assert hits[0].path == str(pb / "b1.png")

    def test_unrelated_not_recalled(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        _write_image(pb / "b1.png", seed=991)
        idx = build_index({"paperA": [pa / "a1.png"], "paperB": [pb / "b1.png"]})
        me = next(e for e in idx.entries if e.paper_id == "paperA")
        assert idx.recall(me.hashes, exclude_paths={str(pa / "a1.png")}) == []

    def test_bad_image_fail_open(self, tmp_path):
        """坏图/非图片文件跳过不抛异常。"""
        pa = tmp_path / "paperA"
        pa.mkdir()
        (pa / "not_image.png").write_bytes(b"this is not a real image")
        idx = build_index({"paperA": [pa / "not_image.png"]})
        assert idx.entries == []


class TestPaperCandidates:
    def test_excludes_same_paper_pairs(self, tmp_path):
        """同论文内的重复图不进入跨论文候选；只返回跨论文对。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        shutil.copy(pa / "a1.png", pa / "a2.png")  # 论文内重复
        shutil.copy(pa / "a1.png", pb / "b1.png")  # 跨论文复用
        idx = build_index(
            {"paperA": [pa / "a1.png", pa / "a2.png"], "paperB": [pb / "b1.png"]}
        )
        cands = idx.paper_candidates("paperA")
        assert len(cands) == 2  # (a1,b1) 与 (a2,b1)，a1-a2 自匹配被排除
        for _, me, other in cands:
            assert me.paper_id == "paperA"
            assert other.paper_id == "paperB"

    def test_no_other_papers_empty(self, tmp_path):
        pa = tmp_path / "paperA"
        pa.mkdir()
        _write_image(pa / "a1.png", seed=5)
        idx = build_index({"paperA": [pa / "a1.png"]})
        assert idx.paper_candidates("paperA") == []


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        p_a, p_b = _two_paper_figures(tmp_path)
        idx = build_index({"paperA": [p_a], "paperB": [p_b]})
        out = tmp_path / "index.json"
        idx.save(out)
        loaded = FigureIndex.load(out)
        assert len(loaded.entries) == len(idx.entries)
        assert loaded.threshold == idx.threshold
        for e, e2 in zip(idx.entries, loaded.entries):
            assert e.paper_id == e2.paper_id
            assert e.path == e2.path
            assert e.hashes == e2.hashes
        # 加载后的召回行为与原索引一致（排除自身路径，与 paper_candidates 同口径）
        hits = loaded.recall(idx.entries[0].hashes, exclude_paths={str(p_a)})
        assert hits[0].path == str(p_b)

    def test_to_from_dict_roundtrip(self):
        e = IndexEntry(paper_id="p", path="x.png", hashes=(1, 2, 3))
        d = FigureIndex(entries=[e], threshold=7).to_dict()
        loaded = FigureIndex.from_dict(d)
        assert loaded.threshold == 7
        assert loaded.entries[0].hashes == (1, 2, 3)
