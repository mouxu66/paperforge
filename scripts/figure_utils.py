#!/usr/bin/env python3
"""Shared utilities for figure/PDF generation and Qwen requests.

Used by:
- scripts/pipeline_figure_understanding.py
"""

from __future__ import annotations

import io

import numpy as np

from mock_api.llm.figure_qwen import ask_qwen

__all__ = ["ask_qwen", "make_experimental_chart_bytes", "make_pdf_with_figure"]

def make_experimental_chart_bytes(
    title: str = "Figure 1: Model Accuracy on Test Set",
    best_accuracy: float = 87.5,
) -> bytes:
    """Generate a PNG chart similar to a paper's experimental figure.

    The default labels and annotations include common OCR keywords
    (accuracy, epoch, baseline, proposed) so benchmark tests can verify
    figure OCR quality.

    matplotlib 改为惰性导入：本模块被 scripts/pipeline_figure_understanding.py
    及测试在导入期引用，若顶层硬 import 会在未安装 matplotlib 的环境（如精简
    CI）直接 ImportError 导致整模块无法收集。仅在此绘图函数内按需加载。
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    """Generate a PNG chart similar to a paper's experimental figure.

    The default labels and annotations include common OCR keywords
    (accuracy, epoch, baseline, proposed) so benchmark tests can verify
    figure OCR quality.
    """
    np.random.seed(42)
    epochs = np.arange(1, 51)
    baseline = 0.72 + 0.15 * np.log(epochs) / np.log(50) + np.random.normal(0, 0.01, 50)
    proposed = 0.75 + 0.12 * np.log(epochs) / np.log(50) + np.random.normal(0, 0.008, 50)

    fig, ax = plt.subplots(figsize=(8, 5.5), dpi=200)
    ax.plot(epochs, baseline * 100, "b--o", markersize=3, label="Baseline", markevery=5)
    ax.plot(epochs, proposed * 100, "r-s", markersize=3, label="Proposed", markevery=5)

    ax.set_xlabel("Epoch", fontsize=12)
    ax.set_ylabel("Accuracy (%)", fontsize=12)
    ax.set_title(title, fontsize=14)
    ax.legend(loc="lower right", frameon=True)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.set_xlim(0, 55)
    ax.set_ylim(70, 92)

    ax.annotate(
        f"Best: {best_accuracy}%",
        xy=(50, proposed[-1] * 100),
        xytext=(42, proposed[-1] * 100 + 2),
        arrowprops={"arrowstyle": "->", "color": "red"},
        fontsize=11,
        color="red",
    )
    ax.annotate(
        f"{baseline[-1] * 100:.1f}%",
        xy=(50, baseline[-1] * 100),
        xytext=(45, baseline[-1] * 100 + 2),
        fontsize=11,
        color="blue",
    )

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def make_pdf_with_figure(
    img_bytes: bytes,
    title: str = "",
    paragraphs: list[str] | None = None,
    caption: str = "",
    img_rect: tuple[float, float, float, float] = (72, 130, 540, 520),
) -> bytes:
    """Embed a PNG image into a simple PDF page with optional text blocks."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 72
    if title:
        page.insert_text((72, y), title, fontsize=14)
        y += 24
    for paragraph in paragraphs or []:
        page.insert_text((72, y), paragraph, fontsize=11)
        y += 14
    page.insert_image(fitz.Rect(*img_rect), stream=img_bytes)
    if caption:
        page.insert_text((72, img_rect[3] + 20), caption, fontsize=11)
    out = io.BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()
