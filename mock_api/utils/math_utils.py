"""Canonical math utility functions for PaperForge.

Contains:
- clamp_float: clamp a value to [lo, hi] (unified from depth_eval_v4._clamp_float
  and depth_calibration._clamp)
- to_floats: convert a list of values to floats (unified from figure_curves._to_floats
  and figure_claims._to_floats)

These were previously duplicated across depth_eval_v4.py, depth_calibration.py,
figure_curves.py, and figure_claims.py.  Centralised here (2026-07-26).
"""

from __future__ import annotations

from typing import Any


def clamp_float(val: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *val* to the inclusive range [*lo*, *hi*].

    Non-numeric values silently return the midpoint.
    """
    try:
        f = float(val)
    except (TypeError, ValueError):
        return (lo + hi) / 2
    if f < lo:
        return lo
    if f > hi:
        return hi
    return f


def to_floats(values: Any) -> list[float]:
    """Convert a list of (possibly string / mixed) values to floats.

    Invalid entries are silently dropped.
    """
    if not isinstance(values, list):
        return []
    result: list[float] = []
    for v in values:
        try:
            result.append(float(v))
        except (TypeError, ValueError):
            pass
    return result
