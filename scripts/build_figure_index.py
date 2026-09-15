#!/usr/bin/env python3
"""全库图片复用粗筛索引：构建 pHash 索引 + 全库跨论文候选清单 CSV。

两级管线（与 figure_index 模块一致）：
1. 召回：全库 pHash 索引（原图/H翻转/V翻转 三假设，汉明距离 < 阈值）；
2. 可选 --verify-sift：候选对再做 SIFT/RANSAC 几何验证（含镜像假设）。

索引 JSON 落盘供复用（figure_index.load）；候选 CSV 供人工复核。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/build_figure_index.py
    .venv/Scripts/python.exe scripts/build_figure_index.py --verify-sift \\
        --out deliverables/cross_paper_figure_candidates.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.experiment_audit.figure_index import (  # noqa: E402
    DEFAULT_PHASH_RECALL_THRESHOLD,
    build_index,
)

_IMG_SUFFIXES = (".png", ".jpg", ".jpeg")
_FIELDS = ("paper_a", "fig_a", "paper_b", "fig_b", "dist")
_FIELDS_SIFT = ("paper_a", "fig_a", "paper_b", "fig_b", "dist", "sift_verified")


def _image_files(d: Path) -> list[str]:
    return [
        str(p) for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in _IMG_SUFFIXES
    ]


def _dedupe_copies(figures_by_paper: dict[str, list[str]]) -> dict[str, list[str]]:
    """按内容 SHA-256 去重跨论文字节相同的整图副本（首见保留）。"""
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    removed = 0
    for paper, paths in figures_by_paper.items():
        kept: list[str] = []
        for p in paths:
            try:
                digest = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            except OSError:
                kept.append(p)
                continue
            if digest in seen:
                removed += 1
                continue
            seen.add(digest)
            kept.append(p)
        if kept:
            out[paper] = kept
    if removed:
        print(f"去重：跨论文字节相同的整图副本 {removed} 个（首见保留）")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="全库图 pHash 粗筛索引构建 + 跨论文候选清单")
    ap.add_argument("--figures-dir", default=str(ROOT / "uploads" / "figures"))
    ap.add_argument("--threshold", type=int, default=DEFAULT_PHASH_RECALL_THRESHOLD)
    ap.add_argument(
        "--dedupe-copies",
        action="store_true",
        default=True,
        help="按内容去重字节相同的整图副本（默认开）",
    )
    ap.add_argument("--verify-sift", action="store_true", help="候选对再做 SIFT/RANSAC 验证")
    ap.add_argument("--index-out", default=str(ROOT / "deliverables" / "figure_index.json"))
    ap.add_argument(
        "--out", default=str(ROOT / "deliverables" / "cross_paper_figure_candidates.csv")
    )
    args = ap.parse_args()

    base = Path(args.figures_dir)
    if not base.is_dir():
        print(f"目录不存在: {base}")
        return 1
    figures_by_paper: dict[str, list[str]] = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        imgs = _image_files(d)
        if imgs:
            figures_by_paper[d.name] = imgs
    if not figures_by_paper:
        print(f"未在 {base} 找到任何论文图目录")
        return 1
    if args.dedupe_copies:
        figures_by_paper = _dedupe_copies(figures_by_paper)

    n_figs = sum(len(v) for v in figures_by_paper.values())
    print(
        f"扫描 {len(figures_by_paper)} 篇论文 / {n_figs} 张图，构建 pHash 索引（阈值 {args.threshold}）…"
    )
    index = build_index(figures_by_paper, threshold=args.threshold)
    print(f"索引条目: {len(index.entries)}")

    index_out = Path(args.index_out)
    index_out.parent.mkdir(parents=True, exist_ok=True)
    index.save(index_out)
    print(f"索引已落盘: {index_out}")

    cands: list[dict] = []
    for paper_id in figures_by_paper:
        for dist, me, other in index.paper_candidates(paper_id):
            cands.append(
                {
                    "paper_a": me.paper_id,
                    "fig_a": me.path,
                    "paper_b": other.paper_id,
                    "fig_b": other.path,
                    "dist": dist,
                }
            )
    # 全库逐篇会重复产出同一对（i vs j 和 j vs i），去重
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for c in sorted(cands, key=lambda c: (c["dist"], c["paper_a"], c["fig_a"])):
        key = tuple(sorted((c["fig_a"], c["fig_b"])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    cands = unique

    if args.verify_sift:
        try:
            import cv2  # noqa: F401
            from mock_api.experiment_audit.figure_reuse import _verify_cross_paper_with_sift

            print(f"SIFT 验证 {len(cands)} 对候选 …")
            cands = _verify_cross_paper_with_sift(cv2, cands, min_ransac_inliers=5)
        except ImportError:
            print("cv2 未安装，跳过 SIFT 验证")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = _FIELDS_SIFT if args.verify_sift else _FIELDS
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for c in cands:
            w.writerow({k: c.get(k, "") for k in fields})

    print(f"跨论文图片复用候选总数: {len(cands)}（pHash 阈值 {args.threshold}）")
    print(f"已落盘: {out}")
    for c in cands[:10]:
        print(
            f"  {c['paper_a']}/{Path(c['fig_a']).name} vs {c['paper_b']}/{Path(c['fig_b']).name} "
            f"(dist={c['dist']}{', sift=' + str(c['sift_verified']) if args.verify_sift else ''})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
