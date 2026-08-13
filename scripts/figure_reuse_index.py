"""跨论文图片复用召回索引（离线，pHash 粗筛）。

扫描 uploads/figures/<paper_id>/ 下所有论文的 figure，做跨论文两两
pHash 比对，命中候选对落 CSV 供人工复核（确认是否图剽窃/复用）。

用法：
    .venv/Scripts/python.exe scripts/figure_reuse_index.py --limit 20
    .venv/Scripts/python.exe scripts/figure_reuse_index.py --threshold 15 \
        --out deliverables/cross_paper_reuse_candidates.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.experiment_audit.figure_reuse import (  # noqa: E402
    DEFAULT_PHASH_RECALL_THRESHOLD,
    detect_cross_paper_reuse,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figures-dir", default=str(ROOT / "uploads" / "figures"))
    ap.add_argument("--limit", type=int, default=0, help="最多扫描的论文数（0=全部）")
    ap.add_argument("--threshold", type=int, default=DEFAULT_PHASH_RECALL_THRESHOLD)
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "cross_paper_reuse_candidates.csv"))
    args = ap.parse_args()

    base = Path(args.figures_dir)
    if not base.is_dir():
        print(f"目录不存在: {base}")
        return 1

    paper_dirs = sorted(p for p in base.iterdir() if p.is_dir())
    if args.limit:
        paper_dirs = paper_dirs[: args.limit]

    figures_by_paper: dict[str, list[str]] = {}
    for d in paper_dirs:
        imgs = [str(p) for p in sorted(d.glob("*")) if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
        if imgs:
            figures_by_paper[d.name] = imgs

    print(f"扫描 {len(figures_by_paper)} 篇论文 / {sum(len(v) for v in figures_by_paper.values())} 张图 …")
    cands = detect_cross_paper_reuse(figures_by_paper, phash_threshold=args.threshold)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["paper_a", "fig_a", "paper_b", "fig_b", "dist"]
        )
        w.writeheader()
        w.writerows(cands)

    print(f"跨论文复用候选: {len(cands)} 对（阈值 {args.threshold}）")
    print(f"已落盘: {out}")
    print("注意: pHash 召回较敏感（空白/同构图表会误召），候选对需人工复核或 SIFT 验证。")
    for c in cands[:10]:
        print(f"  {c['paper_a']}/{Path(c['fig_a']).name} vs "
              f"{c['paper_b']}/{Path(c['fig_b']).name} (dist={c['dist']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
