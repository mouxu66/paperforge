"""Backward-compatible import shim for the moved calibration script."""

from scripts.calibration.export_qf_predictions import (
    _latest_review_for_paper,
    export_predictions,
    main,
)

__all__ = ["_latest_review_for_paper", "export_predictions", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
