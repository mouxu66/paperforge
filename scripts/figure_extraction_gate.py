"""Backward-compatible import shim for the moved calibration script."""

from scripts.calibration.figure_extraction_gate import (
    _build_report,
    _collect_pdfs,
    _count_by_source,
    evaluate_pdf,
    main,
)

__all__ = ["_build_report", "_collect_pdfs", "_count_by_source", "evaluate_pdf", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
