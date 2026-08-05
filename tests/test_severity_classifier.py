"""Tests for mock_api/severity_classifier.py and scripts/train_severity.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from mock_api.severity_classifier import (
    classify_claim_severity,
    clear_model_cache,
)


def test_classify_claim_severity_fallback_threshold() -> None:
    """无模型时，应使用固定 0.5 相对阈值。"""
    clear_model_cache()
    # 偏差 60% -> fatal
    assert classify_claim_severity(10.0, 0.0, 10.0) == "minor"  # value inside axis
    assert classify_claim_severity(16.0, 0.0, 10.0) == "fatal"  # 60% over
    assert classify_claim_severity(-4.0, 0.0, 10.0) == "minor"  # 40% under
    assert classify_claim_severity(15.0, 0.0, 10.0, 5.0, 12.0) == "fatal"  # 50% over


def test_classify_claim_severity_configurable_fallback_threshold(monkeypatch: Any) -> None:
    """fallback 阈值应可从配置读取。"""
    clear_model_cache()
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_FALLBACK_THRESHOLD", 0.3)
    # 偏差 40% -> fatal（阈值 0.3）
    assert classify_claim_severity(14.0, 0.0, 10.0) == "fatal"
    # 偏差 20% -> minor
    assert classify_claim_severity(12.0, 0.0, 10.0) == "minor"


def test_classify_claim_severity_disabled_classifier(tmp_path: Path, monkeypatch: Any) -> None:
    """当配置关闭分类器时，即使模型文件存在也应回退阈值。"""
    clear_model_cache()
    model_path = tmp_path / "severity_model.json"
    # 一个总是返回 fatal 的简单树
    model = {"class": "fatal"}
    model_path.write_text(json.dumps(model), encoding="utf-8")

    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_MODEL_PATH", str(model_path))

    assert classify_claim_severity(15.0, 0.0, 10.0) == "fatal"  # 0.5 threshold
    assert classify_claim_severity(12.0, 0.0, 10.0) == "minor"  # < 0.5 threshold


def test_classify_claim_severity_with_model_file(tmp_path: Path, monkeypatch: Any) -> None:
    """加载 JSON 模型文件后，应走决策树而非固定阈值。"""
    clear_model_cache()
    model_path = tmp_path / "severity_model.json"
    # 决策规则：relative_deviation > 0.3 则 fatal，否则 minor
    tree = {
        "feature": "relative_deviation",
        "threshold": 0.3,
        "left": {"class": "minor"},
        "right": {"class": "fatal"},
    }
    model_path.write_text(json.dumps(tree), encoding="utf-8")

    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_MODEL_PATH", str(model_path))

    assert classify_claim_severity(13.5, 0.0, 10.0) == "fatal"  # rel=0.35
    assert classify_claim_severity(13.0, 0.0, 10.0) == "minor"  # rel=0.3


def test_classify_claim_severity_wrapper_format(tmp_path: Path, monkeypatch: Any) -> None:
    """兼容 train_severity.py 生成的 wrapper 格式。"""
    clear_model_cache()
    model_path = tmp_path / "severity_model.json"
    tree = {
        "feature": "relative_deviation",
        "threshold": 0.2,
        "left": {"class": "minor"},
        "right": {"class": "fatal"},
    }
    wrapper = {"tree": tree, "metadata": {"n_samples": 42}, "feature_names": []}
    model_path.write_text(json.dumps(wrapper), encoding="utf-8")

    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_MODEL_PATH", str(model_path))

    assert classify_claim_severity(12.5, 0.0, 10.0) == "fatal"  # rel=0.25
    assert classify_claim_severity(11.0, 0.0, 10.0) == "minor"  # rel=0.1


# ---------------------------------------------------------------------------
# 训练脚本单元测试（无需 scikit-learn 的部分）
# ---------------------------------------------------------------------------

def test_extract_claim_from_content_parses_chinese() -> None:
    """训练脚本能从中文 evidence content 中解析 claim。"""
    from scripts.train_severity import _extract_claim_from_content

    content = "[FATAL] [图文一致性] 图3 (第2页): accuracy=1.8 超出坐标轴范围 [0.0, 1.0]"
    result = _extract_claim_from_content(content)
    assert result is not None
    assert result["value"] == 1.8
    assert result["axis_min"] == 0.0
    assert result["axis_max"] == 1.0
    assert result["curve_min"] is None
    assert result["metric"] == "accuracy"


def test_extract_claim_from_content_parses_english() -> None:
    """训练脚本应能解析英文 evidence content。"""
    from scripts.train_severity import _extract_claim_from_content

    content = (
        "[FATAL] [Figure consistency] Figure 3 (p2): accuracy=1.8 is outside "
        "axis range [0.0, 1.0]; curve_points y-range [0.05, 0.09]"
    )
    result = _extract_claim_from_content(content)
    assert result is not None
    assert result["value"] == 1.8
    assert result["axis_min"] == 0.0
    assert result["axis_max"] == 1.0
    assert result["curve_min"] == 0.05
    assert result["curve_max"] == 0.09
    assert result["metric"] == "accuracy"


def test_extract_claim_from_content_parses_curve_range() -> None:
    """训练脚本应能解析曲线 y 范围。"""
    from scripts.train_severity import _extract_claim_from_content

    content = (
        "[MINOR] [图文一致性] 图5 (第4页): loss=0.12 超出坐标轴范围 [0.0, 0.1]"
        "; 曲线点 y 范围 [0.05, 0.09]"
    )
    result = _extract_claim_from_content(content)
    assert result is not None
    assert result["value"] == 0.12
    assert result["curve_min"] == 0.05
    assert result["curve_max"] == 0.09


def test_decide_label_hybrid() -> None:
    """hybrid 标签策略应同时参考证据 severity 和终审判绝。"""
    from scripts.train_severity import _decide_label

    claim: dict[str, Any] = {"figure_index": 1, "metric": "accuracy"}
    assert _decide_label({"severity": "fatal"}, {"final_verdict": "major_revision"}, {}, claim, "hybrid") == "fatal"
    assert _decide_label({"severity": "minor"}, {"final_verdict": "reject"}, {}, claim, "hybrid") == "fatal"
    assert _decide_label({"severity": "minor"}, {"final_verdict": "accept"}, {}, claim, "hybrid") == "minor"


def test_compute_features() -> None:
    """特征计算应与 severity_classifier 保持一致，缺失 curve 时以 axis 范围填充。"""
    from scripts.train_severity import _compute_features

    claim = {
        "value": 1.8,
        "axis_min": 0.0,
        "axis_max": 1.0,
        "curve_min": None,
        "curve_max": None,
        "metric": "accuracy",
        "figure_index": 1,
    }
    features = _compute_features(claim)
    assert features is not None
    assert features["relative_deviation"] == pytest.approx(0.8)
    assert features["absolute_deviation"] == pytest.approx(0.8)
    assert features["claim_value"] == 1.8
    assert features["span"] == 1.0
    assert features["is_curve"] == 0
    # 缺失 curve 时，用 axis 范围填充
    assert features["curve_y_min"] == 0.0
    assert features["curve_y_max"] == 1.0


# ---------------------------------------------------------------------------
# 训练脚本集成测试（需要 scikit-learn）
# ---------------------------------------------------------------------------

def _sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _sklearn_available(), reason="scikit-learn not installed")
def test_train_produces_valid_tree(tmp_path: Path) -> None:
    """训练函数应生成 severity_classifier 可解析的树。"""
    import numpy as np

    from scripts.train_severity import FEATURE_NAMES, train

    X = []
    y = []
    for _ in range(20):
        # fatal-like samples
        X.append({
            "relative_deviation": 0.7,
            "absolute_deviation": 0.7,
            "claim_value": 2.0,
            "span": 1.0,
            "axis_min": 0.0,
            "axis_max": 1.0,
            "curve_y_min": 0.0,
            "curve_y_max": 1.0,
            "is_curve": 0,
        })
        y.append("fatal")
    for _ in range(20):
        # minor-like samples
        X.append({
            "relative_deviation": 0.2,
            "absolute_deviation": 0.2,
            "claim_value": 0.2,
            "span": 1.0,
            "axis_min": 0.0,
            "axis_max": 1.0,
            "curve_y_min": 0.0,
            "curve_y_max": 1.0,
            "is_curve": 0,
        })
        y.append("minor")

    tree, metadata = train(X, y, max_depth=2, min_samples_leaf=2)
    assert "feature" in tree or "class" in tree
    assert metadata["n_samples"] == 40
    assert "feature_importances" in metadata

    # 用生成的树做预测
    model_path = tmp_path / "model.json"
    model_path.write_text(json.dumps(tree), encoding="utf-8")

    clear_model_cache()
    import mock_api.severity_classifier as sc_mod

    original_model = sc_mod._SEVERITY_MODEL
    try:
        sc_mod._SEVERITY_MODEL = tree
        assert classify_claim_severity(2.0, 0.0, 1.0) == "fatal"
        assert classify_claim_severity(0.2, 0.0, 1.0) == "minor"
    finally:
        sc_mod._SEVERITY_MODEL = original_model


def test_classify_claim_severity_invalid_json(tmp_path: Path, monkeypatch: Any) -> None:
    """模型文件为非法 JSON 时，应能回退固定阈值而不抛异常。"""
    clear_model_cache()
    model_path = tmp_path / "severity_model.json"
    model_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr("mock_api.config.DEPTH_SEVERITY_MODEL_PATH", str(model_path))

    # 0.6 > 0.5 -> fatal
    assert classify_claim_severity(16.0, 0.0, 10.0) == "fatal"
    # 0.4 < 0.5 -> minor
    assert classify_claim_severity(14.0, 0.0, 10.0) == "minor"


@pytest.mark.skipif(not _sklearn_available(), reason="scikit-learn not installed")
def test_main_cli_with_mocked_dataset(tmp_path: Path, monkeypatch: Any) -> None:
    """模拟历史数据，验证 CLI 主流程能正常输出模型文件。"""
    from scripts.train_severity import main as train_main

    output = tmp_path / "severity_model.json"
    monkeypatch.setattr(
        "sys.argv",
        ["train_severity.py", "--output", str(output), "--min-samples", "4"],
    )

    X = []
    y = []
    for _ in range(10):
        X.append({
            "relative_deviation": 0.8,
            "absolute_deviation": 0.8,
            "claim_value": 1.8,
            "span": 1.0,
            "axis_min": 0.0,
            "axis_max": 1.0,
            "curve_y_min": 0.0,
            "curve_y_max": 1.0,
            "is_curve": 0,
        })
        y.append("fatal")
    for _ in range(10):
        X.append({
            "relative_deviation": 0.1,
            "absolute_deviation": 0.1,
            "claim_value": 0.1,
            "span": 1.0,
            "axis_min": 0.0,
            "axis_max": 1.0,
            "curve_y_min": 0.0,
            "curve_y_max": 1.0,
            "is_curve": 0,
        })
        y.append("minor")

    monkeypatch.setattr("scripts.train_severity._build_dataset", lambda **kwargs: (X, y))

    result = train_main()
    assert result == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "tree" in payload
    assert "metadata" in payload
    assert payload["metadata"]["n_samples"] == 20
