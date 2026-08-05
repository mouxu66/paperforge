"""Tests for scripts/evaluate_figure_annotations.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.evaluate_figure_annotations import (
    _cohens_kappa,
    _compute_metrics,
    _spearman,
)


def test_spearman_perfect_correlation() -> None:
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0]
    assert _spearman(x, y) == pytest.approx(1.0, abs=1e-6)


def test_spearman_perfect_inverse() -> None:
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [5.0, 4.0, 3.0, 2.0, 1.0]
    assert _spearman(x, y) == pytest.approx(-1.0, abs=1e-6)


def test_spearman_too_short() -> None:
    assert _spearman([1.0], [2.0]) == 0.0


def test_cohens_kappa() -> None:
    ann1 = {"t1": "consistent", "t2": "major_inconsistency", "t3": "consistent"}
    ann2 = {"t1": "consistent", "t2": "consistent", "t3": "consistent"}
    kappa = _cohens_kappa(ann1, ann2)
    assert -1.0 <= kappa <= 1.0


def test_compute_metrics(tmp_path: Path) -> None:
    human = [
        {
            "task_id": "fig_0001",
            "overall_label": "consistent",
            "flags": [],
            "confidence": "high",
        },
        {
            "task_id": "fig_0002",
            "overall_label": "major_inconsistency",
            "flags": ["value_mismatch"],
            "confidence": "high",
        },
        {
            "task_id": "fig_0003",
            "overall_label": "minor_inconsistency",
            "flags": ["axis_label_error"],
            "confidence": "medium",
        },
        {
            "task_id": "fig_0004",
            "overall_label": "cannot_judge",
            "flags": [],
            "confidence": "low",
        },
    ]
    predicted = [
        {
            "task_id": "fig_0001",
            "figure_consistency_score": 0.9,
            "inconsistency_flags": [],
        },
        {
            "task_id": "fig_0002",
            "figure_consistency_score": 0.2,
            "inconsistency_flags": ["value_mismatch"],
        },
        {
            "task_id": "fig_0003",
            "figure_consistency_score": 0.5,
            "inconsistency_flags": ["axis_label_error"],
        },
    ]
    human_path = tmp_path / "human.jsonl"
    predicted_path = tmp_path / "predicted.jsonl"
    output_path = tmp_path / "metrics.json"

    with human_path.open("w", encoding="utf-8") as f:
        for record in human:
            f.write(json.dumps(record) + "\n")

    with predicted_path.open("w", encoding="utf-8") as f:
        for record in predicted:
            f.write(json.dumps(record) + "\n")

    metrics = _compute_metrics(human_path, predicted_path, output_path)

    assert metrics["total_tasks"] == 3
    assert "spearman_correlation" in metrics
    assert "binary_roc_auc" in metrics
    assert metrics["binary_roc_auc"] >= 0.0
    assert "flag_metrics" in metrics
    assert "value_mismatch" in metrics["flag_metrics"]
    assert output_path.exists()
    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded["total_tasks"] == 3
