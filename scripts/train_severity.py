#!/usr/bin/env python3
"""基于历史审稿数据训练 severity 决策树分类器。

用法示例：
    python scripts/train_severity.py --output mock_api/config/severity_model.json

依赖：scikit-learn（训练时）；运行时仅依赖 severity_classifier.py 中的 JSON 树解析。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# 允许从项目根目录直接运行脚本
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import numpy as np
    from sklearn.tree import DecisionTreeClassifier
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "训练脚本需要 scikit-learn。请先安装：pip install scikit-learn>=1.3.0"
    ) from exc

from mock_api.database import SessionLocal
from mock_api.models import DepthReviewV4
from mock_api.severity_classifier import clear_model_cache

logger = logging.getLogger(__name__)

FEATURE_NAMES = [
    "relative_deviation",
    "absolute_deviation",
    "claim_value",
    "span",
    "axis_min",
    "axis_max",
    "curve_y_min",
    "curve_y_max",
    "is_curve",
]
# NOTE: paper-level risk could be added once the call site in depth_eval_v4.py
# threads the final-paper risk through; currently it is not available at runtime.



class InsufficientDataError(Exception):
    """训练样本不足时抛出的异常。"""


_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _parse_range(text: str) -> tuple[float, float] | None:
    """从类似 '[0.0, 100.0]' 的字符串中提取 (min, max)。"""
    nums = _NUMBER_RE.findall(text)
    if len(nums) < 2:
        return None
    try:
        values = [float(n.replace(",", "")) for n in nums[:2]]
    except (TypeError, ValueError):
        return None
    return min(values), max(values)


# 兼容中英文两种 content 格式
_CONTENT_RE = re.compile(
    r"\[(?P<severity>\w+)\]\s*\[?(?:图文一致性|Figure consistency)\]?\s*"
    r"(?:图|Figure)\s*(?P<figure_idx>\d+)\s*"
    r"\((?:第\s*(?P<page_cn>\d+)\s*页|p(?P<page_en>\d+))\):\s*"
    r"(?P<metric>[^=]+?)\s*=\s*(?P<value>[^ ]+)\s*"
    r"(?:超出|is outside)\s*(?:坐标轴范围|axis range)\s*"
    r"(?P<axis_range>\[[^\]]+\])"
    r"(?:[^\[]*?(?:曲线点 y 范围|curve_points y-range)\s*\[(?P<curve_range>[^\]]+)\])?",
    re.IGNORECASE,
)


def _extract_claim_from_content(content: str) -> dict[str, Any] | None:
    """从证据 content 中解析出 value、axis range、curve range 等关键字段。

    解析失败时返回 None。
    """
    if not content:
        return None
    m = _CONTENT_RE.search(content)
    if not m:
        return None
    value = _parse_float(m.group("value"))
    axis_range = _parse_range(m.group("axis_range"))
    if value is None or axis_range is None:
        return None
    axis_min, axis_max = axis_range
    curve_min: float | None = None
    curve_max: float | None = None
    curve_range_text = m.group("curve_range")
    if curve_range_text:
        parsed = _parse_range(curve_range_text)
        if parsed:
            curve_min, curve_max = parsed
    return {
        "value": value,
        "axis_min": axis_min,
        "axis_max": axis_max,
        "curve_min": curve_min,
        "curve_max": curve_max,
        "metric": m.group("metric").strip(),
        "figure_index": int(m.group("figure_idx")),
        "severity": m.group("severity"),
    }


def _compute_features(claim: dict[str, Any]) -> dict[str, float] | None:
    """根据解析出的 claim 计算模型特征。"""
    value = float(claim["value"])
    axis_min = float(claim["axis_min"])
    axis_max = float(claim["axis_max"])
    span = axis_max - axis_min
    if span <= 0:
        return None

    curve_min = claim.get("curve_min")
    curve_max = claim.get("curve_max")

    if value < axis_min:
        absolute_deviation = axis_min - value
        deviation = absolute_deviation / span
    elif value > axis_max:
        absolute_deviation = value - axis_max
        deviation = absolute_deviation / span
    else:
        # 解析到的是已越界证据，不应该出现在训练集；防御性跳过
        return None

    # 缺失 curve 范围时，用 axis 范围填充，保证训练/运行时特征一致
    curve_y_min = curve_min if curve_min is not None else axis_min
    curve_y_max = curve_max if curve_max is not None else axis_max

    return {
        "relative_deviation": deviation,
        "absolute_deviation": absolute_deviation,
        "claim_value": value,
        "span": span,
        "axis_min": axis_min,
        "axis_max": axis_max,
        "curve_y_min": curve_y_min,
        "curve_y_max": curve_y_max,
        "is_curve": 1 if curve_min is not None and curve_max is not None else 0,
    }


def _decide_label(
    evidence_item: dict[str, Any],
    final_verdict: dict[str, Any],
    q5a_result: dict[str, Any],
    claim: dict[str, Any],
    label_source: str,
) -> str:
    """根据 label_source 策略决定样本标签。"""
    verdict = (final_verdict or {}).get("final_verdict", "")
    item_severity = str(evidence_item.get("severity", "minor")).lower()

    if label_source == "evidence":
        return "fatal" if item_severity == "fatal" else "minor"
    if label_source == "verdict":
        return "fatal" if verdict == "reject" else "minor"
    if label_source == "critique_points":
        return "fatal" if _is_fatal_by_critique_points(claim, q5a_result) else "minor"

    # hybrid（默认）：证据本身已是 fatal，或者论文最终被判 reject，均视为 fatal
    if item_severity == "fatal" or verdict == "reject":
        return "fatal"
    return "minor"


def _is_fatal_by_critique_points(
    claim: dict[str, Any],
    q5a_result: dict[str, Any],
) -> bool:
    """若任一 fatal critique point 提到该 figure 或 metric，则判为 fatal。"""
    points = (q5a_result or {}).get("critique_points") or []
    if not isinstance(points, list):
        return False
    fig_idx = str(claim.get("figure_index", ""))
    metric = str(claim.get("metric", "")).lower()
    for pt in points:
        if not isinstance(pt, dict):
            continue
        if str(pt.get("severity", "")).lower() != "fatal":
            continue
        text = str(pt.get("point", "")).lower()
        if (
            f"图{fig_idx}" in text
            or f"figure {fig_idx}" in text
            or f"图 {fig_idx}" in text
            or (metric and metric in text)
        ):
            return True
    return False


def _build_dataset(
    label_source: str = "hybrid",
    paper_ids: list[str] | None = None,
) -> tuple[list[dict[str, float]], list[str]]:
    """从历史 DepthReviewV4 记录中提取 severity 训练样本。

    返回 (features, labels)。
    """
    session = SessionLocal()
    try:
        query = session.query(DepthReviewV4).filter(
            DepthReviewV4.kind == "paper",
            DepthReviewV4.status == "completed",
        )
        if paper_ids:
            query = query.filter(DepthReviewV4.paper_id.in_(paper_ids))

        rows = query.all()
        logger.info("找到 %d 条已完成论文审稿记录", len(rows))

        X: list[dict[str, float]] = []
        y: list[str] = []

        for row in rows:
            evidence_pool = (row.evidence_pool or []) if row.evidence_pool else []
            final_verdict = row.final_verdict or {}
            q5a_result = row.q5a_result or {}
            for item in evidence_pool:
                if not isinstance(item, dict):
                    continue
                # 只关心图文一致性 out-of-range claim
                if item.get("section") != "Figures":
                    continue
                content = item.get("content") or item.get("content_zh") or item.get("content_en", "")
                claim = _extract_claim_from_content(content)
                if claim is None:
                    continue
                features = _compute_features(claim)
                if features is None:
                    continue
                label = _decide_label(item, final_verdict, q5a_result, claim, label_source)
                X.append(features)
                y.append(label)

        return X, y
    finally:
        session.close()


def _tree_to_dict(clf: DecisionTreeClassifier, feature_names: list[str]) -> dict[str, Any]:
    """将 sklearn 决策树序列化为 severity_classifier.py 可解析的字典。"""
    tree = clf.tree_
    classes = clf.classes_

    def _node_to_dict(node_id: int) -> dict[str, Any]:
        left = tree.children_left[node_id]
        right = tree.children_right[node_id]
        if left == right:  # leaf
            class_idx = int(tree.value[node_id].argmax())
            return {"class": str(classes[class_idx])}
        return {
            "feature": feature_names[tree.feature[node_id]],
            "threshold": float(tree.threshold[node_id]),
            "left": _node_to_dict(left),
            "right": _node_to_dict(right),
        }

    return _node_to_dict(0)


def train(
    X: list[dict[str, float]],
    y: list[str],
    *,
    max_depth: int = 4,
    min_samples_leaf: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """训练决策树并返回 JSON 树 + 元信息。"""
    if len(X) < 10:
        raise InsufficientDataError(f"训练样本不足：仅有 {len(X)} 条 out-of-range claim 记录")

    X_arr = np.array([[f[name] for name in FEATURE_NAMES] for f in X])
    y_arr = np.array(y)

    clf = DecisionTreeClassifier(
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        random_state=42,
        class_weight="balanced",
    )
    clf.fit(X_arr, y_arr)

    tree_dict = _tree_to_dict(clf, FEATURE_NAMES)
    importances = dict(zip(FEATURE_NAMES, clf.feature_importances_.tolist()))
    train_acc = float(clf.score(X_arr, y_arr))

    unique_labels, counts = np.unique(y_arr, return_counts=True)
    metadata = {
        "n_samples": int(len(X)),
        "label_distribution": {str(k): int(v) for k, v in zip(unique_labels, counts)},
        "feature_importances": importances,
        "train_accuracy": train_acc,
        "max_depth": int(max_depth),
        "min_samples_leaf": int(min_samples_leaf),
    }

    return tree_dict, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="训练 DEPTH severity 决策树模型")
    parser.add_argument(
        "--output",
        "-o",
        default=str(PROJECT_ROOT / "mock_api" / "config" / "severity_model.json"),
        help="输出模型 JSON 路径",
    )
    parser.add_argument(
        "--label-source",
        choices=["hybrid", "evidence", "verdict", "critique_points"],
        default="hybrid",
        help=(
            "标签来源：hybrid=证据 severity+论文终审判决；evidence=仅证据 severity；"
            "verdict=仅终审判绝；critique_points=依据 Q5a critique points 中 fatal 条目"
        ),
    )
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--min-samples-leaf", type=int, default=5)
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument(
        "--paper-ids",
        default="",
        help="逗号分隔的 paper_id 白名单，留空则使用全部历史记录",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="打印更多调试信息")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    paper_ids = None
    if args.paper_ids:
        paper_ids = [pid.strip() for pid in args.paper_ids.split(",") if pid.strip()]

    logger.info("开始构建训练数据集 (label_source=%s)", args.label_source)
    X, y = _build_dataset(label_source=args.label_source, paper_ids=paper_ids)
    if len(X) < args.min_samples:
        logger.error(
            "可用于训练的 out-of-range claim 样本数 %d 少于 --min-samples %d，"
            "无法可靠训练模型。请积累更多历史审稿数据。",
            len(X),
            args.min_samples,
        )
        return 1

    logger.info("共收集 %d 条训练样本", len(X))
    logger.info("开始训练决策树 (max_depth=%d, min_samples_leaf=%d)", args.max_depth, args.min_samples_leaf)
    try:
        tree_dict, metadata = train(
            X,
            y,
            max_depth=args.max_depth,
            min_samples_leaf=args.min_samples_leaf,
        )
    except InsufficientDataError as exc:
        logger.error("%s", exc)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model_payload = {
        "tree": tree_dict,
        "metadata": metadata,
        "feature_names": FEATURE_NAMES,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(model_payload, f, ensure_ascii=False, indent=2)

    # 训练进程内清空缓存，让同进程后续调用能加载新模型
    clear_model_cache()

    logger.info("模型已保存到 %s", output_path)
    logger.info("元信息：%s", json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
