"""Backward-compatible import shim for the moved calibration script."""

from scripts.calibration.evaluate_figure_annotations import (
    _cohens_kappa,
    _compute_metrics,
    _key_by_task_id,
    _load_jsonl,
    _spearman,
    main,
)

__all__ = ["_cohens_kappa", "_compute_metrics", "_key_by_task_id", "_load_jsonl", "_spearman", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
