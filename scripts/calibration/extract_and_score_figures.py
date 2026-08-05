#!/usr/bin/env python3
"""Extract figures from reviewed arXiv papers and collect their OCR text.

This is a limited pipeline: it can only OCR embedded bitmap XObjects from PDFs.
Vector matplotlib figures will not be captured.  The extracted OCR text is saved
so it can be used for later re-scoring.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from mock_api.pdf_parser import extract_figures_for_paper  # noqa: E402


def download_pdf(arxiv_id: str, cache_dir: Path) -> bytes:
    pdf_path = cache_dir / f"{arxiv_id}.pdf"
    if pdf_path.exists():
        print(f"[使用缓存 PDF] {pdf_path}")
        return pdf_path.read_bytes()
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    print(f"[下载 PDF] {url}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    pdf_bytes = resp.content
    pdf_path.write_bytes(pdf_bytes)
    print(f"[缓存 PDF] {pdf_path} ({len(pdf_bytes)} bytes)")
    return pdf_bytes


def describe_figures(
    paper_id: str, with_qwen: bool = False, qwen_url: str = "http://127.0.0.1:8080"
) -> list[dict]:
    """Extract figures for one paper and optionally describe them with Qwen."""
    cache_dir = Path("scripts") / ".arxiv_cache"
    cache_dir.mkdir(exist_ok=True)

    pdf_bytes = download_pdf(paper_id, cache_dir)
    print(f"[{paper_id}] 开始抽取 figure...")
    # extract_figures_for_paper 内部不再调度 VRAM，调用方按需管理；
    # 此脚本仅做 OCR 抽取，不再额外加 bracket。
    figs = extract_figures_for_paper(pdf_bytes, paper_id)
    print(f"[{paper_id}] 抽到 {len(figs)} 张 figure")

    if with_qwen:
        from scripts.figure_utils import ask_qwen
    else:
        ask_qwen = None  # type: ignore[assignment]

    results = []
    for fig in figs:
        ocr_text = fig.get("ocr_text", "") or ""
        description: str | None = None
        if with_qwen:
            if ocr_text.strip():
                try:
                    description = ask_qwen(ocr_text, base_url=qwen_url, timeout=120)
                except Exception as exc:
                    description = f"[Qwen 解读失败: {exc}]"
            else:
                description = "[无 OCR 文字]"
        results.append(
            {
                "page": fig.get("page"),
                "figure_index": fig.get("figure_index"),
                "figure_path": fig.get("figure_path"),
                "ocr_text": ocr_text,
                "qwen_description": description,
            }
        )
        preview = (description or ocr_text or "")[:100].replace("\n", " ")
        print(f"  page {fig.get('page')} fig {fig.get('figure_index')}: {preview}...")
    return results


def load_existing_results(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="抽取论文 figure 并收集 OCR 文本")
    parser.add_argument(
        "--with-qwen",
        action="store_true",
        help="对每个 figure 调用本地 Qwen 生成描述（慢）",
    )
    parser.add_argument(
        "--paper-id",
        type=str,
        default=None,
        help="只处理指定 arXiv ID（默认处理全部 7 篇）",
    )
    parser.add_argument(
        "--qwen-url",
        type=str,
        default="http://127.0.0.1:8080",
        help="本地 Qwen 服务 URL（默认 http://127.0.0.1:8080）",
    )
    args = parser.parse_args()

    output_path = Path("scripts") / "extracted_figures_descriptions.json"
    all_figures = load_existing_results(output_path)

    paper_ids = [args.paper_id] if args.paper_id else [
        "2607.20365",
        "2607.20382",
        "2607.20416",
        "2607.20418",
        "2607.20420",
        "2607.20422",
        "2607.20424",
    ]

    for paper_id in paper_ids:
        if paper_id in all_figures:
            print(f"[{paper_id}] 已存在，跳过")
            continue
        print(f"\n{'=' * 60}\n处理论文 {paper_id}\n{'=' * 60}")
        try:
            all_figures[paper_id] = describe_figures(
                paper_id, with_qwen=args.with_qwen, qwen_url=args.qwen_url
            )
        except Exception as exc:
            print(f"[{paper_id}] 处理失败: {exc}")
            all_figures[paper_id] = []
        # 每篇论文处理完后都保存，避免超时丢失进度
        output_path.write_text(
            json.dumps(all_figures, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[保存] {output_path}")

    print(f"\n[输出] figure OCR 已保存到 {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
