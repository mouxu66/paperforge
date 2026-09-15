"""实验审计 P0-7 数据泄漏 + P0-9 曲线复用单测。

- data_leakage：精确碰撞 / pHash 近似 / 目录校验 / 无重复干净通过
- figure_reuse：同一图两份 → SIFT/RANSAC 验证通过；无关图 → 无 Finding
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from mock_api.experiment_audit import data_leakage, figure_reuse
from mock_api.models import Paper, PaperFigure

cv2 = pytest.importorskip("cv2")
imagehash = pytest.importorskip("imagehash")
PIL_Image = pytest.importorskip("PIL.Image")


def _texture_array(seed: int, size: int = 128) -> np.ndarray:
    """生成确定性随机纹理图（保证 SIFT 有特征点）。"""
    rng = np.random.RandomState(seed)
    img = rng.randint(0, 255, (size, size), dtype=np.uint8)
    # 加粗结构，增加角点
    for i in range(0, size, 16):
        img[i : i + 4, :] = 0
        img[:, i : i + 4] = 0
    return img


def _write_image(path, seed: int, size: int = 128):
    """生成确定性随机纹理图（保证 SIFT 有特征点）。"""
    cv2.imwrite(str(path), _texture_array(seed, size))


def _write_composite_with_panel(
    path,
    base_seed: int,
    panel_seed: int,
    *,
    size: int = 500,
    panel_size: int = 150,
    y: int,
    x: int,
):
    """写一张「base 纹理 + 一块独特子面板」的合成图。

    子面板用与 base 不同的 seed 生成，代表可被论文工厂像素级复制到另一张
    合成图里的单个 panel（如 GAPDH 内参条带）。
    """
    img = _texture_array(base_seed, size)
    panel = _texture_array(panel_seed, panel_size)
    img[y : y + panel_size, x : x + panel_size] = panel
    cv2.imwrite(str(path), img)


def _band_array(seed: int, w: int = 80, h: int = 12) -> np.ndarray:
    """生成一条带「条形码」纹理的 western-blot 式暗带（保证 NCC 有区分度）。

    每条带是单块暗色矩形，但每列强度由 seed 决定，因此同一 seed 缩放后 NCC 高、
    不同 seed 的条带 NCC 低（实测 0.974 vs 0.039）。
    """
    rng = np.random.RandomState(seed)
    band = np.full((h, w), 245, dtype=np.uint8)
    cols = rng.randint(10, 120, w)
    for x in range(w):
        band[:, x] = cols[x]
    band = cv2.GaussianBlur(band, (3, 3), 0)
    return band


def _write_blot_with_band(path, band, *, y: int, x: int, fig_size: int = 400):
    """写一张白底合成免疫印迹图，在 (y, x) 贴一条 band。"""
    fig = np.full((fig_size, fig_size), 255, dtype=np.uint8)
    bh, bw = band.shape
    fig[y : y + bh, x : x + bw] = band
    cv2.imwrite(str(path), fig)


def _reverse_band_array(seed: int, w: int = 80, h: int = 12) -> np.ndarray:
    """生成一条反相「白带」：高亮条形码纹理（值 150–255），贴黑底。"""
    rng = np.random.RandomState(seed)
    band = np.full((h, w), 245, dtype=np.uint8)
    cols = rng.randint(150, 255, w)
    for x in range(w):
        band[:, x] = cols[x]
    band = cv2.GaussianBlur(band, (3, 3), 0)
    return band


def _write_reverse_blot_with_band(path, band, *, y: int, x: int, fig_size: int = 400):
    """写一张黑底反相免疫印迹图，在 (y, x) 贴一条白带。"""
    fig = np.zeros((fig_size, fig_size), dtype=np.uint8)
    bh, bw = band.shape
    fig[y : y + bh, x : x + bw] = band
    cv2.imwrite(str(path), fig)


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

    def test_exact_hash_false_skips_sha_only_phash(self, tmp_path):
        """exact_hash=False：SHA-256 分支关闭（不报 high），pHash 仍召回相同文件为 medium。"""
        train, test = tmp_path / "train", tmp_path / "test"
        train.mkdir()
        test.mkdir()
        _write_image(train / "a.png", seed=1)
        shutil.copy(train / "a.png", test / "a_copy.png")
        findings = data_leakage.check_data_leakage(str(train), str(test), exact_hash=False)
        assert all(f["severity"] != "high" for f in findings), "SHA-256 分支应关闭"
        mediums = [f for f in findings if f["severity"] == "medium"]
        assert len(mediums) == 1, "完全相同文件应被 pHash 召回（dist=0）"


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


# ---------------------------------------------------------------------------
# 跨论文图片复用召回（pHash 粗筛）
# ---------------------------------------------------------------------------
class TestCrossPaperReuse:
    def test_same_image_across_papers_detected(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        shutil.copy(pa / "a1.png", pb / "b1.png")  # 跨论文同图
        cands = figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert len(cands) == 1
        assert cands[0]["paper_a"] != cands[0]["paper_b"]
        assert cands[0]["dist"] == 0

    def test_unrelated_figures_clean(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=17)
        _write_image(pb / "b1.png", seed=991)
        assert figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        ) == []

    def test_same_paper_pairs_excluded(self, tmp_path):
        pa = tmp_path / "paperA"
        pa.mkdir()
        _write_image(pa / "a1.png", seed=9)
        shutil.copy(pa / "a1.png", pa / "a2.png")  # 论文内复用
        # 论文内复用不归跨论文召回管
        assert figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png"), str(pa / "a2.png")]}
        ) == []


# ---------------------------------------------------------------------------
# 跨论文 SIFT 验证阈值实测标定（2026-08-14，Berberine vs KJPP 48 对）
# ---------------------------------------------------------------------------
class TestCrossPaperSiftCalibration:
    """SIFT 验证阈值标定回归：挡住「相似风格图表」低量级匹配的假阳性。

    实测签名：
    - 假阳性（相似风格但非复用）：inliers ≤127、ratio ≤0.50
    - 真复用（同图 ± 轻微压缩/缩放）：ratio ≥0.90
    标定阈值：inliers ≥ DEFAULT_MIN_RANSAC_INLIERS 且 ratio ≥ DEFAULT_MIN_INLIER_RATIO(0.7)。
    """

    def test_geometric_consistency_boundary(self):
        # 实测最差假阳性（127 inliers / 575 good = 0.22）→ 拒绝
        assert (
            figure_reuse._geometrically_consistent(
                127, 575, min_inliers=5, min_ratio=0.7
            )
            is False
        )
        # 实测真复用（缩放往返 5402 / 6022 = 0.897）→ 接受
        assert (
            figure_reuse._geometrically_consistent(
                5402, 6022, min_inliers=5, min_ratio=0.7
            )
            is True
        )
        # 绝对下限防退化：inliers 不足即拒绝（即使比例高）
        assert (
            figure_reuse._geometrically_consistent(
                4, 5, min_inliers=5, min_ratio=0.7
            )
            is False
        )

    def test_similar_style_figures_not_verified(self, tmp_path):
        """相似结构、不同纹理的两图，旧阈值（inliers≥5）会误报，标定后必须拒绝。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        _write_image(pb / "b1.png", seed=42)
        # 放宽 pHash 阈值强制进入 SIFT 验证（专测 SIFT 标定阈值，不受 pHash 门控干扰）
        cands = figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]},
            phash_threshold=100,
            verify_sift=True,
        )
        assert cands == []

    def test_genuine_reuse_verified(self, tmp_path):
        """跨论文同图（真复用）在标定阈值下仍应被验证通过。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        shutil.copy(pa / "a1.png", pb / "b1.png")
        cands = figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]},
            verify_sift=True,
        )
        assert len(cands) == 1
        assert cands[0]["sift_verified"] is True
        assert cands[0]["ransac_inliers"] > 0
        assert cands[0]["inlier_ratio"] >= 0.7

    def test_flip_aware_phashes_recall_flipped_pair(self, tmp_path):
        """翻转复用：召回阶段的翻转感知哈希应把翻转对召回（min 距离 < 阈值）。"""
        from PIL import Image

        p = tmp_path / "a.png"
        _write_image(p, seed=5)
        with Image.open(p) as im:
            orig = figure_reuse._flip_aware_phashes(im)
            flipped = figure_reuse._flip_aware_phashes(
                im.transpose(Image.FLIP_LEFT_RIGHT)
            )
        dist = figure_reuse._min_hash_distance(orig, flipped)
        assert dist < figure_reuse.DEFAULT_PHASH_RECALL_THRESHOLD

    def test_flipped_reuse_detected(self, tmp_path):
        """翻转复用：召回 + 验证两阶段翻转感知后，应被标定阈值捕获。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_image(pa / "a1.png", seed=5)
        img = cv2.imread(str(pa / "a1.png"), cv2.IMREAD_GRAYSCALE)
        cv2.imwrite(str(pb / "b1_flipped.png"), cv2.flip(img, 1))

        cands = figure_reuse.detect_cross_paper_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1_flipped.png")]},
            verify_sift=True,
        )
        assert len(cands) == 1
        assert cands[0]["sift_verified"] is True
        assert cands[0]["inlier_ratio"] >= 0.7

    def test_real_berberine_kjpp_no_false_positives(self):
        """真实案例基准：Berberine vs KJPP 48 对相似风格图，标定阈值下 0 假阳性。"""
        root = Path(__file__).resolve().parents[1]
        ber_dir = root / "uploads" / "figures" / "fraud_berberine"
        kjpp_dir = root / "uploads" / "figures" / "kjpp"
        ber_files = (
            sorted(
                p
                for p in ber_dir.glob("*")
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            )
            if ber_dir.exists()
            else []
        )
        kjpp_files = (
            sorted(
                p
                for p in kjpp_dir.glob("*")
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            )
            if kjpp_dir.exists()
            else []
        )
        if len(ber_files) < 2 or len(kjpp_files) < 2:
            pytest.skip("真实 Berberine/KJPP 图片不在，跳过真实案例基准")

        cands = figure_reuse.detect_cross_paper_reuse(
            {
                "berberine": [str(p) for p in ber_files],
                "kjpp": [str(p) for p in kjpp_files],
            },
            phash_threshold=100,  # 强制全部 48 对进入 SIFT，专测验证阈值
            verify_sift=True,
        )
        assert cands == [], f"标定阈值下出现 {len(cands)} 条假阳性"


# ---------------------------------------------------------------------------
# 跨论文子面板（单 panel）复用检测：局部空间 inlier 密度
# ---------------------------------------------------------------------------
class TestCrossPaperSubpanelReuse:
    """合成图里被复用的单个子面板（如 GAPDH 内参条带）。

    整图 pHash 因布局不同分不开、整图 RANSAC inlier 比例被稀释，此类复用只能靠
    「局部空间内高密度 inlier 簇」捕获。合成正例：同一块子面板像素级复制进两张
    布局不同的合成图；反例：不同纹理、无共享面板的相似风格图。
    """

    def test_subpanel_copy_detected(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        # 同一块子面板（panel_seed=777）像素级复制到两张布局不同的合成图
        _write_composite_with_panel(
            pa / "a1.png", base_seed=1, panel_seed=777, y=340, x=340
        )
        _write_composite_with_panel(
            pb / "b1.png", base_seed=2, panel_seed=777, y=10, x=10
        )
        cands = figure_reuse.detect_cross_paper_subpanel_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert len(cands) == 1
        c = cands[0]
        assert c["subpanel_verified"] is True
        assert c["local_inliers"] >= figure_reuse.DEFAULT_MIN_LOCAL_INLIERS
        assert c["paper_a"] != c["paper_b"]

    def test_similar_style_no_subpanel_false_positive(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_composite_with_panel(
            pa / "a1.png", base_seed=1, panel_seed=777, y=340, x=340
        )
        _write_image(pb / "b1.png", seed=3, size=500)  # 不同纹理、无共享面板
        assert figure_reuse.detect_cross_paper_subpanel_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        ) == []

    def test_same_paper_pairs_excluded(self, tmp_path):
        pa = tmp_path / "paperA"
        pa.mkdir()
        _write_composite_with_panel(
            pa / "a1.png", base_seed=1, panel_seed=777, y=340, x=340
        )
        _write_composite_with_panel(
            pa / "a2.png", base_seed=2, panel_seed=777, y=10, x=10
        )
        # 论文内子面板复用不归跨论文检测管
        assert figure_reuse.detect_cross_paper_subpanel_reuse(
            {"paperA": [str(pa / "a1.png"), str(pa / "a2.png")]}
        ) == []

    def test_real_berberine_kjpp_no_subpanel_false_positives(self):
        """真实案例基准：重画/相似风格图（非像素复制）不应被子面板检测误报。"""
        root = Path(__file__).resolve().parents[1]
        ber_dir = root / "uploads" / "figures" / "fraud_berberine"
        kjpp_dir = root / "uploads" / "figures" / "kjpp"
        ber_files = (
            [
                str(p)
                for p in sorted(ber_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if ber_dir.exists()
            else []
        )
        kjpp_files = (
            [
                str(p)
                for p in sorted(kjpp_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if kjpp_dir.exists()
            else []
        )
        if len(ber_files) < 2 or len(kjpp_files) < 2:
            pytest.skip("真实 Berberine/KJPP 图片不在，跳过真实案例基准")
        cands = figure_reuse.detect_cross_paper_subpanel_reuse(
            {"berberine": ber_files, "kjpp": kjpp_files}
        )
        assert cands == [], f"子面板检测出现 {len(cands)} 条假阳性"


# ---------------------------------------------------------------------------
# 跨论文条带（band）级复用检测：NCC 像素比对，覆盖 SIFT 抓不到的细条带
# ---------------------------------------------------------------------------
class TestCrossPaperBandReuse:
    """western-blot 条带级跨论文复用。

    SIFT 对细条带（~40×20px）特征点不足，子面板检测抓不到；改走条带提取 +
    NCC 像素比对。合成正例：同一条形码条带缩放后贴进两张图；反例：不同纹理条带。
    """

    def test_band_copy_detected(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        band = _band_array(seed=42)
        _write_blot_with_band(pa / "a1.png", band, y=100, x=50)
        resized = cv2.resize(band, (60, 10), interpolation=cv2.INTER_AREA)
        _write_blot_with_band(pb / "b1.png", resized, y=200, x=150)
        cands = figure_reuse.detect_cross_paper_band_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert len(cands) == 1
        assert cands[0]["ncc"] >= figure_reuse.DEFAULT_BAND_NCC_THRESHOLD
        assert cands[0]["paper_a"] != cands[0]["paper_b"]

    def test_unrelated_bands_not_detected(self, tmp_path):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        _write_blot_with_band(pa / "a1.png", _band_array(seed=42), y=100, x=50)
        _write_blot_with_band(pb / "b1.png", _band_array(seed=999), y=80, x=120)
        assert figure_reuse.detect_cross_paper_band_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        ) == []

    def test_same_paper_bands_excluded(self, tmp_path):
        pa = tmp_path / "paperA"
        pa.mkdir()
        band = _band_array(seed=42)
        _write_blot_with_band(pa / "a1.png", band, y=100, x=50)
        _write_blot_with_band(pa / "a2.png", band, y=200, x=150)
        # 论文内条带复用不归跨论文检测管
        assert figure_reuse.detect_cross_paper_band_reuse(
            {"paperA": [str(pa / "a1.png"), str(pa / "a2.png")]}
        ) == []

    def test_real_berberine_kjpp_ampk_gapdh_reuse_detected(self):
        """真实案例基准：撤稿通知指认的 AMPK(Fig-2A)↔GAPDH(Fig-6B) 条带复用应被检出。"""
        root = Path(__file__).resolve().parents[1]
        ber_dir = root / "uploads" / "figures" / "fraud_berberine"
        kjpp_dir = root / "uploads" / "figures" / "kjpp"
        ber_files = (
            [
                str(p)
                for p in sorted(ber_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if ber_dir.exists()
            else []
        )
        kjpp_files = (
            [
                str(p)
                for p in sorted(kjpp_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if kjpp_dir.exists()
            else []
        )
        if len(ber_files) < 2 or len(kjpp_files) < 2:
            pytest.skip("真实 Berberine/KJPP 图片不在，跳过真实案例基准")
        cands = figure_reuse.detect_cross_paper_band_reuse(
            {"berberine": ber_files, "kjpp": kjpp_files}
        )
        # 至少命中 p3_i0(Fig-2A AMPK) 与 g006(Fig-6B GAPDH) 那条（任意方向）
        hits = [
            c
            for c in cands
            if c["ncc"] >= figure_reuse.DEFAULT_BAND_NCC_THRESHOLD
            and any("p3_i0" in Path(n).name for n in (c["fig_a"], c["fig_b"]))
            and any("g006" in Path(n).name for n in (c["fig_a"], c["fig_b"]))
        ]
        assert hits, f"未检出 AMPK↔GAPDH 条带复用（共 {len(cands)} 条候选）"


# ---------------------------------------------------------------------------
# 反相（白带黑底）条带提取：条带提取同时覆盖暗带白底与反相两种方向
# ---------------------------------------------------------------------------
class TestReverseBandDetection:
    """_detect_band_regions 需同时提取「暗带白底」与「反相白带黑底」。"""

    def test_reverse_band_regions_extracted(self, tmp_path):
        p = tmp_path / "rev.png"
        _write_reverse_blot_with_band(p, _reverse_band_array(seed=7), y=100, x=50)
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        regions = figure_reuse._detect_band_regions(cv2, img)
        assert len(regions) >= 1, "反相白带应被提取为条带区域"
        x, y, w, h = regions[0]
        assert w > h  # 横向条带

    def test_normal_dark_band_still_extracted(self, tmp_path):
        """非反相（暗带白底）不回归：仍应提取到暗带。"""
        p = tmp_path / "dark.png"
        _write_blot_with_band(p, _band_array(seed=42), y=100, x=50)
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        regions = figure_reuse._detect_band_regions(cv2, img)
        assert len(regions) >= 1

    def test_reverse_band_copy_detected(self, tmp_path):
        """同一反相白带贴进两张图 → 跨论文条带复用应被检出。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        band = _reverse_band_array(seed=42)
        _write_reverse_blot_with_band(pa / "a1.png", band, y=100, x=50)
        resized = cv2.resize(band, (60, 10), interpolation=cv2.INTER_AREA)
        _write_reverse_blot_with_band(pb / "b1.png", resized, y=200, x=150)
        cands = figure_reuse.detect_cross_paper_band_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert len(cands) == 1
        assert cands[0]["ncc"] >= figure_reuse.DEFAULT_BAND_NCC_THRESHOLD
        assert cands[0]["paper_a"] != cands[0]["paper_b"]


# ---------------------------------------------------------------------------
# 改标图片复用证据链：NCC 像素复用 + VLM 语义标签（目标蛋白）比对
# ---------------------------------------------------------------------------
def _fake_proteins(mapping: dict) -> callable:
    """返回一个按文件名返回目标蛋白集合的假 _extract_target_proteins。"""

    def fn(image_path, timeout=120):
        return set(mapping.get(Path(image_path).name, []))

    return fn


class TestRelabeledImageReuse:
    """RELABELED_IMAGE_REUSE：同一张条带像素复用，但两处标注的目标蛋白不同。"""

    def test_relabeled_reuse_detected(self, tmp_path, monkeypatch):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        band = _band_array(seed=42)
        _write_blot_with_band(pa / "a1.png", band, y=100, x=50)
        resized = cv2.resize(band, (60, 10), interpolation=cv2.INTER_AREA)
        _write_blot_with_band(pb / "b1.png", resized, y=200, x=150)
        monkeypatch.setattr(
            figure_reuse,
            "_extract_target_proteins",
            _fake_proteins({"a1.png": ["ampk"], "b1.png": ["gapdh"]}),
        )
        findings = figure_reuse.detect_relabeled_image_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "RELABELED_IMAGE_REUSE"
        assert f["severity"] == "high"
        assert f["needs_human_review"] is True
        assert "ampk" in f["claim"] and "gapdh" in f["claim"]
        # 图标识必须带论文名（basename 在副本论文目录间会冲突，CSV/报告不可区分）
        assert "paperA/a1.png" in f["title"]
        assert "paperB/b1.png" in f["title"]
        assert f["evidence_sources"][0]["figure_id"] == "paperA/a1.png"

    def test_same_label_no_relabeled_finding(self, tmp_path, monkeypatch):
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        band = _band_array(seed=42)
        _write_blot_with_band(pa / "a1.png", band, y=100, x=50)
        resized = cv2.resize(band, (60, 10), interpolation=cv2.INTER_AREA)
        _write_blot_with_band(pb / "b1.png", resized, y=200, x=150)
        # 同一蛋白（标签一致）→ 只是复用，不判「改标」
        monkeypatch.setattr(
            figure_reuse,
            "_extract_target_proteins",
            _fake_proteins({"a1.png": ["ampk"], "b1.png": ["ampk", "p-ampk"]}),
        )
        findings = figure_reuse.detect_relabeled_image_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert findings == []

    def test_missing_labels_fail_open(self, tmp_path, monkeypatch):
        """任一侧 VLM 提取不到蛋白 → 缺标签不一致证据，不出「改标」结论。"""
        pa, pb = tmp_path / "paperA", tmp_path / "paperB"
        pa.mkdir()
        pb.mkdir()
        band = _band_array(seed=42)
        _write_blot_with_band(pa / "a1.png", band, y=100, x=50)
        _write_blot_with_band(pb / "b1.png", band, y=200, x=150)
        monkeypatch.setattr(
            figure_reuse,
            "_extract_target_proteins",
            _fake_proteins({"a1.png": ["ampk"], "b1.png": []}),
        )
        findings = figure_reuse.detect_relabeled_image_reuse(
            {"paperA": [str(pa / "a1.png")], "paperB": [str(pb / "b1.png")]}
        )
        assert findings == []

    def test_real_berberine_kjpp_relabeled_reuse(self, monkeypatch):
        """真实案例基准：AMPK(Fig-2A p3_i0)↔GAPDH(Fig-6B g006) 改标复用应被检出。

        NCC 像素层走真实文件（确定性）；VLM 标签用实测输出的蛋白集合（离线可复现），
        专测「像素复用 + 标签不一致」的合并判定逻辑。
        """
        root = Path(__file__).resolve().parents[1]
        ber_dir = root / "uploads" / "figures" / "fraud_berberine"
        kjpp_dir = root / "uploads" / "figures" / "kjpp"
        ber_files = (
            [
                str(p)
                for p in sorted(ber_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if ber_dir.exists()
            else []
        )
        kjpp_files = (
            [
                str(p)
                for p in sorted(kjpp_dir.glob("*"))
                if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
            if kjpp_dir.exists()
            else []
        )
        if len(ber_files) < 2 or len(kjpp_files) < 2:
            pytest.skip("真实 Berberine/KJPP 图片不在，跳过真实案例基准")
        monkeypatch.setattr(
            figure_reuse,
            "_extract_target_proteins",
            _fake_proteins(
                {
                    "p3_i0.png": ["ampk", "lkb1", "p-ampk"],
                    "kjpp-20-325-g006.jpg": ["gapdh", "p47phox", "p67phox"],
                }
            ),
        )
        findings = figure_reuse.detect_relabeled_image_reuse(
            {"berberine": ber_files, "kjpp": kjpp_files}, max_pairs=20
        )
        hits = [
            f
            for f in findings
            if "p3_i0" in f["title"] and "g006" in f["title"]
        ]
        assert hits, f"未检出 AMPK↔GAPDH 改标复用（共 {len(findings)} 条）"


# ---------------------------------------------------------------------------
# T1 主链路：单篇 run_paper_audit 的跨论文图片复用（pHash 索引召回 + SIFT 验证）
# ---------------------------------------------------------------------------
_FULL_TEXT = (
    "We propose a novel framework for image classification. Our model uses a "
    "hierarchical feature extractor combined with a lightweight attention module. "
    "We train on the ImageNet dataset with batch size 32 for 50 epochs using the "
    "Adam optimizer with an initial learning rate of 3e-4 and cosine decay. "
    "Extensive experiments show that our method achieves 95.2 percent accuracy on "
    "the benchmark, outperforming the strongest baseline by 3.1 percentage points. "
    "Ablation studies demonstrate that each proposed module contributes positively "
    "to the final performance and the results are stable across random seeds."
)


class TestCrossPaperReuseInCorpus:
    """detect_cross_paper_reuse_in_corpus（P0-9_cross_paper_reuse 主链路入口）。"""

    def _seed(
        self,
        db,
        tmp_path,
        monkeypatch,
        *,
        same_figure: bool,
        papers: tuple[str, str] = ("pa", "pb"),
        write_files: bool = True,
    ):
        monkeypatch.setattr(
            "mock_api.experiment_audit.figure_reuse._get_uploads_dir",
            lambda: tmp_path,
        )
        for pid in papers:
            db.add(Paper(id=pid, title=f"Paper {pid}", full_text=_FULL_TEXT))
            fig_dir = tmp_path / "figures" / pid
            fig_dir.mkdir(parents=True, exist_ok=True)
            db.add(
                PaperFigure(
                    paper_id=pid, page=1, figure_index=0, figure_path="f1.png"
                )
            )
        if write_files:
            _write_image(tmp_path / "figures" / papers[0] / "f1.png", seed=42)
            if len(papers) > 1:
                if same_figure:
                    shutil.copy(
                        tmp_path / "figures" / papers[0] / "f1.png",
                        tmp_path / "figures" / papers[1] / "f1.png",
                    )
                else:
                    _write_image(
                        tmp_path / "figures" / papers[1] / "f1.png", seed=991
                    )
        db.commit()

    def test_same_image_across_papers_detected(
        self, db_session, tmp_path, monkeypatch
    ):
        self._seed(db_session, tmp_path, monkeypatch, same_figure=True)
        findings = figure_reuse.detect_cross_paper_reuse_in_corpus(
            db_session, "pa"
        )
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "FIGURE_REUSE_CANDIDATE"
        assert f["needs_human_review"] is True
        assert f["normal_explanation"]
        assert "other_paper_id=pb" in f["computed"]
        assert f["evidence_sources"]

    def test_unrelated_figures_clean(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch, same_figure=False)
        assert (
            figure_reuse.detect_cross_paper_reuse_in_corpus(db_session, "pa") == []
        )

    def test_no_other_papers_empty(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch, same_figure=True, papers=("pa",))
        assert (
            figure_reuse.detect_cross_paper_reuse_in_corpus(db_session, "pa") == []
        )

    def test_missing_files_fail_open(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch, same_figure=True, write_files=False)
        assert (
            figure_reuse.detect_cross_paper_reuse_in_corpus(db_session, "pa") == []
        )

    def test_run_paper_audit_records_cross_paper_check(
        self, db_session, tmp_path, monkeypatch
    ):
        """run_paper_audit 主链路接线：checks_run 含 P0-9_cross_paper_reuse。"""
        from mock_api.experiment_audit.service import AuditService

        self._seed(db_session, tmp_path, monkeypatch, same_figure=True)
        audit = AuditService().run_paper_audit(db_session, "pa")
        by_check = {c["check"]: c for c in audit.checks_run}
        assert "P0-9_cross_paper_reuse" in by_check
        assert by_check["P0-9_cross_paper_reuse"]["status"] in ("ok", "skipped")
        types = {f["type"] for f in audit.findings}
        assert "FIGURE_REUSE_CANDIDATE" in types
