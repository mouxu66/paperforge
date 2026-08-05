"""Tests for mock_api.figure_curves — CV-based curve point extraction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mock_api.figure_curves import (
    _cluster_colors,
    _data_pixel_mask,
    _detect_chart_type,
    _detect_plot_bbox,
    _map_coordinate,
    _sample_bar,
    _sample_line,
    _sample_scatter,
    extract_curve_points,
)


def _make_line_chart(
    tmp_path: Path,
    size: tuple[int, int] = (400, 300),
    line_color: tuple[int, int, int] = (255, 0, 0),
    series_name: str = "series",
) -> Path:
    """Generate a synthetic matplotlib-style line chart image."""
    w, h = size
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    margin = 40
    # axes
    draw.rectangle([margin, margin, w - margin, h - margin], outline="black", width=2)
    # diagonal line
    for t in range(w - 2 * margin):
        x = margin + t
        y = int(h - margin - (t / (w - 2 * margin)) * (h - 2 * margin))
        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=line_color)
    path = tmp_path / f"{series_name}.png"
    img.save(path)
    return path


def _make_bar_chart(
    tmp_path: Path,
    size: tuple[int, int] = (400, 300),
) -> Path:
    """Generate a synthetic matplotlib-style bar chart with three bars."""
    w, h = size
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    margin = 40
    draw.rectangle([margin, margin, w - margin, h - margin], outline="black", width=2)

    plot_h = h - 2 * margin
    # Heights (in data coordinates 0..100)
    heights = [60, 80, 95]
    bar_width = 40
    gap = 50
    start_x = margin + 50
    for i, height in enumerate(heights):
        bar_h = int((height / 100) * plot_h)
        x0 = start_x + i * (bar_width + gap)
        y0 = h - margin - bar_h
        x1 = x0 + bar_width
        y1 = h - margin
        draw.rectangle([x0, y0, x1, y1], fill="red")
    path = tmp_path / "bar_chart.png"
    img.save(path)
    return path


def _make_scatter_chart(
    tmp_path: Path,
    size: tuple[int, int] = (400, 300),
) -> Path:
    """Generate a synthetic scatter chart with two red marker clusters."""
    w, h = size
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    margin = 40
    draw.rectangle([margin, margin, w - margin, h - margin], outline="black", width=2)

    clusters = [(80, 80), (280, 220)]
    for cx, cy in clusters:
        draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill="red")
    path = tmp_path / "scatter.png"
    img.save(path)
    return path


class TestExtractCurvePoints:
    def test_extract_curve_points_basic(self, tmp_path: Path):
        path = _make_line_chart(tmp_path)
        axis_info = {
            "x_label": "Epoch",
            "y_label": "Accuracy",
            "x_ticks": [0, 100],
            "y_ticks": [0, 1],
        }
        points = extract_curve_points(str(path), axis_info)
        assert points is not None
        assert len(points) > 0
        for p in points:
            assert "series" in p
            assert "x" in p
            assert "y" in p
            assert 0 <= p["x"] <= 100
            assert 0 <= p["y"] <= 1

    def test_extract_curve_points_returns_none_without_axis_info(self, tmp_path: Path):
        path = _make_line_chart(tmp_path)
        assert extract_curve_points(str(path), None) is None

    def test_extract_curve_points_returns_none_with_insufficient_ticks(self, tmp_path: Path):
        path = _make_line_chart(tmp_path)
        assert extract_curve_points(str(path), {"x_ticks": [0], "y_ticks": [0, 1]}) is None

    def test_extract_curve_points_fail_open_on_bad_path(self):
        assert extract_curve_points("/nonexistent/path.png", {"x_ticks": [0, 1], "y_ticks": [0, 1]}) is None

    def test_extract_curve_points_multi_series(self, tmp_path: Path):
        # Create image with two colored lines
        w, h = 400, 300
        img = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(img)
        margin = 40
        draw.rectangle([margin, margin, w - margin, h - margin], outline="black", width=2)

        # red line increasing
        for t in range(w - 2 * margin):
            x = margin + t
            y = int(h - margin - (t / (w - 2 * margin)) * (h - 2 * margin))
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill="red")

        # blue line decreasing
        for t in range(w - 2 * margin):
            x = margin + t
            y = int(margin + (t / (w - 2 * margin)) * (h - 2 * margin))
            draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill="blue")

        path = tmp_path / "multi.png"
        img.save(path)

        axis_info = {
            "x_label": "Epoch",
            "y_label": "Accuracy",
            "x_ticks": [0, 100],
            "y_ticks": [0, 1],
        }
        points = extract_curve_points(str(path), axis_info)
        assert points is not None
        series = {p["series"] for p in points}
        assert len(series) >= 2


class TestHelpers:
    def test_detect_plot_bbox_finds_axes(self):
        # Create a simple image with a dark rectangle frame
        img = Image.new("RGB", (200, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle([40, 40, 160, 160], outline="black", width=2)
        arr = _pil_to_arr(img)
        bbox = _detect_plot_bbox(arr)
        assert bbox is not None
        top, bottom, left, right = bbox
        assert top < bottom
        assert left < right

    def test_data_pixel_mask_filters_background(self):
        img = Image.new("RGB", (100, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.line([(0, 50), (100, 50)], fill="red", width=3)
        arr = _pil_to_arr(img)
        mask = _data_pixel_mask(arr)
        assert mask.any()
        # Background should be mostly filtered out
        assert not mask[0, 0]

    def test_cluster_colors_groups_by_color(self):
        # Synthetic region with red and blue pixels
        region = np.ones((100, 100, 3), dtype=np.uint8) * 255
        region[20:30, 20:80] = [255, 0, 0]  # red
        region[60:70, 20:80] = [0, 0, 255]  # blue
        mask = _data_pixel_mask(region)
        series = _cluster_colors(region, mask)
        assert len(series) >= 1

    def test_sample_line_maps_coordinates(self):
        # 100x100 plot, data range x=[0,10], y=[0,1]
        pixels = [(x, x) for x in range(100)]
        result = _sample_line(pixels, max_points=200)
        assert len(result) > 0
        x0, y0 = result[0]
        # _sample_line returns raw pixel coordinates; caller maps to data.
        assert x0 == 0
        assert y0 == 0
        x1, y1 = result[-1]
        assert x1 == 99
        assert y1 == 99

    def test_sample_bar_detects_bars(self):
        # Three vertical bars at x columns 10-15, 30-35, 50-55 spanning y 50..100
        pixels: list[tuple[int, int]] = []
        for bar_x in [10, 30, 50]:
            for x in range(bar_x, bar_x + 6):
                for y in range(50, 101):
                    pixels.append((x, y))
        bars = _sample_bar(pixels, plot_w=200, plot_h=200)
        assert len(bars) == 3
        centers = sorted([b[0] for b in bars])
        assert centers[0] == 12
        assert centers[1] == 32
        assert centers[2] == 52
        # Top of each bar should be near y=50 (smallest y)
        for _, y in bars:
            assert y == 50

    def test_sample_scatter_clusters(self):
        # Two tight marker clusters
        pixels: list[tuple[int, int]] = []
        for cx, cy in [(20, 20), (80, 80)]:
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    pixels.append((cx + dx, cy + dy))
        centers = _sample_scatter(pixels, max_points=200)
        assert len(centers) == 2
        xs = sorted(c[0] for c in centers)
        ys = sorted(c[1] for c in centers)
        assert xs[0] == pytest.approx(20, abs=1)
        assert xs[1] == pytest.approx(80, abs=1)
        assert ys[0] == pytest.approx(20, abs=1)
        assert ys[1] == pytest.approx(80, abs=1)

    def test_map_coordinate_linear(self):
        assert _map_coordinate(0, 100, 0, 10, "linear") == pytest.approx(0, abs=0.1)
        assert _map_coordinate(99, 100, 0, 10, "linear") == pytest.approx(10, abs=0.1)
        # Inverted y axis: pixel 0 at top maps to data max
        assert _map_coordinate(0, 100, 0, 1, "linear", invert=True) == pytest.approx(1, abs=0.01)
        assert _map_coordinate(99, 100, 0, 1, "linear", invert=True) == pytest.approx(0, abs=0.01)

    def test_map_coordinate_log(self):
        # x in [1, 100], log scale. midpoint (pixel 49/99) should be ~10.
        mid = _map_coordinate(49, 100, 1, 100, "log")
        assert mid == pytest.approx(10, rel=0.1)
        assert _map_coordinate(0, 100, 1, 100, "log") == pytest.approx(1, abs=0.1)
        assert _map_coordinate(99, 100, 1, 100, "log") == pytest.approx(100, abs=0.1)

    def test_detect_chart_type(self):
        assert _detect_chart_type({"figure_type": "bar_chart"}) == "bar_chart"
        assert _detect_chart_type({"figure_type": "scatter"}) == "scatter"
        assert _detect_chart_type({"figure_type": "line_chart"}) == "line_chart"
        assert _detect_chart_type({"caption_summary": "A bar chart of results"}) == "bar_chart"
        assert _detect_chart_type({"caption_summary": "散点图"}) == "scatter"
        assert _detect_chart_type({}) == "line_chart"

    def test_extract_curve_points_log_axis(self, tmp_path: Path):
        # Synthetic line from (1,1) to (100,100) on log-log axes
        path = _make_line_chart(tmp_path)
        axis_info = {
            "x_label": "Epoch",
            "y_label": "Value",
            "x_ticks": [1, 100],
            "y_ticks": [1, 100],
            "x_scale": "log",
            "y_scale": "log",
        }
        points = extract_curve_points(str(path), axis_info)
        assert points is not None
        assert len(points) > 0
        for p in points:
            assert 1 <= p["x"] <= 100
            assert 1 <= p["y"] <= 100

    def test_extract_curve_points_bar_chart(self, tmp_path: Path):
        path = _make_bar_chart(tmp_path)
        axis_info = {
            "x_label": "Category",
            "y_label": "Value",
            "x_ticks": [0, 3],
            "y_ticks": [0, 100],
            "figure_type": "bar_chart",
        }
        points = extract_curve_points(str(path), axis_info)
        assert points is not None
        # Three bars should yield three points (one per bar).
        assert len(points) == 3
        ys = sorted(p["y"] for p in points)
        assert ys[0] > 50  # shortest bar still above mid
        assert ys[-1] <= 100

    def test_extract_curve_points_scatter(self, tmp_path: Path):
        path = _make_scatter_chart(tmp_path)
        axis_info = {
            "x_label": "X",
            "y_label": "Y",
            "x_ticks": [0, 100],
            "y_ticks": [0, 100],
            "figure_type": "scatter",
        }
        points = extract_curve_points(str(path), axis_info)
        assert points is not None
        # Two marker clusters should yield two centroids.
        assert len(points) == 2
        xs = sorted(p["x"] for p in points)
        assert xs[0] < 40
        assert xs[1] > 60


def _pil_to_arr(img: Image.Image) -> np.ndarray:
    import numpy as np
    return np.array(img)
