"""CV-based curve point extraction from matplotlib-style charts.

P3: Extract sample points from figure images and map them back to data
coordinates using the provided axis_info.

Current scope:
- matplotlib line charts (single or multiple series)
- bar charts
- scatter plots
- linear and logarithmic axes
- Uses only PIL + NumPy (no OpenCV dependency)
- Fail-open: returns None on any error
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from PIL import Image

from .utils.math_utils import to_floats

logger = logging.getLogger(__name__)

# Pixels considered "background" / non-data
_MAX_BG_VALUE = 240  # drop near-white background
_MIN_LINE_VALUE = 40  # drop near-black axes/text


def extract_curve_points(
    figure_path: str,
    axis_info: dict[str, Any] | None,
    *,
    max_points_per_series: int = 200,
) -> list[dict[str, Any]] | None:
    """Extract sampled curve points from a matplotlib-style chart.

    Args:
        figure_path: Path to the figure image file.
        axis_info: Structured axis info with x_ticks / y_ticks.
            Expected keys: x_ticks (list[float]), y_ticks (list[float]).
            Optional keys: figure_type, caption_summary, x_scale, y_scale.
        max_points_per_series: Maximum points to retain per curve series.

    Returns:
        List of points, e.g.:
            [{"series": "#ff0000", "x": 12.3, "y": 0.85}, ...]
        Returns None if extraction fails or axis_info is insufficient.
    """
    if not axis_info:
        return None

    x_ticks = to_floats(axis_info.get("x_ticks"))
    y_ticks = to_floats(axis_info.get("y_ticks"))
    if len(x_ticks) < 2 or len(y_ticks) < 2:
        logger.debug("curve extraction skipped: insufficient axis ticks")
        return None

    # Chart-type detection: prefer explicit figure_type, fall back to caption keywords.
    fig_type = _detect_chart_type(axis_info)
    x_scale = _to_scale(axis_info.get("x_scale"))
    y_scale = _to_scale(axis_info.get("y_scale"))

    try:
        img = Image.open(figure_path).convert("RGB")
        arr = np.array(img)
        if arr.size == 0:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("curve extraction failed to open image %s: %s", figure_path, exc)
        return None

    try:
        # 1) Detect inner plot area (crop margins / axes)
        top, bottom, left, right = _detect_plot_bbox(arr)
        plot_w = right - left
        plot_h = bottom - top
        if plot_w < 20 or plot_h < 20:
            return None

        # 2) Extract data pixels inside the plot area
        plot_region = arr[top:bottom, left:right]
        mask = _data_pixel_mask(plot_region)

        # 3) Cluster colored pixels into series
        series_pixels = _cluster_colors(plot_region, mask)
        if not series_pixels:
            return None

        # 4) Map pixel coordinates to data coordinates
        x_min, x_max = min(x_ticks), max(x_ticks)
        y_min, y_max = min(y_ticks), max(y_ticks)

        points: list[dict[str, Any]] = []
        for series_label, pixels in series_pixels.items():
            if fig_type == "bar_chart":
                sampled = _sample_bar(pixels, plot_w, plot_h)
            elif fig_type == "scatter":
                sampled = _sample_scatter(pixels, max_points=max_points_per_series)
            else:
                sampled = _sample_line(pixels, max_points=max_points_per_series)

            for x_px, y_px in sampled:
                x_data = _map_coordinate(x_px, plot_w, x_min, x_max, x_scale, invert=False)
                y_data = _map_coordinate(y_px, plot_h, y_min, y_max, y_scale, invert=True)
                points.append(
                    {
                        "series": series_label,
                        "x": round(float(x_data), 4),
                        "y": round(float(y_data), 4),
                    }
                )

        if not points:
            return None
        return points
    except Exception as exc:  # noqa: BLE001
        logger.debug("curve extraction failed for %s: %s", figure_path, exc)
        return None


# _to_floats removed → use utils.math_utils.to_floats


def _detect_plot_bbox(arr: np.ndarray) -> tuple[int, int, int, int]:
    """Return (top, bottom, left, right) bounding box of the inner plot area.

    Strategy for matplotlib charts:
    - Axes and labels are dark; plot background is white/light.
    - Find the largest rectangle of light background surrounded by darker pixels.
    - If that fails, fall back to cropping 8% margins.
    """
    h, w = arr.shape[:2]
    gray = _to_gray(arr)

    # Light pixels are candidate background; dark pixels are axes/text/labels
    # We look for the inner rectangular region with the most background pixels.
    light = (gray > 200) & (gray < 255)

    # Use integral-image-like row/col sums to find the inner light rectangle
    row_light = light.sum(axis=1)
    col_light = light.sum(axis=0)

    # Find the longest contiguous run of rows/cols where most pixels are light
    def _largest_run(values: np.ndarray) -> tuple[int, int]:
        best_start, best_end, best_len = 0, len(values), 0
        start = 0
        for i, v in enumerate(values):
            if v < max(10, values.max() * 0.3):
                if i - start > best_len:
                    best_len = i - start
                    best_start, best_end = start, i
                start = i + 1
        if len(values) - start > best_len:
            best_len = len(values) - start
            best_start, best_end = start, len(values)
        return best_start, best_end

    top, bottom = _largest_run(row_light)
    left, right = _largest_run(col_light)

    if bottom <= top or right <= left:
        # fallback
        margin_x = int(w * 0.08)
        margin_y = int(h * 0.08)
        return (margin_y, h - margin_y, margin_x, w - margin_x)

    # Add small inset to keep points away from the axes themselves
    inset = 2
    top = min(bottom - 1, top + inset)
    bottom = max(top + 1, bottom - inset)
    left = min(right - 1, left + inset)
    right = max(left + 1, right - inset)

    return (top, bottom, left, right)


def _to_gray(arr: np.ndarray) -> np.ndarray:
    r, g, b = (
        arr[:, :, 0].astype(np.float32),
        arr[:, :, 1].astype(np.float32),
        arr[:, :, 2].astype(np.float32),
    )
    return (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)


def _data_pixel_mask(region: np.ndarray) -> np.ndarray:
    """Return boolean mask of pixels likely belonging to a data line."""
    max_c = region.max(axis=2)
    min_c = region.min(axis=2)
    mean = region.mean(axis=2)

    # Drop near-white background (all channels high)
    is_white = min_c > 240
    # Drop dark axes/text labels (overall brightness too low)
    is_dark = mean < 50
    # Drop grayscale axes/gridlines (little color saturation)
    saturation = max_c.astype(np.int16) - min_c.astype(np.int16)
    is_gray = saturation < 15

    return ~(is_white | is_dark | is_gray)


def _cluster_colors(
    region: np.ndarray,
    mask: np.ndarray,
    max_series: int = 5,
) -> dict[str, list[tuple[int, int]]]:
    """Group data pixels by color into distinct series."""
    coords = np.argwhere(mask)
    if len(coords) < 10:
        return {}

    pixels = region[mask]
    # Bucket colors into 6-bit bins for robust grouping
    bins = 8
    bucket = (pixels // (256 // bins)).astype(np.uint8)
    color_key = (
        (bucket[:, 0].astype(np.uint32) << 16)
        | (bucket[:, 1].astype(np.uint32) << 8)
        | bucket[:, 2].astype(np.uint32)
    )

    unique_keys, inverse, counts = np.unique(color_key, return_inverse=True, return_counts=True)
    if len(unique_keys) == 0:
        return {}

    # Keep top series by pixel count, ignoring tiny clusters
    min_cluster = max(10, int(len(coords) * 0.005))
    top_indices = np.argsort(counts)[::-1][:max_series]

    series: dict[str, list[tuple[int, int]]] = {}
    for idx in top_indices:
        if counts[idx] < min_cluster:
            continue
        members = coords[inverse == idx]
        # Representative color
        rep = pixels[inverse == idx].mean(axis=0).astype(int)
        hex_color = f"#{rep[0]:02x}{rep[1]:02x}{rep[2]:02x}"
        series[hex_color] = [(int(x), int(y)) for y, x in members]

    return series


def _detect_chart_type(axis_info: dict[str, Any]) -> str:
    """Detect chart type from axis_info.

    Prefers the explicit ``figure_type`` field, otherwise falls back to
    keyword matching against ``caption_summary``. Defaults to line_chart.
    """
    fig_type = (axis_info.get("figure_type") or "").strip().lower()
    if fig_type in {"line_chart", "bar_chart", "scatter", "heatmap", "table", "diagram"}:
        if fig_type in {"line_chart", "bar_chart", "scatter"}:
            return fig_type
        # Other structured types currently route to line-like extraction.
        return "line_chart"

    caption = (axis_info.get("caption_summary") or "").lower()
    if "bar chart" in caption or "柱状图" in caption or "条形图" in caption:
        return "bar_chart"
    if "scatter" in caption or "散点" in caption:
        return "scatter"
    return "line_chart"


def _to_scale(value: Any) -> str:
    """Normalize scale value to 'linear' or 'log'."""
    if value and str(value).lower() in {"log", "logarithmic"}:
        return "log"
    return "linear"


def _map_coordinate(
    px: int,
    max_px: int,
    data_min: float,
    data_max: float,
    scale: str,
    *,
    invert: bool = False,
) -> float:
    """Map a pixel coordinate to a data coordinate respecting linear/log scale.

    Args:
        px: pixel coordinate along the axis (0..max_px-1).
        max_px: number of pixels along the axis.
        data_min, data_max: data range for the axis.
        scale: 'linear' or 'log'.
        invert: if True, flip the normalized value (used for y axis).
    """
    norm = px / max(max_px - 1, 1)
    if invert:
        norm = 1.0 - norm

    if scale == "log" and data_min > 0 and data_max > 0:
        log_min = math.log10(data_min)
        log_max = math.log10(data_max)
        return 10 ** (log_min + norm * (log_max - log_min))

    return data_min + norm * (data_max - data_min)


def _sample_line(
    pixels: list[tuple[int, int]],
    *,
    max_points: int = 200,
) -> list[tuple[int, int]]:
    """Sample a line series by taking the median y per x column."""
    if not pixels:
        return []

    col_map: dict[int, list[int]] = {}
    for x, y in pixels:
        col_map.setdefault(x, []).append(y)

    raw_points: list[tuple[int, int]] = []
    for x in sorted(col_map):
        y = int(np.median(col_map[x]))
        raw_points.append((x, y))

    if not raw_points:
        return []

    # Downsample
    if len(raw_points) > max_points:
        step = max(1, len(raw_points) // max_points)
        raw_points = raw_points[::step]

    return raw_points


def _sample_bar(
    pixels: list[tuple[int, int]],
    plot_w: int,
    plot_h: int,
) -> list[tuple[int, int]]:
    """Sample a bar series by detecting contiguous horizontal bars.

    Returns one (x, y) point per bar, where x is the bar center and y is the
    top of the bar (in pixel coordinates, smaller y means higher value).
    """
    if not pixels:
        return []

    col_map: dict[int, list[int]] = {}
    for x, y in pixels:
        col_map.setdefault(x, []).append(y)

    bars: list[tuple[int, int]] = []
    current_run: list[int] = []
    min_run_width = max(2, plot_w // 40)
    # Allow small inter-bar gaps caused by anti-aliasing / spacing.
    max_gap = max(1, plot_w // 100)
    for x in sorted(col_map):
        if not current_run or x - current_run[-1] <= max_gap:
            current_run.append(x)
        else:
            if len(current_run) >= min_run_width:
                center_x = int(sum(current_run) / len(current_run))
                top_y = min(min(col_map[cx]) for cx in current_run)
                bars.append((center_x, top_y))
            current_run = [x]

    if len(current_run) >= min_run_width:
        center_x = int(sum(current_run) / len(current_run))
        top_y = min(min(col_map[cx]) for cx in current_run)
        bars.append((center_x, top_y))

    return bars


def _sample_scatter(
    pixels: list[tuple[int, int]],
    *,
    max_points: int = 200,
) -> list[tuple[int, int]]:
    """Sample a scatter series by returning representative marker centers.

    Uses connected-component-like clustering on the raw pixels and returns the
    centroid of each component, downsampled if needed.
    """
    if not pixels:
        return []

    # Find connected components via simple flood fill on a sparse adjacency map.
    pixels_set = set(pixels)
    visited: set[tuple[int, int]] = set()
    centers: list[tuple[int, int]] = []

    for start in pixels:
        if start in visited:
            continue
        # BFS for this component
        stack = [start]
        component: list[tuple[int, int]] = []
        visited.add(start)
        while stack:
            cx, cy = stack.pop()
            component.append((cx, cy))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (cx + dx, cy + dy)
                if nb in pixels_set and nb not in visited:
                    visited.add(nb)
                    stack.append(nb)

        if not component:
            continue
        xs = [p[0] for p in component]
        ys = [p[1] for p in component]
        centers.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys))))

    # Downsample if too many clusters
    if len(centers) > max_points:
        step = max(1, len(centers) // max_points)
        centers = centers[::step]

    return centers
