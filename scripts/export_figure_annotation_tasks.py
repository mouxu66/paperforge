"""Backward-compatible import shim for the moved calibration script."""

from scripts.calibration.export_figure_annotation_tasks import (
    _extract_caption,
    _find_source_text_spans,
    main,
)

__all__ = ["_extract_caption", "_find_source_text_spans", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
