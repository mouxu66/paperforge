"""evidence.py（双引擎交叉验证 + 证据图标注）单测。"""
from __future__ import annotations

import numpy as np
import pytest
from mock_api.experiment_audit.evidence import (
    annotate_axis_evidence,
    cross_validate_axis,
)

cv2 = pytest.importorskip("cv2")


def _write_image(path, size: int = 200):
    img = np.zeros((size, size), dtype=np.uint8)
    # 一条竖直轴线 + 一个矩形，供标注函数读入
    img[:, 30:34] = 255
    cv2.imwrite(str(path), img)
    return str(path)


class TestCrossValidateAxis:
    def test_conflict_when_vlm_contradicts_broken_axis(self):
        cv_risk = {"risk": "broken_axis", "segments": 2}
        out = cross_validate_axis(cv_risk, "y 轴从 0 开始，连续刻度")
        assert out is not None
        assert out["conflict"] is True
        assert out["trust"] == "opencv"

    def test_agree_when_vlm_no_contradiction(self):
        cv_risk = {"risk": "panel_scale_inconsistency"}
        out = cross_validate_axis(cv_risk, "图中包含三个子图")
        assert out is not None
        assert out["conflict"] is False
        assert out["trust"] == "opencv"

    def test_vlm_only_hint_without_cv(self):
        out = cross_validate_axis(None, "该图 y 轴截断，可能误导")
        assert out is not None
        assert out["trust"] == "vlm_only"

    def test_empty_inputs_return_none(self):
        assert cross_validate_axis(None, "") is None


class TestAnnotateAxisEvidence:
    def test_annotation_saved_and_readable(self, tmp_path):
        src = _write_image(tmp_path / "fig.png")
        out_path = str(tmp_path / "_audit" / "fig_broken_axis.png")
        risk = {"risk": "broken_axis", "bbox": [0, 0, 200, 200]}
        ret = annotate_axis_evidence(src, risk, out_path)
        assert ret == out_path
        assert (tmp_path / "_audit" / "fig_broken_axis.png").exists()
        # 标注后的图仍可读且尺寸一致
        img = cv2.imread(out_path)
        assert img is not None and img.shape[:2] == (200, 200)

    def test_none_risk_returns_none(self, tmp_path):
        src = _write_image(tmp_path / "fig.png")
        assert annotate_axis_evidence(src, None, str(tmp_path / "x.png")) is None

    def test_missing_source_returns_none(self, tmp_path):
        risk = {"risk": "broken_axis", "bbox": [0, 0, 10, 10]}
        assert (
            annotate_axis_evidence(
                str(tmp_path / "gone.png"), risk, str(tmp_path / "out.png")
            )
            is None
        )
