#!/usr/bin/env python3
"""Fetch an arXiv paper and run DEPTH v4.2 review in deep/speed mode(s).

Usage:
    python scripts/arxiv_depth_test.py [--keyword "machine learning"] [--compute-mode both]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mock_api.arxiv_crawler import search_arxiv  # noqa: E402
from mock_api.config import set_compute_mode  # noqa: E402
from mock_api.database import init_db  # noqa: E402


def download_pdf(arxiv_id: str) -> bytes:
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    print(f"[下载 PDF] {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def extract_text(pdf_bytes: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("需要 PyMuPDF (fitz) 来提取 PDF 文本") from exc

    doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
    parts: list[str] = []
    for page in doc:
        parts.append(page.get_text())
    return "\n".join(parts)


def save_paper_to_db(arxiv_id: str, metadata: dict, full_text: str) -> None:
    """Save a minimal Paper record so DEPTH can reference it by paper_id."""
    from mock_api.database import SessionLocal
    from mock_api.models import Paper

    db = SessionLocal()
    try:
        existing = db.query(Paper).filter(Paper.id == arxiv_id).first()
        if existing:
            print(f"[DB] 论文 {arxiv_id} 已存在，跳过写入")
            return
        paper = Paper(
            id=arxiv_id,
            title=metadata["title"],
            authors=list(metadata.get("authors", []) or []),
            year=metadata.get("year") or 0,
            abstract=metadata.get("abstract", ""),
            full_text=full_text,
            source="arxiv",
            category="arxiv",
        )
        db.add(paper)
        db.commit()
        print(f"[DB] 已写入论文 {arxiv_id}")
    finally:
        db.close()


async def run_review(
    arxiv_id: str,
    full_text: str,
    title: str,
    abstract: str,
    compute_mode: str,
) -> dict:
    from mock_api.config import get_compute_mode
    from mock_api.depth_eval_v4 import DepthReviewer

    # DepthReviewer reads the global compute mode config at init time, so switch
    # the global mode before instantiating the reviewer. Restore the original
    # mode afterwards to avoid leaking global state.
    original_mode = get_compute_mode()
    try:
        set_compute_mode(compute_mode)
        reviewer = DepthReviewer(compute_mode=compute_mode)
        result = await reviewer.review_async_dag(
            paper_id=arxiv_id,
            title=title,
            full_text=full_text,
            abstract=abstract,
        )
        return result.model_dump()
    finally:
        set_compute_mode(original_mode)


def print_scores(result: dict, mode: str = "") -> None:
    label = f"[{mode}]" if mode else ""
    print(f"\n=== DEPTH v4.2 评分结果 {label} ===")
    print(f"paper_id:          {result.get('paper_id')}")
    print(f"title:             {result.get('title')}")
    print(f"has_substance:     {result.get('has_substance')}")
    print(f"expectation:       {result.get('expectation')}")
    print(f"paper_type:        {result.get('paper_type')} (secondary: {result.get('secondary_type')})")
    print(f"novelty_score:     {result.get('novelty_score')}")
    print(f"rigor_score:       {result.get('rigor_score')}")
    print(f"influence_score:   {result.get('influence_score')}")
    print(f"reproducibility:   {result.get('reproducibility_score')}")
    print(f"hotspot_alignment: {result.get('hotspot_alignment_score')}")
    print(f"figure_consistency:{result.get('figure_consistency_score')}")
    print(f"base_score:        {result.get('base_score')}")
    print(f"calibrated_score:  {result.get('calibrated_score')}")
    print(f"delta:             {result.get('delta')}")
    print(f"llm_verdict:       {result.get('llm_verdict')}")
    print(f"final_verdict:     {result.get('final_verdict')}")
    print(f"figure_coverage:   {result.get('figure_coverage')}")
    print(f"override_reason:   {result.get('override_reason')}")
    print("critique_points:")
    for cp in result.get("critique_points", []) or []:
        print(f"  - [{cp.get('severity')}] {cp.get('point')}")


def compare_results(results: dict[str, dict]) -> None:
    """Print a side-by-side comparison of deep and speed mode results."""
    print("\n" + "=" * 70)
    print(" DEPTH v4.2 deep vs speed 对比")
    print("=" * 70)

    deep = results.get("deep", {})
    speed = results.get("speed", {})

    def _f(name: str, key: str) -> None:
        d_val = deep.get(key)
        s_val = speed.get(key)
        if isinstance(d_val, float) and isinstance(s_val, float):
            diff = s_val - d_val
            print(f"{name:22s}  deep={d_val:.4f}  speed={s_val:.4f}  diff={diff:+.4f}")
        else:
            print(f"{name:22s}  deep={d_val!r}  speed={s_val!r}")

    _f("novelty_score", "novelty_score")
    _f("rigor_score", "rigor_score")
    _f("influence_score", "influence_score")
    _f("reproducibility_score", "reproducibility_score")
    _f("hotspot_alignment_score", "hotspot_alignment_score")
    _f("figure_consistency_score", "figure_consistency_score")
    _f("base_score", "base_score")
    _f("calibrated_score", "calibrated_score")
    _f("delta", "delta")

    print(f"\n{'final_verdict':22s}  deep={deep.get('final_verdict')!r}  speed={speed.get('final_verdict')!r}")
    print(f"{'figure_coverage':22s}  deep={deep.get('figure_coverage')!r}  speed={speed.get('figure_coverage')!r}")

    # 简单一致性评估
    d_verdict = deep.get("final_verdict")
    s_verdict = speed.get("final_verdict")
    d_score = deep.get("calibrated_score", 0.0)
    s_score = speed.get("calibrated_score", 0.0)
    score_diff = abs(d_score - s_score)

    print("\n一致性评估:")
    if d_verdict == s_verdict and score_diff < 0.05:
        print("  [OK] deep/speed 高度一致（verdict 相同，calibrated_score 差 < 0.05）")
    elif d_verdict == s_verdict and score_diff < 0.10:
        print("  [WARN] verdict 相同，但 calibrated_score 差异较大（>= 0.05），建议检查")
    elif score_diff < 0.10:
        print("  [WARN] calibrated_score 接近，但 verdict 不同，建议检查边界阈值")
    else:
        print("  [ERROR] deep/speed 评分差异过大（score diff >= 0.10），需要排查原因")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch an arXiv paper and run DEPTH v4.2")
    parser.add_argument("--keyword", default="machine learning", help="arXiv search keyword")
    parser.add_argument("--max-results", type=int, default=10, help="papers to search")
    parser.add_argument(
        "--compute-mode",
        default="both",
        choices=["deep", "speed", "both"],
        help="DEPTH compute mode: deep, speed, or both (default: both)",
    )
    args = parser.parse_args()

    init_db()

    print(f"[arXiv] 搜索关键词: {args.keyword}")
    papers = search_arxiv(args.keyword, max_results=args.max_results)
    if not papers:
        print("未找到论文")
        return 1

    for idx, p in enumerate(papers, 1):
        print(f"  {idx}. {p['id']} - {p['title']}")

    selected = papers[0]
    arxiv_id = selected["id"]
    print(f"\n[选中] {arxiv_id}: {selected['title']}")

    pdf_bytes = download_pdf(arxiv_id)
    print(f"[PDF] 大小 {len(pdf_bytes)} bytes")

    full_text = extract_text(pdf_bytes)
    print(f"[文本] 提取 {len(full_text)} 字符")

    save_paper_to_db(arxiv_id, selected, full_text)

    modes = ["deep", "speed"] if args.compute_mode == "both" else [args.compute_mode]
    results: dict[str, dict] = {}
    timings: dict[str, float] = {}

    for mode in modes:
        print(f"\n[DEPTH v4.2] 开始 {mode} 模式审稿（可能需要几分钟）...")
        start = time.time()
        result = asyncio.run(
            run_review(arxiv_id, full_text, selected["title"], selected["abstract"], mode)
        )
        elapsed = time.time() - start
        timings[mode] = elapsed
        results[mode] = result
        print(f"[DEPTH v4.2] {mode} 审稿完成，耗时 {elapsed:.1f}s")
        print_scores(result, mode=mode)

        # 保存结果供后续分析
        output_path = Path("scripts") / f"depth_review_{arxiv_id}_{mode}.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[输出] {mode} 完整结果已保存到 {output_path}")

    if len(results) == 2:
        compare_results(results)
        print("\n耗时对比:")
        for mode in modes:
            print(f"  {mode}: {timings[mode]:.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
