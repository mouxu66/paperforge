#!/usr/bin/env python3
"""Evaluate DEPTH figure-consistency predictions against human annotations.

Computes correlation, ordinal metrics, per-flag precision/recall, and calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

LABEL_ORDINAL = {
    "consistent": 0,
    "minor_inconsistency": 1,
    "major_inconsistency": 2,
    "cannot_judge": None,
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _key_by_task_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for record in records:
        task_id = record.get("task_id")
        if not task_id:
            continue
        if task_id in keyed:
            raise ValueError(f"Duplicate task_id in predictions: {task_id}")
        keyed[task_id] = record
    return keyed


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (naive implementation without scipy)."""
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return 0.0

    def rank(values: list[float]) -> list[float]:
        sorted_vals = sorted(values)
        ranks: list[float] = []
        for v in values:
            count = sum(1 for sv in sorted_vals if sv < v)
            tie = sum(1 for sv in sorted_vals if sv == v)
            ranks.append(count + (tie - 1) / 2.0 + 1)
        return ranks

    rx, ry = rank(x), rank(y)
    n = len(rx)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = math.sqrt(sum((v - mean_rx) ** 2 for v in rx))
    den_y = math.sqrt(sum((v - mean_ry) ** 2 for v in ry))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _cohens_kappa(ann1: dict[str, Any], ann2: dict[str, Any]) -> float:
    """Simple Cohen's kappa for the overall_label field across overlapping tasks."""
    labels = set(ann1.keys()) & set(ann2.keys())
    if not labels:
        return 0.0

    agreed = sum(1 for t in labels if ann1[t] == ann2[t])
    total = len(labels)
    p_o = agreed / total if total else 0.0

    # Expected agreement based on marginal distributions
    cat_counts1: dict[str, int] = defaultdict(int)
    cat_counts2: dict[str, int] = defaultdict(int)
    for t in labels:
        cat_counts1[ann1[t]] += 1
        cat_counts2[ann2[t]] += 1

    p_e = 0.0
    for cat in set(cat_counts1.keys()) | set(cat_counts2.keys()):
        p_e += (cat_counts1[cat] / total) * (cat_counts2[cat] / total)

    if p_e >= 0.9999:
        return 1.0
    return (p_o - p_e) / (1 - p_e)


def _compute_metrics(
    human_path: Path,
    predicted_path: Path,
    output_path: Path | None,
    human2_path: Path | None = None,
) -> dict[str, Any]:
    human_records = _load_jsonl(human_path)
    predicted_records = _load_jsonl(predicted_path)

    human_by_task = _key_by_task_id(human_records)
    predicted_by_task = _key_by_task_id(predicted_records)

    common_tasks = set(human_by_task.keys()) & set(predicted_by_task.keys())
    if not common_tasks:
        raise ValueError("No common task_id between human and predicted files")

    # Ordinal analysis (skip cannot_judge)
    task_inconsistency: dict[str, float] = {}
    task_ordinal: dict[str, int] = {}
    for task_id in common_tasks:
        human_label = human_by_task[task_id].get("overall_label", "cannot_judge")
        ordinal = LABEL_ORDINAL.get(human_label)
        pred_score = predicted_by_task[task_id].get("figure_consistency_score", 0.5)
        if ordinal is not None:
            task_ordinal[task_id] = ordinal
            task_inconsistency[task_id] = 1.0 - float(pred_score)

    human_scores = list(task_ordinal.values())
    inconsistency_scores = list(task_inconsistency.values())

    correlation = _spearman(inconsistency_scores, human_scores) if len(human_scores) >= 2 else None

    # Binary major inconsistency AUC (major=1, others=0)
    binary_labels = [1 if s >= 2 else 0 for s in human_scores]
    # ROC-AUC using trapezoidal rule over sorted predictions
    sorted_pairs = sorted(
        zip(inconsistency_scores, binary_labels),
        key=lambda x: x[0],
        reverse=True,
    )
    n_pos = sum(binary_labels)
    n_neg = len(binary_labels) - n_pos
    tpr_list: list[float] = [0.0]
    fpr_list: list[float] = [0.0]
    tp = 0
    fp = 0
    for score, label in sorted_pairs:
        if label:
            tp += 1
        else:
            fp += 1
        tpr_list.append(tp / n_pos if n_pos else 0.0)
        fpr_list.append(fp / n_neg if n_neg else 0.0)

    auc = 0.0
    for i in range(1, len(tpr_list)):
        auc += tpr_list[i] * (fpr_list[i] - fpr_list[i - 1])

    # Per-flag precision/recall/F1
    flag_metrics: dict[str, dict[str, float]] = {}
    all_flags: set[str] = set()
    for task_id in common_tasks:
        pred_flags = set(predicted_by_task[task_id].get("inconsistency_flags", []))
        human_flags = set(human_by_task[task_id].get("flags", []))
        all_flags.update(pred_flags | human_flags)

    for flag in sorted(all_flags):
        tp = 0
        fp = 0
        fn = 0
        for task_id in common_tasks:
            pred_flags = set(predicted_by_task[task_id].get("inconsistency_flags", []))
            human_flags = set(human_by_task[task_id].get("flags", []))
            if flag in pred_flags and flag in human_flags:
                tp += 1
            elif flag in pred_flags and flag not in human_flags:
                fp += 1
            elif flag not in pred_flags and flag in human_flags:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        flag_metrics[flag] = {"precision": precision, "recall": recall, "f1": f1}

    # Calibration: binned inconsistency score vs human major proportion
    bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    calibration: list[dict[str, Any]] = []
    for low, high in bins:
        bin_tasks = [
            (task_id, human_by_task[task_id])
            for task_id in common_tasks
            if task_id in task_inconsistency and low < task_inconsistency[task_id] <= high
        ]
        if not bin_tasks:
            continue
        major = sum(
            1
            for _, human in bin_tasks
            if LABEL_ORDINAL.get(human.get("overall_label", "consistent"), 0) >= 2
        )
        calibration.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "count": len(bin_tasks),
                "major_ratio": major / len(bin_tasks),
            }
        )

    # Inter-annotator agreement (optional second human file)
    inter_annotator_agreement: dict[str, Any] = {}
    if human2_path:
        human2_records = _load_jsonl(human2_path)
        human2_by_task = _key_by_task_id(human2_records)
        overlap = set(human_by_task.keys()) & set(human2_by_task.keys())
        if overlap:
            labels1 = {t: human_by_task[t].get("overall_label", "cannot_judge") for t in overlap}
            labels2 = {t: human2_by_task[t].get("overall_label", "cannot_judge") for t in overlap}
            inter_annotator_agreement = {
                "overlap_count": len(overlap),
                "cohens_kappa": _cohens_kappa(labels1, labels2),
            }
        else:
            inter_annotator_agreement = {"overlap_count": 0, "cohens_kappa": 0.0}

    metrics: dict[str, Any] = {
        "total_tasks": len(common_tasks),
        "spearman_correlation": correlation,
        "binary_roc_auc": auc,
        "flag_metrics": flag_metrics,
        "calibration": calibration,
        "inter_annotator_agreement": inter_annotator_agreement,
    }

    if output_path:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        print(f"Metrics written to {output_path}")

    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate QF figure-consistency predictions against human annotations.",
    )
    parser.add_argument("--human", required=True, type=str, help="Human annotation JSONL file.")
    parser.add_argument(
        "--human-2",
        type=str,
        default=None,
        help="Optional second human annotation JSONL file (for inter-annotator agreement).",
    )
    parser.add_argument(
        "--predicted",
        required=True,
        type=str,
        help="QF prediction JSONL file.",
    )
    parser.add_argument("--output", type=str, default=None, help="Output metrics JSON path.")
    args = parser.parse_args(argv)

    human_path = Path(args.human)
    human2_path = Path(args.human_2) if args.human_2 else None
    predicted_path = Path(args.predicted)
    output_path = Path(args.output) if args.output else None

    if not human_path.exists():
        print(f"Human annotation file not found: {human_path}", file=sys.stderr)
        return 1
    if human2_path and not human2_path.exists():
        print(f"Second human annotation file not found: {human2_path}", file=sys.stderr)
        return 1
    if not predicted_path.exists():
        print(f"Prediction file not found: {predicted_path}", file=sys.stderr)
        return 1

    metrics = _compute_metrics(human_path, predicted_path, output_path, human2_path)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
