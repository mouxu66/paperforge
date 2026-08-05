#!/usr/bin/env python3
"""M0 矢量图抽取门禁（召回率核验骨架）。

目标：对 10 篇样本 PDF 验证「矢量渲染 + 位图」抽取能覆盖论文里作者标注的图，
作为 M0 接入 DEPTH 生产前的验收门禁（铁律：新来源接入前跑 10 篇、图表召回率 >=90%）。

重要：本脚本给出的 caption_proxy_recall 是「自动代理指标」，不是 ground-truth 召回率。
- 真·召回率需要人工（或带标注 gold）核验：打开每篇 PDF，数真实图数，比对抽出的 PNG。
- 因此脚本默认【不自动判过/不过】，只产出可审计产物 + 代理指标，最终 pass/fail 由人核验后裁定。

代理指标定义：
  ground truth(代理) = 论文文本里出现的 Figure/Fig N 标号集合（作者显式标注的图）
  caption_proxy_recall = 成功关联到抽出 figure 的标号数 / 总标号数
  局限：无图注的图（如纯装饰/无标号图）不在代理 ground truth 内 -> 代理可能低估真实召回。

与旧 figure_extraction_gate.py 的区别：
  旧脚本 coverage_ratio = total_with_vector / total_bitmap_only，测的是「矢量相对位图的增量比」，
  对纯矢量论文（位图=0）会直接满分，完全不对照真实图数 -> 不能当召回率用。本脚本修正此点。

用法:
  python scripts/figure_recall_gate.py --pdf-dir ./sample_pdfs --output recall_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本可从仓库根运行（mock_api 包可导入）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mock_api.pdf_parser import extract_figures_for_paper, _extract_captions


def _caption_labels(page) -> list[tuple[int, tuple[float, float, float, float]]]:
    """返回该页所有 Figure/Fig N 标号及其 bbox（来自 _extract_captions）。"""
    out = []
    for bbox, _text, num in _extract_captions(page):
        if num is not None:
            out.append((num, bbox))
    return out


def _associate(figs_nearby: list[dict], captions: list, page_h: float) -> set[int]:
    """对每个 caption 标号，找最近（中心曼哈顿距离 < 0.6*页高）的抽出 figure 算匹配。

    figs_nearby 应已包含同页 + 相邻页的抽出 figure（论文常把图放在 caption 上一页/下一页）。
    """
    matched: set[int] = set()
    for num, cbbox in captions:
        cx0, cy0, cx1, cy1 = cbbox
        ccx, ccy = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        best = None
        best_d = page_h * 0.6
        for fig in figs_nearby:
            fx0, fy0, fx1, fy1 = fig["bbox"]
            fcx, fcy = (fx0 + fx1) / 2, (fy0 + fy1) / 2
            d = abs(fcy - ccy) + abs(fcx - ccx)
            if d < best_d:
                best_d, best = d, fig
        if best is not None:
            matched.add(num)
    return matched


def evaluate_pdf(pdf_path: Path, paper_id: str) -> dict:
    import fitz

    content = pdf_path.read_bytes()
    # 只抽矢量(默认开) + 位图，skip_ocr 省显存/时间
    figs = extract_figures_for_paper(content, paper_id, skip_ocr=True, vector_render_enabled=True)
    doc = fitz.open(stream=content, filetype="pdf")

    # 按页分组，便于跨页（±1 页）关联 caption 与 figure
    figs_by_page: dict[int, list[dict]] = {}
    for f in figs:
        figs_by_page.setdefault(f["page"], []).append(f)

    total_captions = 0
    matched_captions = 0
    for page_no, page in enumerate(doc, start=1):
        caps = _caption_labels(page)
        total_captions += len(caps)
        # 关联范围：本页 + 上一页 + 下一页（图常跨页摆放）
        nearby = (
            figs_by_page.get(page_no - 1, [])
            + figs_by_page.get(page_no, [])
            + figs_by_page.get(page_no + 1, [])
        )
        matched = _associate(nearby, caps, page.rect.height)
        matched_captions += len(matched)

    proxy_recall = round(matched_captions / total_captions, 4) if total_captions else None
    by_source: dict[str, int] = {}
    for f in figs:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1

    return {
        "filename": pdf_path.name,
        "pages": len(doc),
        "extracted_total": len(figs),
        "by_source": by_source,
        "caption_labels_total": total_captions,
        "caption_matched": matched_captions,
        "caption_proxy_recall": proxy_recall,
        "note": "proxy 仅覆盖有标号的图；无标号图/装饰图不在内，最终需人工核验",
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="M0 矢量图抽取召回率门禁（骨架，不自动判过/不过）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--pdfs", nargs="+", help="一个或多个 PDF 路径")
    ap.add_argument("--pdf-dir", help="含 PDF 的目录")
    ap.add_argument("--output", "-o", default="-", help="报告输出路径，'-' 表示 stdout")
    ap.add_argument("--prefix", default="recall_gate", help="临时 paper_id 前缀")
    ap.add_argument(
        "--min-per-paper",
        type=int,
        default=1,
        help="每篇至少抽到的图数（低于则告警，但不自动判不过）",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    paths: list[Path] = []
    if args.pdf_dir:
        paths += sorted(Path(args.pdf_dir).glob("*.pdf"))
    if args.pdfs:
        paths += [Path(p) for p in args.pdfs]
    paths = list(dict.fromkeys(p.resolve() for p in paths))
    if not paths:
        print("错误：未找到任何 PDF 文件。", file=sys.stderr)
        return 1

    results = [evaluate_pdf(p, f"{args.prefix}_{i:03d}") for i, p in enumerate(paths, start=1)]

    # --min-per-paper 告警（不自动判过，仅提示）
    for r in results:
        if r["extracted_total"] < args.min_per_paper:
            r["low_figure_warn"] = True
            print(
                f"[WARN] {r['filename']} 仅抽到 {r['extracted_total']} 张图（< --min-per-paper {args.min_per_paper}）",
                file=sys.stderr,
            )

    report = {
        "sample_size": len(results),
        "discipline": "M0 接入前需 10 篇、图表召回率 >=90%；本脚本只出代理指标+产物，最终 pass/fail 须人工核验",
        "per_paper": results,
    }
    txt = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(txt)
    else:
        Path(args.output).write_text(txt, encoding="utf-8")
        print(f"报告已写: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
