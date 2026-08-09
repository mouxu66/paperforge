"""实验审计 P0-7 数据泄漏 + P0-9 曲线复用单测。

- data_leakage：精确碰撞 / pHash 近似 / 目录校验 / 无重复干净通过
- figure_reuse：同一图两份 → SIFT/RANSAC 验证通过；无关图 → 无 Finding
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from mock_api.experiment_audit import data_leakage, figure_reuse
from mock_api.models import Paper, PaperFigure

cv2 = pytest.importorskip("cv2")
imagehash = pytest.importorskip("imagehash")
PIL_Image = pytest.importorskip("PIL.Image")


def _write_image(path, seed: int, size: int = 128):
    """生成确定性随机纹理图（保证 SIFT 有特征点）。"""
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (size, size), dtype=np.uint8)
    # 加粗结构，增加角点
    for i in range(0, size, 16):
        img[i : i + 4, :] = 0
        img[:, i : i + 4] = 0
    cv2.imwrite(str(path), img)


# ---------------------------------------------------------------------------
# P0-7 数据泄漏
# ---------------------------------------------------------------------------
class TestDataLeakage:
    def test_exact_collision_high(self, tmp_path):
        train, test = tmp_path / "train", tmp_path / "test"
        train.mkdir()
        test.mkdir()
        _write_image(train / "a.png", seed=1)
        shutil.copy(train / "a.png", test / "a_copy.png")  # 完全相同
        findings = data_leakage.check_data_leakage(str(train), str(test))
        highs = [f for f in findings if f["severity"] == "high"]
        assert len(highs) == 1
        assert highs[0]["type"] == "DATA_LEAKAGE_CANDIDATE"
        # 精确碰撞不应再产生 pHash medium 双重上报
        assert len(findings) == 1

    def test_near_duplicate_medium(self, tmp_path):
        train, test = tmp_path / "train", tmp_path / "test"
        train.mkdir()
        test.mkdir()
        _write_image(train / "a.png", seed=7)
        # 近似：同图轻微亮度变化
        img = cv2.imread(str(train / "a.png"))
        cv2.imwrite(str(test / "a_bright.png"), cv2.add(img, 10))
        findings = data_leakage.check_data_leakage(str(train), str(test))
        mediums = [f for f in findings if f["severity"] == "medium"]
        assert len(mediums) == 1
        assert "相似" in mediums[0]["title"]

    def test_disjoint_sets_clean(self, tmp_path):
        train, test = tmp_path / "train", tmp_path / "test"
        train.mkdir()
        test.mkdir()
        _write_image(train / "a.png", seed=11)
        _write_image(test / "b.png", seed=99)
        assert data_leakage.check_data_leakage(str(train), str(test)) == []

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(ValueError):
            data_leakage.check_data_leakage(str(tmp_path / "nope"), str(tmp_path))

    def test_empty_dirs_clean(self, tmp_path):
        train, test = tmp_path / "train", tmp_path / "test"
        train.mkdir()
        test.mkdir()
        assert data_leakage.check_data_leakage(str(train), str(test)) == []


# ---------------------------------------------------------------------------
# P0-9 曲线复用
# ---------------------------------------------------------------------------
class TestFigureReuse:
    def _seed(self, db, tmp_path, paper_id="p-reuse"):
        db.add(Paper(id=paper_id, title="Reuse Test"))
        fig_dir = tmp_path / "figures" / paper_id
        fig_dir.mkdir(parents=True, exist_ok=True)
        # Figure 1 与 Figure 2 为同一张图（复用）；Figure 3 无关
        _write_image(fig_dir / "f1.png", seed=42)
        shutil.copy(fig_dir / "f1.png", fig_dir / "f2.png")
        _write_image(fig_dir / "f3.png", seed=1234)
        for i, name in enumerate(["f1.png", "f2.png", "f3.png"], start=1):
            db.add(
                PaperFigure(
                    paper_id=paper_id,
                    page=i,
                    figure_index=0,
                    figure_path=name,
                    figure_number=i,
                )
            )
        db.commit()
        return paper_id

    def test_duplicate_pair_detected(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "mock_api.experiment_audit.figure_reuse._get_uploads_dir",
            lambda: tmp_path,
        )
        paper_id = self._seed(db_session, tmp_path)
        findings = figure_reuse.detect_figure_reuse(db_session, paper_id)
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "FIGURE_REUSE_CANDIDATE"
        assert f["severity"] == "low"
        assert "Figure 1" in f["title"] and "Figure 2" in f["title"]
        assert f["needs_human_review"] is True

    def test_single_figure_returns_empty(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "mock_api.experiment_audit.figure_reuse._get_uploads_dir",
            lambda: tmp_path,
        )
        db_session.add(Paper(id="p-one", title="One"))
        db_session.add(
            PaperFigure(
                paper_id="p-one", page=1, figure_index=0, figure_path="x.png"
            )
        )
        db_session.commit()
        assert figure_reuse.detect_figure_reuse(db_session, "p-one") == []

    def test_missing_files_fail_open(self, db_session):
        db_session.add(Paper(id="p-missing", title="Missing"))
        db_session.add(
            PaperFigure(
                paper_id="p-missing", page=1, figure_index=0, figure_path="gone1.png"
            )
        )
        db_session.add(
            PaperFigure(
                paper_id="p-missing", page=2, figure_index=0, figure_path="gone2.png"
            )
        )
        db_session.commit()
        assert figure_reuse.detect_figure_reuse(db_session, "p-missing") == []
