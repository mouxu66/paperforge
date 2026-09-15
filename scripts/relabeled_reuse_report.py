#!/usr/bin/env python3
"""改标图片复用证据链报告：跨论文 NCC 条带复用（像素）+ VLM 语义标签（蛋白）比对。

把两份论文的图抽出来做两两「改标复用」检测，产出 HTML 审计报告（复用
render_audit_html 的 Finding 表格）。证据链：NCC 像素复用 + 两图各自的目标蛋白
集合无交集 → RELABELED_IMAGE_REUSE。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/relabeled_reuse_report.py \\
        --paper-a fraud_berberine --paper-b kjpp \\
        --out deliverables/relabeled_reuse_berberine_kjpp.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_api.experiment_audit.figure_reuse import (  # noqa: E402
    detect_relabeled_image_reuse,
)
from mock_api.experiment_audit.report import render_audit_html  # noqa: E402
from mock_api.experiment_audit.schemas import assign_finding_ids  # noqa: E402
from mock_api.pdf_parser import _get_uploads_dir  # noqa: E402

_IMG_SUFFIXES = (".png", ".jpg", ".jpeg")


def _figure_files(paper_id: str) -> list[str]:
    d = _get_uploads_dir() / "figures" / paper_id
    if not d.is_dir():
        print(f"警告: 图目录不存在 {d}")
        return []
    return [
        str(p)
        for p in sorted(d.glob("*"))
        if p.suffix.lower() in _IMG_SUFFIXES
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-a", default="fraud_berberine")
    ap.add_argument("--paper-b", default="kjpp")
    ap.add_argument("--ncc-threshold", type=float, default=0.92)
    ap.add_argument("--max-pairs", type=int, default=20)
    ap.add_argument(
        "--out",
        default="deliverables/relabeled_reuse_report.html",
    )
    args = ap.parse_args()

    figs_a = _figure_files(args.paper_a)
    figs_b = _figure_files(args.paper_b)
    if not figs_a or not figs_b:
        print(f"图不足: {args.paper_a}={len(figs_a)} 张, {args.paper_b}={len(figs_b)} 张")
        return 1

    print(
        f"检测 {args.paper_a}({len(figs_a)} 图) vs {args.paper_b}({len(figs_b)} 图) "
        f"的改标图片复用 …"
    )
    findings = detect_relabeled_image_reuse(
        {args.paper_a: figs_a, args.paper_b: figs_b},
        ncc_threshold=args.ncc_threshold,
        max_pairs=args.max_pairs,
    )
    findings = assign_finding_ids(findings)

    audit = SimpleNamespace(
        id="relabeled-reuse",
        paper_id=f"{args.paper_a} <-> {args.paper_b}",
        status="completed",
        source_pdf_hash="-",
        completed_at=None,
        findings=findings,
        checks_run=[
            {
                "check": "RELABELED_IMAGE_REUSE",
                "status": "ok" if findings else "ok (0 findings)",
                "duration_ms": "-",
                "findings": len(findings),
                "reason": (
                    f"NCC 阈值 {args.ncc_threshold}；跨论文条带复用 + VLM 目标蛋白比对"
                ),
            }
        ],
    )
    html = render_audit_html(
        audit,
        paper_title=f"改标图片复用证据链：{args.paper_a} vs {args.paper_b}",
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"改标复用 findings: {len(findings)} 条")
    print(f"报告已落盘: {out}")
    for f in findings:
        print(f"  - {f['finding_id']} {f['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
