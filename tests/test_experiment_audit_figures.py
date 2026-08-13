"""实验审计 P0-4 图表坐标轴审计单测（figures.py）。

- axis_info 截断判定（纯数据，无依赖）
- OpenCV 断轴 / 子图尺度检测（合成图像；cv2 缺失时跳过）
- check_figure_axis_risks DB 集成（VLM 路径不触网：vision_http_url=None）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from mock_api.experiment_audit import figures
from mock_api.models import Paper, PaperFigure

cv2 = pytest.importorskip("cv2")


# ---------------------------------------------------------------------------
# axis_info 截断判定
# ---------------------------------------------------------------------------
class TestTruncatedYAxis:
    def test_truncated(self):
        r = figures.check_truncated_y_axis({"y_ticks": [90, 92, 94, 96]})
        assert r is not None
        assert r["risk"] == "truncated_y_axis"
        assert r["y_min"] == 90

    def test_starts_from_zero(self):
        assert figures.check_truncated_y_axis({"y_ticks": [0, 25, 50, 100]}) is None

    def test_no_ticks(self):
        assert figures.check_truncated_y_axis({"y_ticks": []}) is None
        assert figures.check_truncated_y_axis(None) is None


# ---------------------------------------------------------------------------
# OpenCV 确定性检测（合成图像）
# ---------------------------------------------------------------------------
def _save(tmp_path, img, name="fig.png") -> str:
    p = tmp_path / name
    cv2.imwrite(str(p), img)
    return str(p)


class TestBrokenAxis:
    def test_broken_axis_detected(self, tmp_path):
        # 白底，左缘两段分离的垂直线（中间大间隙）→ 断轴
        img = np.full((400, 600), 255, dtype=np.uint8)
        cv2.line(img, (40, 30), (40, 160), 0, 2)
        cv2.line(img, (40, 240), (40, 370), 0, 2)
        r = figures.detect_axis_line_gaps(_save(tmp_path, img))
        assert r is not None
        assert r["risk"] == "broken_axis"
        assert r["segments"] >= 2

    def test_continuous_axis_not_flagged(self, tmp_path):
        img = np.full((400, 600), 255, dtype=np.uint8)
        cv2.line(img, (40, 30), (40, 370), 0, 2)
        assert figures.detect_axis_line_gaps(_save(tmp_path, img)) is None

    def test_missing_file_fail_open(self):
        assert figures.detect_axis_line_gaps("no/such/file.png") is None


class TestPanelScale:
    def test_inconsistent_panels_flagged(self, tmp_path):
        img = np.full((400, 800), 255, dtype=np.uint8)
        cv2.rectangle(img, (30, 50), (380, 350), 0, 2)  # 高 300
        cv2.rectangle(img, (420, 150), (770, 350), 0, 2)  # 高 200（差 33%）
        r = figures.detect_panel_scale_inconsistency(_save(tmp_path, img))
        assert r is not None
        assert r["risk"] == "panel_scale_inconsistency"

    def test_consistent_panels_not_flagged(self, tmp_path):
        img = np.full((400, 800), 255, dtype=np.uint8)
        cv2.rectangle(img, (30, 50), (380, 350), 0, 2)
        cv2.rectangle(img, (420, 50), (770, 350), 0, 2)
        assert figures.detect_panel_scale_inconsistency(_save(tmp_path, img)) is None


# ---------------------------------------------------------------------------
# DB 集成（VLM 不触网：settings.vision_http_url 默认 None）
# ---------------------------------------------------------------------------
class TestCheckFigureAxisRisks:
    def _seed(self, db, axis_info=None):
        db.add(Paper(id="p-fig", title="Figure Audit"))
        db.add(
            PaperFigure(
                paper_id="p-fig",
                page=5,
                figure_index=0,
                figure_path="figures/p-fig/f0.png",
                figure_number=3,
                axis_info=axis_info,
                caption_text="Accuracy comparison",
            )
        )
        db.commit()

    def test_truncated_axis_info_produces_finding(self, db_session):
        self._seed(db_session, axis_info={"y_ticks": [90, 92, 94], "x_ticks": [1, 2]})
        findings = figures.check_figure_axis_risks(db_session, "p-fig")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "CHART_AXIS_RISK"
        assert f["severity"] == "low"
        assert f["page"] == 5
        assert any(e.get("figure_id") == "Figure 3" for e in f["evidence_sources"])

    def test_zero_based_axis_no_finding(self, db_session):
        self._seed(db_session, axis_info={"y_ticks": [0, 50, 100]})
        assert figures.check_figure_axis_risks(db_session, "p-fig") == []

    def test_no_figures_returns_empty(self, db_session):
        assert figures.check_figure_axis_risks(db_session, "no-figures") == []

    def test_vlm_unavailable_no_semantic_finding(self, db_session):
        # axis_info=None 触发 VLM 兜底路径，但 vision_http_url 未配置 → 空
        self._seed(db_session, axis_info=None)
        assert figures.check_figure_axis_risks(db_session, "p-fig") == []

    def test_broken_axis_annotates_evidence(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(figures, "_get_uploads_dir", lambda: tmp_path)
        fig_dir = tmp_path / "figures" / "p-fig"
        fig_dir.mkdir(parents=True, exist_ok=True)
        img = np.full((400, 600), 255, dtype=np.uint8)
        cv2.line(img, (40, 30), (40, 160), 0, 2)
        cv2.line(img, (40, 240), (40, 370), 0, 2)
        cv2.imwrite(str(fig_dir / "f0.png"), img)

        self._seed(db_session, axis_info=None)
        findings = figures.check_figure_axis_risks(db_session, "p-fig")
        cv_findings = [f for f in findings if "broken_axis" in f["title"]]
        assert cv_findings
        snippets = [e.get("snippet") or "" for e in cv_findings[0]["evidence_sources"]]
        assert any("_audit" in s and s.endswith(".png") for s in snippets)
        # 标注证据图已落盘
        annotated = [s for s in snippets if "_audit" in s]
        assert annotated and Path(annotated[0]).exists()
