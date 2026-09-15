"""实验审计 P0-14 单图内 copy-move / 条带克隆单测（figure_reuse.py）。

覆盖：
- _detect_copy_move_in_image：克隆区域命中 / 干净图不误报 / 空白图 / 重叠不报
- _detect_intra_image_band_duplication：同图条带克隆命中 / 不同条带不误报
- detect_intra_image_copy_move：DB 集成（克隆图/克隆条带/干净图/无图）
"""

from __future__ import annotations

import numpy as np
import pytest
from mock_api.experiment_audit import figure_reuse
from mock_api.models import Paper, PaperFigure

cv2 = pytest.importorskip("cv2")


def _texture(seed: int, size: int = 320) -> np.ndarray:
    """确定性随机纹理 + 网格线（保证 SIFT 有大量角点）。"""
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (size, size), dtype=np.uint8)
    for i in range(0, size, 16):
        img[i : i + 4, :] = 0
        img[:, i : i + 4] = 0
    return img


def _clone_image(seed: int = 7, size: int = 320, patch: int = 96) -> np.ndarray:
    """底图灰 + 一块纹理贴两处（copy-move 克隆）。"""
    img = np.full((size, size), 128, np.uint8)
    sub = _texture(seed, patch)
    img[20 : 20 + patch, 20 : 20 + patch] = sub
    img[size - patch - 16 : size - 16, size - patch - 16 : size - 16] = sub
    return img


def _rotation_matrix(degrees: float, scale: float = 1.0, patch: int = 96):
    """绕块中心的旋转（+可选缩放）仿射矩阵。"""
    return cv2.getRotationMatrix2D((patch / 2, patch / 2), degrees, scale)


def _warped_clone(
    seed: int = 7,
    size: int = 320,
    patch: int = 96,
    transform=None,
    flip: int | None = None,
) -> np.ndarray:
    """底图灰 + 一块纹理贴左上，旋转/缩放/镜像变换后贴右下（copy-move 变体）。"""
    img = np.full((size, size), 128, np.uint8)
    sub = _texture(seed, patch)
    img[20 : 20 + patch, 20 : 20 + patch] = sub
    warped = cv2.warpAffine(sub, transform, (patch, patch)) if transform is not None else sub
    if flip is not None:
        warped = cv2.flip(warped, flip)
    img[size - patch - 16 : size - 16, size - patch - 16 : size - 16] = warped
    return img


def _band(seed: int, w: int = 120, h: int = 20) -> np.ndarray:
    """barcode 纹理暗带（每列强度由 seed 决定，保证 NCC 有区分度）。"""
    rng = np.random.RandomState(seed)
    b = np.full((h, w), 245, dtype=np.uint8)
    cols = rng.randint(10, 120, w)
    for x in range(w):
        b[:, x] = cols[x]
    return cv2.GaussianBlur(b, (3, 3), 0)


class TestDetectCopyMoveInImage:
    def test_cloned_patch_detected(self):
        img = _clone_image()
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS
        assert r["separation"] > 0
        # 源/目标 bbox 尺寸相近（都是 ~96x96 的克隆块）
        assert abs(r["src_bbox"][2] - r["dst_bbox"][2]) < 20

    def test_distinct_textures_not_flagged(self):
        img = np.full((320, 320), 128, np.uint8)
        img[20:116, 20:116] = _texture(7, 96)
        img[204:300, 204:300] = _texture(99, 96)
        assert figure_reuse._detect_copy_move_in_image(cv2, img) is None

    def test_blank_image_not_flagged(self):
        img = np.full((320, 320), 128, np.uint8)
        assert figure_reuse._detect_copy_move_in_image(cv2, img) is None

    def test_overlapping_paste_not_flagged(self):
        # 克隆贴回紧邻原块的位置（空间不分离）→ 不报
        img = np.full((320, 320), 128, np.uint8)
        sub = _texture(7, 96)
        img[20:116, 20:116] = sub
        img[30:126, 30:126] = sub  # 大幅重叠
        assert figure_reuse._detect_copy_move_in_image(cv2, img) is None


class TestDetectCopyMoveTransforms:
    """旋转/缩放/镜像克隆：超越纯平移的 copy-move 变体。"""

    @pytest.mark.parametrize("degrees", [15, 30, 90])
    def test_rotation_clone_detected(self, degrees):
        img = _warped_clone(transform=_rotation_matrix(degrees))
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS
        # 旋转角（方向可正可负，取决于 RANSAC 选中的匹配方向）
        assert abs(abs(r["rotation"]) - degrees) < 20

    def test_rotation_180_detected_via_local_recovery(self):
        # 180° 旋转克隆会让 RANSAC 收敛到对称网格的整图自映射退化解，
        # 依赖局部簇恢复才能命中（回归锁定）。
        img = _warped_clone(transform=_rotation_matrix(180))
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS
        assert abs(abs(r["rotation"]) - 180) < 20

    def test_scale_clone_detected(self):
        # 1.8x 上采样克隆；scale 可报 1.8 或其倒数（取决于匹配方向）
        img = _warped_clone(transform=_rotation_matrix(0, 1.8))
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["scale"] > 1.5 or r["scale"] < 0.7

    def test_rotation_scale_clone_detected(self):
        img = _warped_clone(transform=_rotation_matrix(30, 0.7))
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS

    @pytest.mark.parametrize("flip_code", [1, 0])
    def test_mirror_clone_detected(self, flip_code):
        # 镜像克隆：SIFT 非镜像不变，靠 H/V 翻转假设命中
        img = _warped_clone(flip=flip_code)
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS

    def test_rotation_plus_mirror_detected(self):
        img = _warped_clone(transform=_rotation_matrix(45), flip=1)
        r = figure_reuse._detect_copy_move_in_image(cv2, img)
        assert r is not None
        assert r["inliers"] >= figure_reuse.DEFAULT_CMFD_MIN_INLIERS


class TestDetectIntraImageBandDuplication:
    def test_cloned_band_detected(self):
        fig = np.full((200, 200), 255, np.uint8)
        fig[30:50, 40:160] = _band(1)
        fig[120:140, 40:160] = _band(1)  # 克隆
        r = figure_reuse._detect_intra_image_band_duplication(cv2, fig)
        assert r is not None
        assert r["ncc"] >= figure_reuse.DEFAULT_BAND_NCC_THRESHOLD

    def test_distinct_bands_not_flagged(self):
        fig = np.full((200, 200), 255, np.uint8)
        fig[30:50, 40:160] = _band(1)
        fig[120:140, 40:160] = _band(2)  # 不同
        assert figure_reuse._detect_intra_image_band_duplication(cv2, fig) is None

    def test_single_band_not_flagged(self):
        fig = np.full((200, 200), 255, np.uint8)
        fig[30:50, 40:160] = _band(1)
        assert figure_reuse._detect_intra_image_band_duplication(cv2, fig) is None


class TestDetectIntraImageCopyMove:
    def _seed(self, db, tmp_path, monkeypatch, image, paper_id="p-cm"):
        monkeypatch.setattr(
            "mock_api.experiment_audit.figure_reuse._get_uploads_dir",
            lambda: tmp_path,
        )
        fig_dir = tmp_path / "figures" / paper_id
        fig_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(fig_dir / "f1.png"), image)
        db.add(Paper(id=paper_id, title="Copy-move Test"))
        db.add(
            PaperFigure(
                paper_id=paper_id,
                page=1,
                figure_index=0,
                figure_path="f1.png",
                figure_number=1,
            )
        )
        db.commit()
        return paper_id

    def test_cloned_patch_produces_finding(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch, _clone_image())
        findings = figure_reuse.detect_intra_image_copy_move(db_session, "p-cm")
        cm = [f for f in findings if "copy-move" in f["title"]]
        assert len(cm) == 1
        f = cm[0]
        assert f["type"] == "IMAGE_TAMPERING_CANDIDATE"
        assert f["severity"] == "high"
        assert f["needs_human_review"] is True
        assert f["page"] == 1
        assert f["bbox"] is not None

    def test_cloned_band_produces_finding(self, db_session, tmp_path, monkeypatch):
        fig = np.full((200, 200), 255, np.uint8)
        fig[30:50, 40:160] = _band(1)
        fig[120:140, 40:160] = _band(1)  # 克隆
        self._seed(db_session, tmp_path, monkeypatch, fig, paper_id="p-band")
        findings = figure_reuse.detect_intra_image_copy_move(db_session, "p-band")
        band_findings = [f for f in findings if "条带" in f["title"]]
        assert len(band_findings) == 1
        assert band_findings[0]["type"] == "IMAGE_TAMPERING_CANDIDATE"

    def test_clean_figure_no_finding(self, db_session, tmp_path, monkeypatch):
        clean = _texture(3, 200)
        self._seed(db_session, tmp_path, monkeypatch, clean, paper_id="p-clean")
        assert figure_reuse.detect_intra_image_copy_move(db_session, "p-clean") == []

    def test_no_figures_returns_empty(self, db_session):
        db_session.add(Paper(id="p-none", title="No figs"))
        db_session.commit()
        assert figure_reuse.detect_intra_image_copy_move(db_session, "p-none") == []

    def test_missing_file_fail_open(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "mock_api.experiment_audit.figure_reuse._get_uploads_dir",
            lambda: tmp_path,
        )
        db_session.add(Paper(id="p-gone", title="Gone"))
        db_session.add(
            PaperFigure(
                paper_id="p-gone", page=1, figure_index=0, figure_path="gone.png"
            )
        )
        db_session.commit()
        assert figure_reuse.detect_intra_image_copy_move(db_session, "p-gone") == []
