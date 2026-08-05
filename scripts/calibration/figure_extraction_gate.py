#!/usr/bin/env python3
"""Figure 提取门禁脚本：统计样本 PDF 的位图/矢量 figure 召回数并输出覆盖率报告。

用法示例：
    python scripts/figure_extraction_gate.py \\
        --pdfs sample1.pdf sample2.pdf ... sample10.pdf \\
        --output coverage_report.json

    python scripts/figure_extraction_gate.py \\
        --pdf-dir ./sample_pdfs \\
        --output coverage_report.json \\
        --threshold 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _collect_pdfs(args: argparse.Namespace) -> list[Path]:
    """从命令行参数收集 PDF 文件路径。"""
    paths: list[Path] = []
    if args.pdf_dir:
        dir_path = Path(args.pdf_dir)
        if not dir_path.is_dir():
            raise SystemExit(f"--pdf-dir 不是有效目录: {args.pdf_dir}")
        paths.extend(sorted(dir_path.glob("*.pdf")))
    if args.pdfs:
        paths.extend(Path(p) for p in args.pdfs)

    # 去重并保持顺序
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _count_by_source(figures: list[dict]) -> dict[str, int]:
    """按 source 统计 figure 数量。"""
    counts: dict[str, int] = defaultdict(int)
    for fig in figures:
        source = fig.get("source", "unknown")
        counts[source] += 1
    return dict(counts)


def _extract_for_mode(pdf_path: Path, paper_id: str, *, vector_enabled: bool) -> list[dict]:
    """以指定模式抽取 figure，默认跳过 OCR 以节省显存/时间。"""
    from mock_api.pdf_parser import extract_figures_for_paper

    content = pdf_path.read_bytes()
    return extract_figures_for_paper(
        content,
        paper_id,
        skip_ocr=True,
        vector_render_enabled=vector_enabled,
    )


def evaluate_pdf(pdf_path: Path, paper_id_prefix: str, idx: int) -> dict:
    """对单篇 PDF 分别跑 bitmap-only 和 bitmap+vector 两种模式，返回统计。"""
    paper_id_base = f"{paper_id_prefix}_{idx:03d}"
    bitmap_figs = _extract_for_mode(pdf_path, f"{paper_id_base}_bitmap", vector_enabled=False)
    all_figs = _extract_for_mode(pdf_path, f"{paper_id_base}_both", vector_enabled=True)

    bitmap_counts = _count_by_source(bitmap_figs)
    all_counts = _count_by_source(all_figs)

    bitmap_total = bitmap_counts.get("bitmap", 0)
    all_total = sum(all_counts.values())
    vector_total = all_counts.get("vector", 0)

    return {
        "filename": pdf_path.name,
        "paper_id_base": paper_id_base,
        "pages": _pdf_page_count(pdf_path),
        "bitmap_only": {
            "count": bitmap_total,
        },
        "bitmap_plus_vector": {
            "count": all_total,
            "bitmap": all_counts.get("bitmap", 0),
            "vector": vector_total,
        },
        "vector_gain": vector_total,
        "coverage_ratio": all_total / max(bitmap_total, 1),
    }


def _pdf_page_count(pdf_path: Path) -> int | None:
    """返回 PDF 页数，失败返回 None。"""
    try:
        import fitz  # noqa: F401
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
        return len(doc)
    except Exception:
        return None


def _build_report(results: list[dict], threshold: float) -> dict:
    """汇总单篇统计并生成门禁报告。"""
    total_bitmap_only = sum(r["bitmap_only"]["count"] for r in results)
    total_with_vector = sum(r["bitmap_plus_vector"]["count"] for r in results)
    total_vector = sum(r["bitmap_plus_vector"]["vector"] for r in results)
    total_files = len(results)

    if total_bitmap_only > 0:
        coverage = total_with_vector / total_bitmap_only
    else:
        # 没有位图时，只要有矢量图也算覆盖
        coverage = 1.0 if total_with_vector > 0 else 0.0

    pass_gate = coverage >= threshold

    return {
        "threshold": threshold,
        "summary": {
            "total_files": total_files,
            "total_bitmap_only": total_bitmap_only,
            "total_with_vector": total_with_vector,
            "total_vector": total_vector,
            "coverage_ratio": round(coverage, 4),
            "avg_bitmap_per_file": round(total_bitmap_only / max(total_files, 1), 4),
            "avg_vector_per_file": round(total_vector / max(total_files, 1), 4),
            "pass": pass_gate,
        },
        "per_paper": results,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计样本 PDF 的位图/矢量 figure 召回数并输出覆盖率报告。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\n门禁判定逻辑：
  coverage_ratio = total_with_vector / total_bitmap_only
  当 coverage_ratio >= threshold 时通过门禁（默认 0.9，即 90%）。
""",
    )
    parser.add_argument(
        "--pdfs",
        nargs="+",
        help="一个或多个 PDF 文件路径。",
    )
    parser.add_argument(
        "--pdf-dir",
        help="包含 PDF 文件的目录。",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="报告输出路径，默认 '-' 表示输出到 stdout。",
    )
    parser.add_argument(
        "--paper-id-prefix",
        default="gate",
        help="临时 paper_id 前缀（默认 gate）。",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="覆盖率通过阈值（默认 0.9）。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pdf_paths = _collect_pdfs(args)
    if not pdf_paths:
        print("错误：未找到任何 PDF 文件。", file=sys.stderr)
        return 1

    results: list[dict] = []
    for idx, pdf_path in enumerate(pdf_paths, start=1):
        try:
            result = evaluate_pdf(pdf_path, args.paper_id_prefix, idx)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"处理 {pdf_path.name} 时出错: {exc}", file=sys.stderr)
            results.append(
                {
                    "filename": pdf_path.name,
                    "error": str(exc),
                    "bitmap_only": {"count": 0},
                    "bitmap_plus_vector": {"count": 0, "bitmap": 0, "vector": 0},
                }
            )

    report = _build_report(results, args.threshold)
    report_json = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output == "-":
        print(report_json)
    else:
        output_path = Path(args.output)
        output_path.write_text(report_json, encoding="utf-8")
        print(f"报告已保存至: {output_path}")

    return 0 if report["summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
