#!/usr/bin/env python3
"""把指定 PDF 每页渲染成 PNG（低 dpi，仅供肉眼核对图召回用）。"""
import sys
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parents[1]
PDF_DIR = REPO / "scripts" / "gate_pdfs"
OUT = REPO / "scripts" / "_verify"
OUT.mkdir(parents=True, exist_ok=True)
DPI = 60

stems = ["1501.01239", "0910.5761", "1502.02367", "1612.08810", "1702.02171"]


def render(stem: str) -> int:
    src = PDF_DIR / f"{stem}.pdf"
    d = OUT / stem
    d.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(src)
    n = 0
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=DPI)
        pix.save(d / f"page_{i:02d}.png")
        n += 1
    print(f"{stem}: {n} pages -> {d}")
    return n


if __name__ == "__main__":
    for s in stems:
        render(s)
    print("DONE")
