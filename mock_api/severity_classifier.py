"""可训练 severity 分类器。

设计目标：
- 训练阶段使用 scikit-learn（离线），导出树规则到 JSON。
- 运行时不依赖 scikit-learn，仅解析 JSON 树。
- 若模型文件不存在或解析失败，回退到固定 0.5 阈值。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "severity_model.json"
)

_SEVERITY_MODEL: dict[str, Any] | None = None
_MODEL_MTIME: float = 0.0
_MODEL_PATH: str | None = None


def _validate_tree_node(node: Any) -> bool:
    """简单校验节点结构。

    叶子节点必须只含 class；内部节点必须含 feature/threshold/left/right。
    """
    if not isinstance(node, dict):
        return False
    keys = set(node.keys())
    if "class" in keys:
        return keys == {"class"}
    required = {"feature", "threshold", "left", "right"}
    if not required.issubset(keys):
        return False
    return _validate_tree_node(node["left"]) and _validate_tree_node(node["right"])


def _load_severity_model(path: str | None = None) -> dict[str, Any] | None:
    """从 JSON 文件加载训练好的 severity 决策树。

    可通过配置开关强制禁用：关闭时总是返回 None，从而回退到固定 0.5 阈值。
    支持按文件 mtime 自动热重载。
    """
    from . import config

    if not config.DEPTH_SEVERITY_CLASSIFIER_ENABLED:
        logger.debug("[severity] classifier disabled by config, fallback to 0.5 threshold")
        return None

    path = path or config.DEPTH_SEVERITY_MODEL_PATH or DEFAULT_MODEL_PATH
    if not path:
        return None
    path = os.path.abspath(path)
    if not os.path.exists(path):
        logger.info("[severity] model file not found at %s, fallback to 0.5 threshold", path)
        return None

    global _SEVERITY_MODEL, _MODEL_MTIME, _MODEL_PATH
    try:
        current_mtime = os.path.getmtime(path)
    except OSError as exc:
        logger.warning("[severity] failed to stat model file %s: %s", path, exc)
        return None

    if _SEVERITY_MODEL is not None and path == _MODEL_PATH and current_mtime == _MODEL_MTIME:
        return _SEVERITY_MODEL

    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        # 兼容新训练脚本生成的 wrapper 格式 {"tree": ..., "metadata": ...}
        if isinstance(loaded, dict) and "tree" in loaded:
            model = loaded["tree"]
        else:
            model = loaded
        if not _validate_tree_node(model):
            logger.warning("[severity] model file %s is not a valid decision tree", path)
            _SEVERITY_MODEL = None
            _MODEL_MTIME = current_mtime
            _MODEL_PATH = path
            return None
        _SEVERITY_MODEL = model
        _MODEL_MTIME = current_mtime
        _MODEL_PATH = path
        logger.info("[severity] loaded model from %s", path)
        return _SEVERITY_MODEL
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[severity] failed to load model from %s: %s", path, exc)
        return None


def _evaluate_tree(node: dict[str, Any], features: dict[str, Any]) -> str:
    """递归评估决策树节点。"""
    if "class" in node:
        return str(node["class"])

    feature = node.get("feature")
    threshold = node.get("threshold")
    value = features.get(feature)

    # 缺失特征或无法比较时，走左分支（minor 方向）
    try:
        if value is not None and float(value) <= float(threshold):
            return _evaluate_tree(node["left"], features)
        return _evaluate_tree(node["right"], features)
    except (TypeError, ValueError):
        return _evaluate_tree(node["left"], features)


def classify_claim_severity(
    value: Any,
    axis_min: float | None,
    axis_max: float | None,
    curve_y_min: float | None = None,
    curve_y_max: float | None = None,
    **extra: Any,
) -> str:
    """基于训练好的决策树对越界 claim 进行 severity 分类。

    若模型不存在或无效，回退到固定 0.5 相对阈值规则。
    """
    # 基础特征计算
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return "minor"

    min_bound = axis_min if axis_min is not None else curve_y_min
    max_bound = axis_max if axis_max is not None else curve_y_max
    if min_bound is None or max_bound is None:
        return "minor"

    min_bound = float(min_bound)
    max_bound = float(max_bound)
    span = max_bound - min_bound
    if span <= 0:
        return "minor"

    if value_f < min_bound:
        deviation = (min_bound - value_f) / span
        absolute_deviation = min_bound - value_f
    elif value_f > max_bound:
        deviation = (value_f - max_bound) / span
        absolute_deviation = value_f - max_bound
    else:
        return "minor"

    from . import config

    model = _load_severity_model()
    if model is None:
        return "fatal" if deviation >= config.DEPTH_SEVERITY_FALLBACK_THRESHOLD else "minor"

    # 缺失 curve 范围时，用 axis 范围填充，保持与训练脚本一致
    curve_y_min = curve_y_min if curve_y_min is not None else min_bound
    curve_y_max = curve_y_max if curve_y_max is not None else max_bound

    features = {
        "relative_deviation": deviation,
        "absolute_deviation": absolute_deviation,
        "claim_value": value_f,
        "span": span,
        "axis_min": min_bound,
        "axis_max": max_bound,
        "curve_y_min": curve_y_min,
        "curve_y_max": curve_y_max,
        "is_curve": 1 if curve_y_min is not None and curve_y_max is not None else 0,
    }
    return _evaluate_tree(model, features)


def clear_model_cache() -> None:
    """清除加载的模型缓存（主要用于测试）。"""
    global _SEVERITY_MODEL, _MODEL_MTIME, _MODEL_PATH
    _SEVERITY_MODEL = None
    _MODEL_MTIME = 0.0
    _MODEL_PATH = None
