"""测试 figure_extraction_gate 门禁脚本。

覆盖场景：
- _count_by_source 正确按 source 统计
- _collect_pdfs 从 --pdfs 和 --pdf-dir 收集路径
- _build_report 生成正确的覆盖率与通过/失败判定
- evaluate_pdf 对纯矢量 PDF 统计 bitmap-only vs bitmap+vector 召回数
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.figure_extraction_gate import (
    _build_report,
    _collect_pdfs,
    _count_by_source,
    evaluate_pdf,
)


def test_count_by_source_groups_bitmap_vector():
    """_count_by_source 按 source 字段聚合 figure 数量。"""
    figures = [
        {"source": "bitmap"},
        {"source": "bitmap"},
        {"source": "vector"},
    ]
    assert _count_by_source(figures) == {"bitmap": 2, "vector": 1}


def test_count_by_source_empty_list():
    """空 figure 列表返回空统计。"""
    assert _count_by_source([]) == {}


def test_collect_pdfs_from_cli_args(tmp_path):
    """_collect_pdfs 支持从 --pdfs 参数收集路径。"""
    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    pdf1.write_bytes(b"x")
    pdf2.write_bytes(b"x")
    args = argparse.Namespace(pdf_dir=None, pdfs=[str(pdf1), str(pdf2)])
    assert _collect_pdfs(args) == [pdf1.resolve(), pdf2.resolve()]


def test_collect_pdfs_from_dir(tmp_path):
    """_collect_pdfs 支持从 --pdf-dir 收集所有 PDF。"""
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    args = argparse.Namespace(pdf_dir=str(tmp_path), pdfs=None)
    result = _collect_pdfs(args)
    assert len(result) == 2
    assert all(p.suffix == ".pdf" for p in result)


def test_build_report_passes_when_coverage_above_threshold():
    """覆盖率高于阈值时报告通过。"""
    results = [
        {
            "filename": "a.pdf",
            "bitmap_only": {"count": 1},
            "bitmap_plus_vector": {"count": 2, "bitmap": 1, "vector": 1},
        }
    ]
    report = _build_report(results, threshold=0.9)
    assert report["summary"]["coverage_ratio"] == 2.0
    assert report["summary"]["pass"] is True


def test_build_report_fails_when_coverage_below_threshold():
    """覆盖率低于阈值时报告不通过。"""
    results = [
        {
            "filename": "a.pdf",
            "bitmap_only": {"count": 10},
            "bitmap_plus_vector": {"count": 5, "bitmap": 5, "vector": 0},
        }
    ]
    report = _build_report(results, threshold=0.9)
    assert report["summary"]["coverage_ratio"] == 0.5
    assert report["summary"]["pass"] is False


def test_evaluate_pdf_counts_vector_rendered_figures(tmp_path):
    """evaluate_pdf 对纯矢量 PDF 应统计出 bitmap-only=0、bitmap+vector>0。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    rect = fitz.Rect(50, 50, 450, 400)
    page.draw_rect(rect, color=(0, 0, 0.8), fill=(0.9, 0.9, 1.0), width=2)
    pdf_bytes = doc.tobytes()
    doc.close()

    pdf_path = tmp_path / "vector.pdf"
    pdf_path.write_bytes(pdf_bytes)

    result = evaluate_pdf(pdf_path, paper_id_prefix="gate", idx=1)

    assert result["bitmap_only"]["count"] == 0
    assert result["bitmap_plus_vector"]["count"] >= 1
    assert result["bitmap_plus_vector"]["vector"] >= 1


def test_evaluate_pdf_counts_bitmap_and_vector(tmp_path):
    """同时包含嵌入位图和矢量图的 PDF 应被分别统计。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    # 位图：用 pixmap 生成一张红色小图并插入
    pix = fitz.Pixmap(fitz.Colorspace(fitz.CS_RGB), fitz.Rect(0, 0, 100, 100))
    pix.clear_with(0xFF0000)
    page.insert_image(fitz.Rect(50, 50, 150, 150), pixmap=pix)
    # 矢量：再画一个矩形
    page.draw_rect(fitz.Rect(200, 50, 450, 400), color=(0, 0, 0.8), fill=(0.9, 0.9, 1.0), width=2)
    pdf_bytes = doc.tobytes()
    doc.close()

    pdf_path = tmp_path / "mixed.pdf"
    pdf_path.write_bytes(pdf_bytes)

    result = evaluate_pdf(pdf_path, paper_id_prefix="gate", idx=2)

    assert result["bitmap_plus_vector"]["bitmap"] >= 1
    assert result["bitmap_plus_vector"]["vector"] >= 1
